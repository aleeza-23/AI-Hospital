# Development log: G3 Verified memory

Each entry: date, failure or limitation observed, change made, measured effect.
Design decisions were made offline or on `data/processed/medqa_ext_dev.jsonl`;
`medqa_ext_test.jsonl` supplies the paired cases and `medqa_test.jsonl` the consultation
subset, neither of which was tuned on.

## Status

**Poisoning: done** (`--limit 12`, 1 seed, 48 episodes, zero errors) — see the 2026-11-09 row
and `results/memory/`.

**Consultation E1 / E2 / E3: NOT RUN.** The code and configs are complete and verified offline,
but no run has ever been executed, and `results/consultation/` does not exist. This is the one
remaining gap in G3. It needs a `GROQ_API_KEY`, which is not present in every checkout; the
member who has quota should run:

```bash
python experiments/run_consultation.py --configs e1 e2 e3 --n 20     # ~650K tokens
python experiments/run_consultation.py --configs e1 e2 e3 --n 20 --resume   # after a daily cap
```

Everything else below was measured offline with `MockProvider` and the committed data.
`pytest -q` passes (164 tests).

## For the README

`README.md` is not a G3 file, so this sentence is recorded here for whoever edits it:

> Memory retrieval uses TF-IDF cosine similarity (`src/medsim/memory/embed.py`) in place of
> MedAgentSim's CLIP embeddings. The cases in this repository are text-only and no GPU is
> available, so an image-text embedding model would add a dependency and a download without
> changing what is retrieved. Weighting follows scikit-learn's defaults (sublinear term
> frequency, smoothed inverse document frequency, L2-normalised vectors), so the numbers remain
> comparable with a scikit-learn implementation.

## Iterations (offline)

