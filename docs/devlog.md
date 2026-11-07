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
