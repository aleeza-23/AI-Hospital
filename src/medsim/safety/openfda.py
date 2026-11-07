"""G2: openFDA drug-label lookup for the Safety Officer.

    from medsim.safety.openfda import drug_interactions
    drug_interactions("warfarin")   # -> label text, or "" when unknown

The public openFDA drug-label endpoint needs no API key:

    https://api.fda.gov/drug/label.json?search=openfda.generic_name:"warfarin"&limit=1

Every response (including "not found") is cached to
`data/cache/openfda/<drug>.json` and the cache is committed, so an experiment
rerun is reproducible and works with no network at all. Set `OPENFDA_OFFLINE=1`
to forbid network access outright, which is what the test suite does.

Only the standard library is used: `requests` is not a dependency of this repo.
Populate the cache for a set of drugs with

    python -m medsim.safety.openfda warfarin ibuprofen "tmp-smx"
"""
from __future__ import annotations

import json
import logging
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field

from medsim.safety.rules import DRUG_ALIASES

log = logging.getLogger(__name__)

API = "https://api.fda.gov/drug/label.json"
CACHE_DIR = Path(__file__).resolve().parents[3] / "data" / "cache" / "openfda"
TIMEOUT = 20.0
# Label sections are long (warfarin's interactions text is ~6.5 KB); keep enough
# to be useful as evidence without bloating the committed cache.
MAX_FIELD = 6000
# openFDA indexes the same drug under several name fields.
SEARCH_FIELDS = ("openfda.generic_name", "openfda.brand_name", "openfda.substance_name")

# brand or abbreviation -> generic, so "Coumadin" and "warfarin" share one cache entry
_TO_GENERIC = {alias.lower(): generic
               for generic, aliases in DRUG_ALIASES.items() for alias in aliases}


class DrugLabel(BaseModel):
    """One cached openFDA lookup. `status` records why a field may be empty."""

    drug: str
    status: str = "ok"                      # ok | not_found | error | offline
    drug_interactions: str = ""
    contraindications: str = ""
    warnings: str = ""
    source_url: str = ""
    fetched_at: str = ""
    detail: str = ""                        # error text, for the audit trail

    @property
    def found(self) -> bool:
        return self.status == "ok" and bool(self.text)

    @property
    def text(self) -> str:
        parts = [("DRUG INTERACTIONS", self.drug_interactions),
                 ("CONTRAINDICATIONS", self.contraindications)]
        return "\n\n".join(f"{label}: {body}" for label, body in parts if body)


def canonical(drug_name: str) -> str:
    """'Coumadin' / 'WARFARIN ' -> 'warfarin'. Unknown names pass through cleaned."""
    name = re.sub(r"\s+", " ", drug_name.strip().lower())
    name = re.sub(r"\s*\d.*$", "", name).strip()          # drop a trailing dose
    return _TO_GENERIC.get(name, name)


