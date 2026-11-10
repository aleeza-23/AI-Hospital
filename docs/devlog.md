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

### 2026-11-10: G1 repeat run (seed 1), nurse and nurse + floor
Second complete run of the LLM methods on the same frozen test split, so B0, the nurse and the guarded nurse now have two runs each. No failed cases (141/141 for each). `--start-seed 1` and a per-case checkpoint were added to `run_triage.py` after the free-tier daily limit stopped the run partway.

| Method | Acc (run 1 / run 2) | QWK (run 1 / run 2) | Under-triage KTAS 1–2 (run 1 / run 2) |
|---|---|---|---|
| B0 single LLM | 0.461 / 0.504 | 0.720 / 0.746 | 24/51 / 19/51 |
| Triage Nurse agent | 0.397 / 0.418 | 0.660 / 0.690 | 31/51 / 31/51 |
| Nurse + rule floor | 0.546 / 0.560 | 0.752 / 0.772 | 10/51 / 10/51 |

- H1 holds in both runs for the guarded agent: relative reduction against B0 is 58% and 47%, paired bootstrap difference 27.5 points [15.7, 39.2] and 17.6 points [5.9, 31.4]. QWK beats B1 (0.734) in both.
- The run-to-run change is mostly in B0. Nine of the ten patients the guarded agent still under-triages are the same in both runs.
- The second run has the first correct-to-wrong floor override: `ktas-1221`, expert level 4, raised to level 2.
- Still not significant against rules alone (2.0 points, CI [0.0, 5.9], both runs). Two runs are too few to estimate variation between runs.

### 2026-11-10: Consultation E1/E2 in the paper, and a second E2 run
The G3 member's commit 19aa524 ran E1 and E2 on the same 10 MedQA test cases (subset seed 0): E1 6/10 (60%, Wilson [31, 83]), E2 5/10 (50%, [24, 76]). A separate E2 run on 15 cases (which include those 10) was made the same day, on keys 2 and 3. It is kept in `results/consultation/repeat_n15/` and `logs/consultation/repeat_n15/` so the two runs do not overwrite each other.

- The repeat reached 9/15 (60%, [36, 80]) and 7/10 on the shared cases. It differs from the first E2 run on `medqa-0056` and `medqa-0062`, which is more than the one-case E1–E2 difference. At this size, memory with chain-of-thought shows no effect either way.
- Cold start: only 2 of 10 E2 cases (7 of 15 in the repeat) retrieved any memory entry.
- E3 was stopped after 6 of 15 cases (quota) and is not reported. Its partial log is outside the repo, in `../consultation_local_backup/`.
- Both runs are reported in the paper, not only the better one.
