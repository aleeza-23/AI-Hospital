# Development log

Each entry: date, failure or limitation observed, change made, measured effect.
Design decisions were made on dev splits only; test splits are used for reported numbers.

## Phase 1 (baseline consultation loop)

| Date | Observed failure / limitation | Change | Measured effect |
|---|---|---|---|
| 2026-10-20 | Groq retired `llama-3.3-70b-versatile`; every case failed with HTTP 404, and the run kept going case after case. | Switched to `openai/gpt-oss-120b`; 401/403/404 now raise `ProviderConfigError` and abort the run. | Runs start; misconfiguration stops the run immediately instead of producing 100% error rows. |
| 2026-10-20 | Free-tier 8K tokens/min limit: repeated HTTP 429, a 3-case pilot stalled. | Retry waits the server's `retry-after`; patient and measurement moved to `gpt-oss-20b` (separate rate-limit bucket); smaller `max_tokens` (Groq counts it against the limit). | 3-case pilot completed with no stalls. |
| 2026-10-20 | Doctor packed up to 5 questions, tests and a diagnosis into one reply (bypassing the turn budget) and echoed the turn counter. | One action per turn (`parse_doctor_action`), turn counter moved to the system prompt, original reply kept in `Message.raw`. | Pilot transcripts: one action per turn. |
| 2026-10-20 | Tests listed before a diagnosis in the same reply were skipped (case medqa-0000: 0 tests run). | Earliest action wins, except on the forced final turn. | Same case: 2 tests run, still correct. |
| 2026-10-20 | Measurement agent answered "NORMAL READINGS" for recorded tests named differently ("Brain MRI with contrast" vs `MRI_Brain`), withholding evidence. | Prompt lists the recorded test names and asks for matching by meaning. | 7/7 probe requests answered correctly (was 5/7). |
| 2026-10-20 | Run stopped at 35/107 MedQA cases: Groq free tier daily token limit (~200K tokens/day). | Recorded initial results on 35 cases; budget per case measured (about 8K tokens). | Phase 1 baseline: 74.3% (26/35). |

## Phase 2

| Date | Observed failure / limitation | Change | Measured effect |
|---|---|---|---|
| 2026-11-07 | G1: ward simulation gave identical waits for FIFO and acuity ordering: cohort was the first 50 cases of a level-sorted split, and one doctor was overloaded. | Cohorts drawn at random (5 seeded wards of 50), 2 doctors, 10-minute mean inter-arrival. | Acuity ordering now separates from FIFO (dev: high-acuity wait 14.4 to 6.9 min with nurse labels). |
| 2026-11-07 | G1: the Triage Nurse agent under-triaged urgent patients: 30% of dev level 1-2 cases got a less urgent level (dev, 25 cases), worse than the single-LLM baseline (10%). Example: ktas-0820, "mental change", expert level 1, nurse level 2. | Added a rule-floor guardrail: when the deterministic danger-sign rules give level 1-2, the agent cannot assign a less urgent level (`nurse_floor`). Evaluated on the same nurse trajectories. | Dev under-triage on level 1-2: 30% to 0%; QWK 0.73 to 0.80; accuracy 48% to 56%. |
| 2026-11-07 | Patient agent returned an empty reply once (`finish_reason=length`): reasoning tokens used up `max_tokens`. | Patient `max_tokens` 400 to 700. | To be checked on the test run. |

### 2026-11-07: G1 test-split results (141 cases, 51 at KTAS 1–2, 1 seed, gpt-oss-120b)
Run once on the held-out test split after all dev tuning was frozen. `python experiments/run_triage.py --split test --methods all --seeds 1`.

| Method | Acc | QWK | Under-triage (all) | Under-triage KTAS 1–2 | Tokens/case | Ward high-acuity wait (min) |
|---|---|---|---|---|---|---|
| Human nurse (KTAS_RN) | 0.844 | 0.949 | 0.121 | 10/51 = 0.196 | – | 6.89 |
| B1 rules | 0.596 | 0.734 | 0.142 | 11/51 = 0.216 | 0 | 8.62 |
| B0 single LLM | 0.461 | 0.720 | 0.340 | 24/51 = 0.471 | 507 | 11.18 |
| Triage Nurse agent | 0.397 | 0.660 | 0.355 | 31/51 = 0.608 | 1864 | 11.22 |
| Nurse + rule floor (SafeWard G1) | 0.546 | 0.752 | 0.191 | 10/51 = 0.196 | 1864 | 9.27 |
| FIFO (no triage, MedAgentSim) | – | – | – | – | – | 14.38 |

- H1 holds for the guarded agent: high-acuity under-triage 0.471 → 0.196 vs B0 (−58% relative; paired bootstrap diff 0.275, 95% CI [0.157, 0.392]), and QWK 0.752 > B1 0.734 (small margin).
- H1 fails for the unguarded agent: the interview-only nurse under-triages more than B0 (0.608). The rule floor overrode 25/141 decisions; the gain comes from the rule floor, not from the interview.
- The rule floor's advantage over rules alone is not significant (diff 0.02, CI [0.0, 0.059]). Rules alone have higher accuracy (0.596 vs 0.546).
- Limitation: 1 seed only (free-tier budget); CIs are case bootstrap, not seed variance.