| Date | Observed failure / limitation | Change | Measured effect |
|---|---|---|---|
| 2026-11-08 | **`medqa_ext` is a superset of `medqa`.** The brief says to draw the paired cases from both `medqa_test.jsonl` and `medqa_ext_test.jsonl` and to pre-fill memory from dev cases. Measured overlap by content hash: `medqa_test` shares **78 of its 87** cases with `medqa_ext_test`, and `medqa_dev` shares **19 of its 20**. Pre-filling from `medqa_dev` would therefore have written the answers to the paired cases straight into memory, inflating accuracy in *both* arms and destroying the comparison. | Restricted the whole poisoning experiment to one dataset: pairs from `medqa_ext_test`, pre-fill from `medqa_ext_dev`. | `medqa_ext_dev` ∩ `medqa_ext_test` = **0** (asserted in `tests/test_memory.py`). Nothing is lost, because `medqa_ext_test` already contains almost all of `medqa_test`. |
| 2026-11-08 | **Pairing by identical diagnosis is not possible at this size.** After de-duplicating by content the combined test pool is 204 distinct cases, not 282, and only **3** normalised diagnoses have 3 or more distinct cases (chronic lymphocytic leukemia, pneumonia, neuroleptic malignant syndrome). Ten families of three were unobtainable. | Paired by TF-IDF similarity of `case.objective` instead, which the brief permits ("same or similar") and which is the actual poisoning mechanism: a lesson can only mislead a case it is retrieved for. | `medqa_ext_test` alone yields **26** disjoint families of 3 at cosine >= 0.30; 10 were selected, giving 30 distinct cases. |
| 2026-11-08 | One wrong lesson was retrieved for only 2 of its 3 paired cases. `medqa_ext-0180` (atrial myxoma, objective about shortness of breath) scored 0.187 against the loss-of-consciousness lesson and lost the top-3 slot. A lesson that is never retrieved tests nothing. | Replaced it with `medqa_ext-0159` (hypertrophic cardiomyopathy, "sudden loss of consciousness"), a better lexical *and* clinical fit, since "orthostatic hypotension, no cardiac workup needed" is lethally wrong in HCM. Widened the lesson wording to cover sudden onset and collapse. | Poison retrieved for **30/30** paired cases, up from 29/30, against a memory pre-filled exactly as the experiment pre-fills it. |
| 2026-11-08 | Suspected that the dataset boilerplate every objective shares ("Assess and diagnose the patient presenting with") would dominate cosine similarity, so it was added to the stop list. | Measured it rather than assuming: re-ran poison retrieval with the boilerplate stop-worded and with a generic stop list only. | **No effect: 30/30 either way.** The suspicion was wrong. Smoothed idf already drives the weight of a term appearing in every document to near zero, so the stop list is redundant for this metric. It is kept because it shortens the vectors, but the earlier commit message overstates its value and this row is the correction. |
| 2026-11-08 | `"seizures"` and `"seizure"` were separate terms, so an obvious match could be missed. | Crude singularisation in `tokenize`, with `ss`/`us`/`is` endings left alone. | `tokenize("seizures") == tokenize("seizure")`, and `"ptosis"` does not become `"ptosi"`. Both asserted in tests. |
| 2026-11-08 | **A resumed poisoning run reported the wrong headline number.** Wrong-lesson retrieval was read out of the live memory object, which only has entries for cases run in that invocation. A fresh 6-case arm reported 4/6; resuming the same arm reported **0/6**. | Read it out of the persisted `EpisodeResult.memory_context` instead, which is written for every case. | Fresh run and resumed run now both report 4/6. |
| 2026-11-08 | Poison retrieval during a *live* ungated run is lower than against a static memory, because ungated memory keeps writing a lesson from every episode as the run proceeds and those compete for the top k. | None: this is genuine MedAgentSim behaviour, not a defect. | On a 6-case mock arm, 4/6 live against 6/6 static. `wrong_lesson_retrieval_rate` is therefore reported as a measured quantity per run, never assumed to be 1.0. |
| 2026-11-08 | — | The gate itself, checked against the committed data. | Ungated admits **10/10** wrong lessons; gated admits **0/10** and records a reason for each rejection. |
| 2026-11-09 | Poisoning: accuracy clean vs poisoned, both memories. Ran `run_memory_poisoning.py --limit 12` (first 4 lesson families = 12 of 30 paired cases, 10 wrong lessons pre-filled, 1 seed) on gpt-oss-120b; ~389K tokens, zero errored episodes. | Measured the four arms. | **The gate blocks the poison as designed, but the accuracy harm is not demonstrated at this size.** Ungated: clean **0.583**, poisoned **0.583** -> drop **0.0** (10/10 wrong lessons admitted; a wrong lesson retrieved for 10/12 cases). Gated: clean **0.500**, poisoned **0.667** -> **0/10 wrong lessons admitted**. So the gate mechanism is unambiguous (10 -> 0 admitted), but on these 12 cases the retrieved poison did not flip the diagnosis, so ungated accuracy is flat and the hypothesis's ">=5-point ungated drop" is **not met** (n=12, 1 seed, no CI separation; the gated "<=2" is met only trivially). Honest reading: this run supports *"the gate prevents poison admission"* and does **not**, at this scale, establish *"poison degrades accuracy."* A larger `--limit` (more lesson families) and/or more seeds would be needed to test the accuracy claim. |
| 2026-11-09 | **`cases_retrieving_a_wrong_lesson` was overcounted, and the committed clean-arm figure was impossible.** `ungated_clean` reported a wrong lesson retrieved for 1 of 12 cases, in the arm where nothing is ever injected. Cause: the matcher tested whether the first 80 characters of a lesson appeared in `EpisodeResult.memory_context`, but every lesson opens by restating the case objective, and for **6 of the 10** that restatement is longer than 80 characters — so an ordinary episode-derived record for the same case matched. The false hit was `medqa_ext-0185`, whose context held an episode entry with insight "Recorded from an unverified episode outcome." | Match the `Lesson:` field of the context line instead. Only a submitted lesson carries its own text there; an episode entry carries "Confirmed correct." or "Recorded from an unverified episode outcome." | `ungated_clean` corrected from **1 to 0** (rate 0.0833 to 0.0), which is what the clean arm must report by construction. The other three arms were unaffected: ungated poisoned stays **10/12**, both gated arms **0/12**. Accuracy figures unchanged. Regression tests added. |
| 2026-11-09 | Correcting that metric would normally mean re-running the arms, i.e. spending ~389K tokens again to fix an arithmetic error. | Added `--recompute`, which re-derives every number from the committed logs with no provider, no API key and no model call. The memory is replayed offline — pre-fill, inject, then walk the logged episodes calling `context_for` and `add`, which are pure Python — so entries, retractions and the audit trail are reproduced exactly. | All four arms recomputed for **0 tokens**. `replayed_context_matches` is **true** for all four, i.e. the replayed retrieval agrees with the `memory_context` stored in the log case for case, which is what makes the replay trustworthy. |
| 2026-11-09 | **Both G3 runners froze transient failures into their results on resume.** `run_safety.py` had been fixed to treat an errored episode as not-done, but `run_consultation.py` and `run_memory_poisoning.py` still counted an `ERROR` status as finished, so a provider 400 or a dropped connection would be kept permanently and never retried. The consultation runs have not happened yet and will certainly be resumed across daily token caps, so this would have corrupted them. | Ported the fix to both: on resume, errored episodes are dropped and the log is rewritten without them, so only completed episodes are skipped. | The committed poisoning logs contain 48 episodes, all `diagnosed`, so nothing already measured was affected. Both runners verified fresh and resumed under `--mock`. |
| pending | — | E1 / E2 / E3 consultation runs | **not run; needs an API key** |
| pending | — | `DOCTOR_COT` and `DOCTOR_MEMORY` prompt tuning on dev | **needs an API key** |

