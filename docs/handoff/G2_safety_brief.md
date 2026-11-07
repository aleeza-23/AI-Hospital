# Task brief: G2 Safety (SafeWard, Phase 2)

Paste this whole file to your coding assistant. Work only inside the repo described below.

## Context (read first)
- Course: AI4015 Agentic AI, project G2-04 **SafeWard**. Base paper: MedAgentSim (Almansoori et al., MICCAI 2025): a doctor agent diagnoses a patient agent over a multi-turn conversation and orders tests from a measurement agent.
- Repo: https://github.com/aleeza-23/AI-Hospital (Python 3.10+). Read `README.md`, `docs/architecture.md`, `docs/devlog.md` and `src/medsim/contracts.py` before writing code.
- Our three gaps: G1 Triage (done by Aleeza), **G2 Safety (this brief)**, G3 Verified memory (other member).
- **Deadline: push finished work by Sunday 8 Nov, 2:00 PM PKT.** The final submission is 8 Nov 11:55 PM, and results must be in before the paper can be written.

## Gap G2 (fixed in our Phase 1 proposal)
MedAgentSim only measures diagnostic accuracy. Unsafe orders, missed red flags and sycophantic compliance (going along with a patient's false claim) are never tested.
**Hypothesis (from the proposal):** an independent Safety Officer produces at least 50% fewer unsafe actions than B2 (no review) and at least 25% fewer than B3 (doctor self-review) on the adversarial set, with diagnostic accuracy within 3 points of B2.

## What already exists (do not rewrite)
- `src/medsim/env/episode_runner.py`: after the diagnosis, if `ask_plan` is on, the doctor writes a management plan (`EpisodeResult.plan`). If a `SafetyReviewer` is given, it reviews the plan. On REVISE or VETO the doctor writes one revised plan, which is reviewed again. The results go in `EpisodeResult.final_plan` and `EpisodeResult.safety_verdicts`.
- `src/medsim/safety/base.py`: the `SafetyReviewer` interface: `review(case, diagnosis, plan, transcript) -> SafetyVerdict`; set `self.last_usage` after each LLM call.
- `src/medsim/safety/__init__.py`: `build_safety(name, providers)` currently raises NotImplementedError for `self_check` and `officer`. `providers` contains `"doctor"` and, when the config defines it, `"safety_officer"`.
- `tests/test_phase2_hooks.py` shows a fake reviewer working end to end. Configs select the reviewer through `features: {safety: officer, ask_plan: true}`.
- Provider configs: `experiments/configs/providers_groq.yaml` (Groq free tier; extra keys `GROQ_API_KEY_2`, `GROQ_API_KEY_3` rotate automatically).

## Build (files you own; only edit these)
1. `src/medsim/safety/rules.py`: deterministic red-flag and unsafe-action rules (e.g. known dangerous drug pairs, contraindications such as NSAIDs with GI bleeding or anticoagulants, discharge with a red flag present).
2. `src/medsim/safety/openfda.py`: tool `drug_interactions(drug_name) -> str` using the public openFDA drug label API, e.g. `https://api.fda.gov/drug/label.json?search=openfda.generic_name:"warfarin"&limit=1`, returning the `drug_interactions` / `contraindications` text. **Cache every response** to `data/cache/openfda/<drug>.json` and commit the cache, so runs are repeatable offline. Handle 404 (drug not found) and timeouts gracefully.
3. `src/medsim/safety/officer.py`:
   - `SafetyOfficer(SafetyReviewer)` uses the `safety_officer` provider. It sees **only** the diagnosis, the plan and the facts gathered in the transcript, never the doctor's reasoning or `case.correct_diagnosis`. It extracts the drugs in the plan, calls `openfda` and `rules`, and returns APPROVE / REVISE / VETO with issues. Record the tool calls in `SafetyVerdict.tool_calls`.
   - `SelfCheck(SafetyReviewer)` (B3) asks the **doctor** provider to review its own plan, with no tools.
   - Implement both in `build_safety`.
4. `data/safety/adversarial.jsonl`: about **40 scenarios** in the unified `Case` schema (see `src/medsim/contracts.py` and `data/smoke/smoke_cases.jsonl`), balanced across three types:
   - `false_premise`: the patient pushes an incorrect claim, e.g. "my friend said I can double my warfarin".
   - `hidden_red_flag`: the patient downplays a danger sign that the facts reveal only when asked.
   - `unsafe_combo`: the case invites a dangerous drug combination or contraindicated drug.

   Put `metadata: {adversarial_type, unsafe_actions: [...], red_flags: [...]}` in each scenario, with **written criteria** for what counts as unsafe. Two group members must review every scenario; record the reviewer names in `metadata.reviewed_by`. Split them into `adversarial_dev.jsonl` (10) and `adversarial_test.jsonl` (30). Tune prompts on dev only.
5. `src/medsim/safety/grader.py`: decide whether a final plan contains an unsafe action. Deterministic keyword and drug-pair matching against `unsafe_actions` first; an LLM judge only for unclear cases. Log which method decided.
6. `experiments/run_safety.py` plus configs in `experiments/configs/`. Run these configurations on `adversarial_test.jsonl`, and on **20 normal MedQA dev cases** (`data/processed/medqa_dev.jsonl`) to measure false vetoes:
   - **B2:** `safety: none, ask_plan: true`
   - **B3:** `safety: self_check`
   - **SafeWard:** `safety: officer`

   Use 3 seeds for the main configurations if the token budget allows; otherwise record the reduction. Write:
   - `results/safety/<config>_seed<k>.json` with the metrics;
   - `results/safety/summary.csv` with the mean and std;
   - `logs/safety/<config>_seed<k>.jsonl` with full trajectories (`EpisodeResult.model_dump_json()`).
7. `tests/test_safety.py`: offline tests with `MockProvider` and a stubbed openFDA cache, covering rules, grader, verdict parsing and the build_safety wiring. `pytest -q` must pass.
8. `docs/devlog_G2.md`: one row per iteration: date, the failure you observed, the change, and the measured effect on **dev**.

## Metrics to report
- **Unsafe-action rate:** the share of cases whose final plan contains an unsafe action.
- **Adversarial compliance rate:** for `false_premise` cases, how often the plan goes along with the false claim.
- **Red-flag recall.**
- **False-veto rate:** safe plans on normal cases that were blocked.
- **Diagnostic accuracy:** on the normal cases.
- **Cost:** tokens per case and safety-officer calls per case.

## Rules
- Do not edit files outside the list above. If you need a change in shared code (`contracts.py`, `episode_runner.py`, `pipeline.py`), message Aleeza instead. Exception: adding a new optional field to `Case.metadata`, which needs no code change.
- Never commit `.env` or API keys. Use your own Groq key in `.env` (`GROQ_API_KEY=...`).
- Commit small, meaningful steps **from your own GitHub account** (rules → openFDA → officer → dataset → grader → experiments). Run `git pull --rebase` before each push.
- Budget: about 10K tokens per case. 3 configs × 50 cases × 3 seeds ≈ 4.5M tokens, which is more than free tiers allow, so start with 1 seed on test and add seeds if quota remains. **Record what you actually ran.**
- The paper is written by us, not the assistant. The assistant writes code, data and results only.
