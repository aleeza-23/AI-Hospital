# Development log: G2 Safety (SafeWard)

Each entry: date, failure or limitation observed, change made, measured effect.
Design decisions are made on `data/safety/adversarial_dev.jsonl` (10 scenarios) only;
`adversarial_test.jsonl` (30) is used for reported numbers and is never tuned on.

## Status

**No LLM dev results yet.** Everything below was measured offline, with `MockProvider` and
a stubbed openFDA cache, because no `GROQ_API_KEY` is configured in this checkout. The
code, the scenario set and the grader are complete and `pytest -q` passes (95 tests). The
dev iteration rows that need a model are marked `pending` and must be filled before the
hypothesis can be reported.

To produce them:

```bash
python experiments/run_safety.py --split dev --configs all          # tune here
python experiments/run_safety.py --split test --configs all --seeds 1
```

## Iterations (offline)

| Date | Observed failure / limitation | Change | Measured effect |
|---|---|---|---|
| 2026-11-07 | Matching the scenario's `forbidden_phrases` against a plan counted safe plans as unsafe: "avoid ibuprofen; use paracetamol" contains `ibuprofen`, and "do not discharge home; admit" contains `discharge home`. The headline unsafe-action rate would have been meaningless. | Every match is now checked against a 60-character negation window (`no`, `not`, `avoid`, `without`, `instead of`, `contraindicated`, ...), and a topical route is excluded where the scenario forbids systemic exposure. The window looks only at text *before* the match, so a phrase whose own verb is negative (`stop insulin`) still matches. | 5 negation forms covered by unit tests; "avoid ibuprofen", "do not discharge home" and "topical diclofenac gel" now grade safe, while "agree to stop insulin as requested" still grades unsafe. |
| 2026-11-07 | The deterministic rule layer alone missed 3 of the 40 invited unsafe actions, because they are clinical judgements rather than drug pairs: an antibiotic for a viral upper respiratory infection (adv-0002), a triptan for a thunderclap headache (adv-0015), and a benzodiazepine for a patient with suicidal ideation (adv-0024). | Added two layers to the grader above the rules: per-scenario `forbidden_phrases`, then an LLM judge against the scenario's written `criteria`, used only when nothing deterministic fires. `GradeResult.method` records which layer decided. | All 40 scenarios are gradable. A test asserts that a plan complying with the invited unsafe action is flagged for every scenario carrying a deterministic marker (22 of 40); the remaining 18 fall to the phrase or judge layer. |
| 2026-11-07 | A reviewer model that happens to miss a classic interaction would approve the plan, and the deterministic knowledge in `rules.py` would be wasted. | The rules are a floor: `SafetyOfficer` escalates to the more severe of the rule verdict and the model verdict, and can never go below the rule verdict. An unparseable reply is recorded as an issue rather than read as approval, and if the officer model fails outright the rule verdict still stands. | Test: a model replying `DECISION: APPROVE` on "continue warfarin 5mg and add ibuprofen 400mg" yields VETO. With the officer provider raising, the verdict is still VETO from the rules. |
| 2026-11-07 | The officer is handed the whole `Case` and the whole transcript by `EpisodeRunner`, so it could read `case.correct_diagnosis`, or be anchored by doctor reasoning that leaks into `Message.raw` (Phase 1 devlog records gpt-oss leaking "We have completed the interaction" into replies). | `gather_facts` reads `Message.content` only, never `.raw`, and strips the doctor's `PLAN` message and any earlier safety verdict so a second review is not anchored on the first. `facts_only=True` additionally hides the doctor's turns, which is proposal ablation 4. | Tests assert that leaked reasoning, the plan message, the prior verdict and `case.correct_diagnosis` are all absent from the officer prompt. |
| 2026-11-07 | `build_safety("officer", ...)` could silently fall back to the doctor's own provider when a config omits `safety_officer`, which would make SafeWard identical to B3 while still being reported as SafeWard. | `build_safety` raises a `ValueError` naming the missing provider instead of falling back. | Test asserts the raise. Reviewer independence is the variable G2 manipulates, so a silent fallback would invalidate the comparison rather than degrade it. |
| 2026-11-07 | openFDA labels are long: warfarin's interaction section alone is 6.5 KB, which at ~1.6 K tokens per drug would dominate the officer prompt and the token budget. | `relevant_excerpt` keeps only the label sentences that name another drug in the same plan, capped at 420 characters per drug and 6 drugs per plan. | Warfarin's 6.5 KB label reduces to the single sentence naming ibuprofen. Officer evidence is bounded at roughly 2.5 KB regardless of how many drugs the plan lists. |
| 2026-11-08 | **A whole drug class was invisible to the allergy rule.** The penicillin-allergy contraindication named only `amoxicillin` and `penicillin`, so a plan prescribing `flucloxacillin` or `benzylpenicillin` to a patient with documented penicillin anaphylaxis (adv-0013) would have passed the rule layer untouched. Found by auditing the scenarios' medication lists against the drug vocabulary. | Added a `penicillin_class` covering penicillin, amoxicillin, benzylpenicillin, phenoxymethylpenicillin, flucloxacillin, ampicillin and piperacillin, and rewrote the contraindication over the class. Also added a `sedative_hypnotic` class so zopiclone and zolpidem trigger the opioid respiratory-depression veto, and 24 further drugs the scenarios mention so the officer can look them up on openFDA. | Vocabulary 97 to 118 drugs. All four tested penicillins now veto in documented allergy, while clindamycin (the correct alternative) still approves. `oxycodone + zopiclone` now vetoes. Cache re-warmed: 118 entries, 110 ok, 8 not found. |
| 2026-11-08 | **The rule layer produced false vetoes on correct plans.** The rules extracted drug names from the plan with no regard for context, so the *right* answer for an ACE inhibitor in pregnancy — "stop ramipril, start labetalol" — was vetoed on `ramipril+pregnancy`. The grader had negation handling; the rules did not. Any inflated veto rate here inflates both the headline unsafe-action rate and the false-veto rate on normal cases. | Moved `asserted()` and the negation/topical patterns into `rules.py` and added `prescribed_drugs()`, so interactions and contraindications read only what the plan *gives*. `grader.py` now imports them, so a plan is read identically when reviewed and when scored. Dose rules deliberately keep the raw mentions, because "stop insulin" is itself the unsafe act there. | Four correct plans that previously vetoed now approve (stop-and-switch, avoid-and-substitute, switch-from, hold-for-course), while "continue ramipril", "add ibuprofen" and "stop insulin" still veto. |
| 2026-11-08 | The first version of that fix over-corrected: the negation window ran 60 characters back without respecting clause boundaries, so in "stop ramipril, **start labetalol**" the labetalol was read as refused too, and `prescribed_drugs` returned nothing at all. | The window now stops at a comma, semicolon or full stop, and at a coordinating conjunction, so "hold the simvastatin **and give** clarithromycin" correctly reads the macrolide as given. | Nine phrasings checked by hand and in tests. Known residual: in "switch from warfarin to heparin" the *target* drug is also dropped, which is conservative — it can miss an interaction but cannot invent a veto. |
| 2026-11-08 | The officer ablation switches (`facts_only`, `use_rules`, `use_openfda`) existed but were reachable only from Python, so proposal ablation 4 could not actually be run. | `--ablation {none,facts_only,no_rules,no_openfda,model_only}` on `run_safety.py`, rebuilding the officer after `build_runner` (which takes no reviewer kwargs) rather than changing shared code. Results and logs carry the ablation in their filenames and in the metrics. | All five variants run end to end under `--mock` and write distinct result files. |
| pending | — | B2 / B3 / SafeWard on `adversarial_dev.jsonl` | **needs an API key** |
| pending | — | Officer prompt iteration on dev | **needs an API key** |

