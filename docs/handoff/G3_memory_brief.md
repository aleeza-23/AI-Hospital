# Task brief: G3 Verified memory + base-paper reproduction (SafeWard, Phase 2)

Paste this whole file to your coding assistant. Work only inside the repo described below.

## Context (read first)
- Course: AI4015 Agentic AI, project G2-04 **SafeWard**. Base paper: MedAgentSim (Almansoori et al., MICCAI 2025): a doctor agent diagnoses a patient agent over a multi-turn conversation and orders tests from a measurement agent. It improves over time with a memory of past cases, kNN retrieval, reflection and chain-of-thought.
- Repo: https://github.com/aleeza-23/AI-Hospital (Python 3.10+). Read `README.md`, `docs/architecture.md`, `docs/devlog.md` and `src/medsim/contracts.py` before writing code.
- Our three gaps: G1 Triage (done by Aleeza), G2 Safety (other member), **G3 Verified memory (this brief)**. You also run the **consultation experiments E1/E2** on MedQA.
- **Deadline: push finished work by Sunday 8 Nov, 2:00 PM PKT.** The final submission is 8 Nov 11:55 PM, and results must be in before the paper can be written.

## Gap G3 (fixed in our Phase 1 proposal)
MedAgentSim's self-evolving memory stores lessons without vetting them, so a wrong lesson can spread to later patients.
**Hypothesis:** with injected wrong lessons, accuracy on the paired similar later cases drops by **at least 5 points** under ungated memory but by **no more than 2 points** under SafeWard's gated memory.

## What already exists (do not rewrite)
- `src/medsim/memory/buffer.py` defines the `MemoryBuffer` contract:
  - `context_for(case)` runs before the consultation, and its text goes into the doctor's system prompt. Query **only with `case.objective`**; that is all the doctor knows up front.
  - `add(case, result)` runs after judging. `result.verdict.correct` and `result.safety_verdicts` are available here.
  - `retrieve(query, k)` returns a list of `MemoryRecord`.
- `src/medsim/memory/__init__.py`: `build_memory(name, k=...)` raises NotImplementedError for `ungated` and `gated`.
- The doctor prompt supports `cot: true` (`features.cot`). Memory and CoT are switched by config: `features: {memory: gated, cot: true, memory_k: 3}`.
- `tests/test_phase2_hooks.py` shows a fake memory working end to end. `scripts/run_baseline.py --config <yaml>` runs a full consultation experiment (`runs/<name>/episodes.jsonl` + `metrics.json`). One runner, and so one memory, is shared across the cases of a run. **Use `workers: 1`** for memory runs, because order matters.
- Providers: `experiments/configs/providers_groq.yaml` and `configs/baseline_medqa_groq.yaml` (Groq free tier; extra keys `GROQ_API_KEY_2`, `GROQ_API_KEY_3` rotate automatically).

## Build (files you own; only edit these)
1. `src/medsim/memory/embed.py`: text similarity for kNN. Use TF-IDF cosine, in pure Python or with scikit-learn (add it to `requirements.txt` and `pyproject.toml`), instead of the paper's CLIP embeddings. Record that change and the reason (no GPU, text-only cases) for the README.
2. `src/medsim/memory/stores.py`:
   - `UngatedMemory` (MedAgentSim-style). It stores correctly diagnosed cases as medical records (summary from `case.objective` plus the key findings, with the diagnosis). It also stores a lesson from every case, including wrong ones, plus any externally submitted lesson, **without verification**.
   - `GatedMemory` (G3). A lesson or record is written only if (a) the outcome is confirmed against the ground truth (`result.verdict.correct`), and (b) no `safety_verdicts` entry is a VETO.
   - **Provenance:** every entry carries its source case, timestamp and status.
   - **Retraction:** `retract(entry_id)`, plus automatic retraction when a retrieved entry was followed by a wrong diagnosis twice. Keep an audit log of the writes, rejects and retractions.
   - Both expose `submit_lesson(lesson, source_case_id, claimed_diagnosis)`, the write path used for the poisoning test. The gate checks the claim against the source case's ground truth.
   - Implement both in `build_memory`.
3. `data/memory/poisoning.jsonl`: **10 wrong lessons**, each paired with **3 similar later cases** (30 cases). Take them from `data/processed/medqa_ext_test.jsonl` and `medqa_test.jsonl`, with the same or similar correct diagnosis. Each lesson states a plausible but **wrong** diagnosis for that presentation. Record the pairing and the wrong claim for each lesson.
4. `experiments/run_memory_poisoning.py`. For each of `ungated` and `gated`:
   1. **Clean run:** on the 30 paired cases, with memory pre-filled by correct records from dev cases.
   2. **Poisoned run:** submit the 10 wrong lessons, then run the same 30 cases.

   Report: accuracy clean vs poisoned and the drop in points; how many wrong lessons were admitted; how many paired cases retrieved a wrong lesson; tokens. Write `results/memory/*.json` plus `summary.csv`, and the trajectories to `logs/memory/`.
5. `experiments/run_consultation.py` plus configs in `experiments/configs/`, on a fixed MedQA test subset of **30–50 cases** (seed recorded; never tuned on test):
   - **E1 baseline:** all features off. This is the Phase 1 system.
   - **E2 MedAgentSim reproduction:** `memory: ungated, cot: true`. The paper's multi-doctor majority-vote ensembling is skipped for cost; record this.
   - **E3 prep:** `memory: gated, cot: true`. Aleeza adds the Safety Officer for the final E3.

   Write `results/consultation/<config>_seed<k>.json` and `summary.csv`, with accuracy (mean ± std), average turns and tests, tokens, latency and failure rate.
6. `tests/test_memory.py`: offline tests covering TF-IDF retrieval order, gate admit/reject, retraction and provenance, using `MockProvider` where an episode is needed. `pytest -q` must pass.
7. `docs/devlog_G3.md`: one row per iteration: date, the failure you observed, the change, and the measured effect on **dev**.

## Rules
- Do not edit files outside the list above. If you need a change in shared code (`contracts.py`, `episode_runner.py`, `pipeline.py`, `agents/`), message Aleeza instead. The prompt text in `agents/prompts.py` (`DOCTOR_COT`, `DOCTOR_MEMORY`) may be tuned, but **only using dev results**.
- Never commit `.env` or API keys. Use your own Groq key in `.env`.
- Commit small, meaningful steps **from your own GitHub account** (embeddings → stores → gate/retraction → poisoning set → experiments). Run `git pull --rebase` before each push.
- Budget: about 8K tokens per consultation case. Poisoning: 30 cases × 2 memories × 2 conditions ≈ 1M tokens; consultation 40 cases × 3 configs ≈ 1M per seed. Start with 1 seed; add seeds only if quota remains. **Record what you actually ran.**
- The paper is written by us, not the assistant. The assistant writes code, data and results only.