## Design decisions worth stating in the paper

1. **Where the lesson's wrongness is defined.** Each wrong lesson names a `source_case_id` and a
   `claimed_diagnosis`. The gate verifies the claim against that source case's ground truth, so
   only the 10 source cases are registered with the gate — the minimum it needs. Nothing
   registered reaches the doctor's prompt: only an entry's summary, diagnosis and insight are
   ever shown. The gate having ground-truth access is the premise of verified memory, not a leak.
2. **The gate fails closed.** A lesson about a case whose ground truth is unknown is rejected,
   because an unverifiable claim is exactly what the gate exists to stop. This makes
   `GatedMemory` strictly more conservative than the hypothesis requires, and it should be stated
   rather than left implicit.
3. **A REVISE does not block a write; only a VETO does.** The brief says "no `safety_verdicts`
   entry is a VETO", so a plan the officer merely questioned still enters memory if the diagnosis
   was right.
4. **Automatic retraction threshold is 2.** One wrong answer after a retrieval is noise; two is
   evidence. The counter is per entry, so a single bad lesson retrieved by many cases is withdrawn
   quickly. `auto_retract_after` is configurable and was not tuned, since tuning it needs model runs.
5. **`workers` is forced to 1** in both memory scripts regardless of config, because one memory is
   shared across a run and the order the cases are seen in is part of the experiment.

## Recorded reductions against the Phase 1 proposal

| Proposal | Delivered | Reason |
|---|---|---|
| CLIP embeddings for kNN | **TF-IDF cosine, pure stdlib** | Text-only cases, no GPU. scikit-learn would be a new dependency in `requirements.txt` and `pyproject.toml` for ~80 lines of arithmetic over documents numbering in the tens. |
| 20 wrong lessons, each paired with 5 similar cases (100 cases) | **10 lessons x 3 cases (30)** | The brief's scope. Also bounded by the data: see the pairing row above. |
| Consultation set of 200 MedQA/NEJM cases | **40 MedQA cases, subset seed 0** | The brief's scope (30-50) and the token budget. |
| MedAgentSim's multi-doctor majority-vote ensembling | **not implemented** | Explicitly skipped for cost in the brief. E2 is therefore a *partial* reproduction: memory + CoT only. This must be stated wherever E2 is compared with the paper's numbers. |
| Reflection on misdiagnosis before storing | **not implemented** | Not in the brief's build list. `UngatedMemory` stores the doctor's own final diagnosis as the lesson, which is the un-reflected form and the one that makes the poisoning mechanism work. |
| 3 repeats, mean ± std | **1 seed planned** | Groq free tier is ~200K tokens/day per key. With 1 seed there is no std; Wilson 95% CIs are reported instead. |
| Streamlit dashboard showing the memory log | **not built** | Out of scope for this brief. The audit log is written to `logs/memory/<arm>_memory_audit.jsonl` instead. |

## Beyond the brief

`experiments/configs/consultation_e3_full.yaml` adds the complete SafeWard configuration —
gated memory + chain of thought + the G2 Safety Officer. The brief leaves the Safety Officer for
another member to add, but since G2 and G3 are being done by the same person it is wired up and
verified offline. It is the configuration where the two gaps actually interlock: `GatedMemory`
refuses to store an outcome the officer vetoed.

## Not done

- **Every experiment.** No run has been executed against a real model.
- **Prompt tuning.** `DOCTOR_COT` and `DOCTOR_MEMORY` are unchanged from Phase 1. The brief permits
  tuning them on dev results, which do not exist yet.
- **The README sentence** above has not been added to `README.md`, which is not a G3 file.