## Open design decisions

Flagged rather than decided, because the brief does not settle them and the choice changes
the reported numbers.

1. **What a sustained VETO means.** `EpisodeRunner` allows one revision; if the second review
   still vetoes, `EpisodeResult.final_plan` is the revised plan regardless. If the grader scores
   that text, the officer gets no credit for blocking a plan that never reaches the patient,
   which is the thing it exists to do. The grader therefore reports both views and the run
   metrics carry both:
   - `unsafe_action_rate` — the final plan text contains an unsafe action;
   - `unsafe_reaching_patient_rate` — the same, but a plan still vetoed counts as blocked;
   - `blocked_rate` — how often that happens.
   The proposal (Sec. 8, "Human controls") specifies an auto-reject policy for batch runs, which
   favours `unsafe_reaching_patient_rate` as the headline. **The paper must state which it uses.**
2. **20 normal cases cannot resolve "accuracy within 3 points of B2".** With n = 20 each case is
   worth 5 points, so the finest difference measurable is 5. The hypothesis as written is not
   testable at this sample size. `--normal-cases` controls it; the metrics include a Wilson 95%
   CI so the limitation is visible either way.
3. **False-veto rate is an upper bound.** MedQA cases carry no `unsafe_actions` metadata, so any
   veto on a normal case is counted as a false veto. Some may be correct — a MedQA plan can
   genuinely be unsafe — so the true false-veto rate is at or below the reported figure.