def _slug(drug: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", canonical(drug)).strip("_") or "unknown"


def cache_path(drug: str, cache_dir: Optional[Path] = None) -> Path:
    return (cache_dir or CACHE_DIR) / f"{_slug(drug)}.json"


def _offline() -> bool:
    return os.environ.get("OPENFDA_OFFLINE", "").strip().lower() in ("1", "true", "yes")


def _clean(value: object) -> str:
    """openFDA returns each section as a list of long strings."""
    if isinstance(value, list):
        value = " ".join(str(v) for v in value)
    return re.sub(r"\s+", " ", str(value or "")).strip()[:MAX_FIELD]


def _request(url: str, timeout: float) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "SafeWard/0.1 (coursework)"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


def _fetch(drug: str, timeout: float) -> DrugLabel:
    """Try each name field in turn. A 404 from openFDA means 'no matching label'."""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    last_detail = ""
    for field in SEARCH_FIELDS:
        query = urllib.parse.quote(f'{field}:"{canonical(drug)}"')
        url = f"{API}?search={query}&limit=1"
        try:
            results = _request(url, timeout).get("results") or []
        except urllib.error.HTTPError as exc:
            if exc.code == 404:                       # not indexed under this field
                last_detail = "404 not found"
                continue
            log.warning("openFDA HTTP %s for %s", exc.code, drug)
            return DrugLabel(drug=canonical(drug), status="error", source_url=url,
                             fetched_at=now, detail=f"HTTP {exc.code}")
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            log.warning("openFDA lookup failed for %s: %s", drug, exc)
            return DrugLabel(drug=canonical(drug), status="error", source_url=url,
                             fetched_at=now, detail=f"{type(exc).__name__}: {exc}")
        if results:
            rec = results[0]
            return DrugLabel(
                drug=canonical(drug), status="ok", source_url=url, fetched_at=now,
                drug_interactions=_clean(rec.get("drug_interactions")),
                contraindications=_clean(rec.get("contraindications")),
                warnings=_clean(rec.get("warnings_and_cautions") or rec.get("warnings")),
            )
        last_detail = "no results"
    return DrugLabel(drug=canonical(drug), status="not_found", fetched_at=now, detail=last_detail)


def lookup(drug_name: str, cache_dir: Optional[Path] = None, timeout: float = TIMEOUT,
           refresh: bool = False) -> DrugLabel:
    """Cached label lookup. Never raises: failures come back as a status."""
    path = cache_path(drug_name, cache_dir)
    if path.exists() and not refresh:
        try:
            return DrugLabel.model_validate_json(path.read_text(encoding="utf-8"))
        except Exception as exc:                      # corrupt cache entry: refetch
            log.warning("Unreadable cache entry %s (%s); refetching", path, exc)
    if _offline():
        return DrugLabel(drug=canonical(drug_name), status="offline",
                         detail="OPENFDA_OFFLINE=1 and no cache entry")
    label = _fetch(drug_name, timeout)
    if label.status in ("ok", "not_found"):           # don't cache transient errors
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(label.model_dump_json(indent=2), encoding="utf-8")
    return label


def drug_interactions(drug_name: str, cache_dir: Optional[Path] = None) -> str:
    """The tool the Safety Officer calls: interaction + contraindication text, or ""."""
    return lookup(drug_name, cache_dir).text


# --------------------------------------------------------------------------- #
# Evidence for the officer prompt
# --------------------------------------------------------------------------- #
class ToolCall(BaseModel):
    """Recorded in SafetyVerdict.tool_calls so every lookup is auditable."""

    tool: str = "openfda.drug_interactions"
    drug: str
    status: str
    chars: int = 0
    source_url: str = ""
    matched: list[str] = Field(default_factory=list)   # co-prescribed drugs named in the label


def relevant_excerpt(label: DrugLabel, others: list[str], budget: int = 420) -> tuple[str, list[str]]:
    """Sentences of the label that actually name one of the other drugs in the plan.

    Feeding whole labels to the officer wastes the token budget and buries the
    signal, so only the sentences that mention a co-prescribed drug are kept.
    """
    if not label.text:
        return "", []
    sentences = re.split(r"(?<=[.;])\s+", label.text)
    hits, matched = [], []
    for other in others:
        term = canonical(other)
        if term == label.drug:
            continue
        pattern = re.compile(rf"(?<![a-z]){re.escape(term)}(?![a-z])", re.IGNORECASE)
        for sentence in sentences:
            if pattern.search(sentence) and sentence not in hits:
                hits.append(sentence)
                if term not in matched:
                    matched.append(term)
                break
    excerpt = " ".join(hits)[:budget]
    if not excerpt:                                    # nothing specific: lead with the section
        excerpt = label.text[:budget]
    return excerpt.strip(), matched


def evidence(drugs: list[str], cache_dir: Optional[Path] = None,
             max_drugs: int = 6) -> tuple[str, list[ToolCall]]:
    """Look up every drug in the plan and build the officer's evidence block."""
    lines, calls = [], []
    for drug in drugs[:max_drugs]:
        label = lookup(drug, cache_dir)
        excerpt, matched = relevant_excerpt(label, drugs)
        calls.append(ToolCall(drug=label.drug, status=label.status, chars=len(label.text),
                              source_url=label.source_url, matched=matched))
        if excerpt:
            flag = f" [names {', '.join(matched)} in this plan]" if matched else ""
            lines.append(f"- {label.drug}{flag}: {excerpt}")
        else:
            lines.append(f"- {label.drug}: no label text available ({label.status})")
    return "\n".join(lines), calls


# --------------------------------------------------------------------------- #
# Cache warming
# --------------------------------------------------------------------------- #
def warm_cache(drugs: list[str], cache_dir: Optional[Path] = None,
               refresh: bool = False) -> dict[str, str]:
    """Fetch and cache each drug; returns drug -> status."""
    out = {}
    for drug in dict.fromkeys(canonical(d) for d in drugs):
        label = lookup(drug, cache_dir, refresh=refresh)
        out[drug] = label.status
        print(f"{drug:34s} {label.status:10s} {len(label.text):6d} chars")
    return out


def main() -> None:
    import argparse

    ap = argparse.ArgumentParser(description="Populate the committed openFDA cache.")
    ap.add_argument("drugs", nargs="*", help="drug names; default = every drug in rules.py")
    ap.add_argument("--refresh", action="store_true", help="refetch entries that are already cached")
    args = ap.parse_args()
    drugs = args.drugs or sorted(DRUG_ALIASES)
    statuses = warm_cache(drugs, refresh=args.refresh)
    counts: dict[str, int] = {}
    for status in statuses.values():
        counts[status] = counts.get(status, 0) + 1
    print(f"\n{len(statuses)} drugs -> {counts}  (cache: {CACHE_DIR})")


if __name__ == "__main__":
    main()