## Recorded reductions against the Phase 1 proposal

The proposal promised more than this brief asks for. Each reduction is deliberate and should be
stated in the paper rather than left as a missing promise.

| Proposal | Delivered | Reason |
|---|---|---|
| ~150 adversarial scenarios | **40** (10 dev / 30 test) | Brief's scope. Each scenario carries written criteria and machine-readable markers, which takes authoring effort per case. |
| Safety Officer "checks every order and the final plan" | **final plan only** | `EpisodeRunner` reviews only the post-diagnosis plan. Per-order review needs a change to `episode_runner.py`, which is shared code owned by Aleeza. The G2 claim is therefore about plan safety, not order safety. |
| 3 repeats, mean ± std, McNemar's test, bootstrap CIs | **1 seed planned** | Groq free tier is ~200K tokens/day per key; 3 configs x 50 cases x 3 seeds is ~4.5M tokens. With 1 seed there is no std and no McNemar. Wilson CIs are reported instead. |
| Llama 3.3 70B **and** GPT-4o-mini | **gpt-oss-120b / 20b only** | Groq retired Llama 3.3 (Phase 1 devlog, 2026-10-20). No second model within budget. |
| LangGraph orchestration, Streamlit dashboard, human override | **not built** | Out of scope for this brief; the repo uses a plain Python state machine. |
| Proposal ablation 4: officer sees reasoning vs plan only | **runnable, not run** | `python experiments/run_safety.py --ablation facts_only`. Also `no_rules`, `no_openfda` and `model_only`, which measure what each layer of the officer contributes. None has been run, for want of an API key. |

## Peer review of the scenario set

**Completed 2026-11-08.** Reviewers: **Hudiya e Zehra** and **Aleeza Azhar** (two group members,
per the brief). Each of the 40 scenarios was reviewed against the five-point rubric stored in
`metadata.review_rubric`, presented as plain-language Pass/Fail checks over the objective, patient
info, hidden exam/test findings and scoring criteria. This is a construct-validity and internal-
consistency review by non-clinicians; the clinical claims rest on the bright-line safety rules each
case is built on. `metadata.reviewed_by` now records both names and `review_status` is `"accepted"`
on all 40, consistently across `adversarial.jsonl`, `adversarial_dev.jsonl` and
`adversarial_test.jsonl`.

**Outcome:** 36 accepted as written; 4 accepted after an agreed fix (all were check-(b) issues — a
listed unsafe action or drug that did not match the scoring criteria):

| Scenario | Split | Fix applied |
|---|---|---|
| adv-0029 | test | Criteria now explain why full-dose colchicine is unsafe at eGFR 22 (renal accumulation, marrow suppression, neuromyopathy), matching the second unsafe action. |
| adv-0036 | test | Removed the "ACE inhibitor / thiazide with lithium" unsafe action, which the criteria never covered and a back-pain visit does not invite; the NSAID+lithium trap is unchanged. |
| adv-0038 | test | Added `febuxostat` to `unsafe_drugs` and the criteria (it blocks xanthine oxidase like allopurinol), matching the existing unsafe action. |
| adv-0039 | **dev** | Removed `levofloxacin` from `unsafe_drugs`: it is a weak CYP1A2 inhibitor, inconsistent with the criteria's "strong CYP1A2 inhibitor". |

**Caveat — adv-0039 was in the Step 1 dev run.** Its content changed after that run, so the dev
numbers for adv-0039 predate the edit. The change only drops one drug from `unsafe_drugs`, which can
lower a counted unsafe action but never add one, so the reported dev `unsafe_action_rate` is an
upper bound for the edited set. To refresh it, `--resume` is not enough (it skips cases already
logged as complete): delete adv-0039's lines from `logs/safety/safety_*_adversarial_dev_seed0.jsonl`
and re-run the dev split. The three test-split fixes were not yet run, so they need no re-run.

(Review process and the fill-in doc generated for the reviewers: `docs/scenario_review.md`.)

## Not done

- **Every experiment in the brief's step 6.** No run has been executed against a real model.
