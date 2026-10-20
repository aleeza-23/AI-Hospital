# Phase 1 Results: Multi-Agent Clinic baseline

Baseline = the paper's **Multi-Agent Clinic** (doctor + patient + measurement agents; no memory,
CoT or ensembling). Run: `runs/baseline_medqa_gptoss120b-20261020-192932`
(config: [configs/baseline_medqa_groq.yaml](../configs/baseline_medqa_groq.yaml)).
A committed copy of the run is in [runs/sample/baseline_medqa_gptoss120b_35cases](../runs/sample/baseline_medqa_gptoss120b_35cases).

> **Status: initial results on 35 / 107 MedQA cases.** The run stopped at Groq's free-tier
> daily token limit for gpt-oss-120b (~200K tokens/day; this baseline uses ~4.5K of them per case).
> Continue with:
> `python scripts/run_baseline.py --config configs/baseline_medqa_groq.yaml --resume runs/baseline_medqa_gptoss120b-20261020-192932`

## Setup

| Item | Value |
|---|---|
| Dataset | AgentClinic MedQA (`data/processed/medqa.jsonl`), cases medqa-0000 … medqa-0034 |
| Doctor | `openai/gpt-oss-120b` (Groq), reasoning effort low, temperature 0.2 |
| Patient, measurement | `openai/gpt-oss-20b` (Groq), reasoning effort low |
| Judge | `openai/gpt-oss-120b`, yes/no "same disease?" |
| Max interactions | 20 per case; one action (question / test / diagnosis) per turn |

## Accuracy

| Doctor model | MedQA cases | Accuracy | 95% CI (Wilson) |
|---|---|---|---|
| gpt-oss-120b (ours, Multi-Agent Clinic) | 35 | **74.3%** (26/35) | 57.9% – 85.8% |
| *Paper, Multi-Agent Clinic, LLaMA 3.3 70B* | 106 | *54.7%* | |
| *Paper, Multi-Agent Clinic, GPT-4o* | 106 | *52.8%* | |
| *Paper, MedAgentSim (full), LLaMA 3.3 70B* | 106 | *70.8%* | |

Not directly comparable with the paper: different doctor model (Groq no longer serves
LLaMA 3.3 70B), different patient/measurement models, and only the first 35 cases so far.

## Interaction statistics

| Metric | Value |
|---|---|
| Avg doctor turns per case | 6.5 |
| Avg tests requested per case | 3.2 |
| Timeout rate (no diagnosis in 20 turns) | 0% |
| Error rate | 0% |
| Tokens per case (all agents) | ~8.0K (220K prompt + 59K completion over 35 cases) |
| LLM calls per case | doctor 6.5, patient 2.3, measurement 3.2, judge 1 |

## Error analysis (9 incorrect cases)

| Case | Doctor's diagnosis | Correct diagnosis | Pattern |
|---|---|---|---|
| medqa-0004 | Renal abscess | Acute interstitial nephritis | wrong renal etiology |
| medqa-0005 | Medial tibial stress syndrome | Pes anserine bursitis | anatomic localisation |
| medqa-0008 | Fibroadenoma | Phyllodes tumor | look-alike lesion, premature (2 tests) |
| medqa-0010 | Rectal adenocarcinoma | Hemorrhoids | over-calling malignancy |
| medqa-0013 | Jejunal atresia | Hirschsprung's disease | look-alike neonatal obstruction |
| medqa-0015 | Branchial cleft cyst | Epidermoid cyst | look-alike lesion |
| medqa-0021 | Septic arthritis | Reactive arthritis | missed history (infection trigger) |
| medqa-0028 | Addison's disease | Lung cancer | named the syndrome, not the underlying cause |
| medqa-0034 | Viral exanthem | Erythema infectiosum | too generic |

Observations:
- Most errors are clinically close "look-alike" diagnoses or generic answers, made after few
  tests: the gap the paper's memory + CoT + ensembling (Phase 2) is meant to close.
- The judge accepted reasonable subtypes/synonyms (e.g. "Micturition syncope" vs "Situational
  syncope", "Congenital rubella syndrome" vs "Congenital Rubella Infection"), and all 9 cases
  above look genuinely wrong. A formal audit is pending: fill `human_correct` in
  `runs/baseline_medqa_gptoss120b-20261020-192932/judge_review.csv` (30 sampled cases).
- gpt-oss on Groq often packs several actions into one reply and leaks reasoning fragments
  ("We have completed the interaction"). The environment reduces each reply to one action and
  keeps the original in `Message.raw`. Leaked text after a diagnosis did not affect grading.
- Fixes made during pilot runs: one action per turn (the doctor was batching questions), earliest
  action wins (tests were being skipped when listed before a diagnosis), and test-name matching by
  meaning in the measurement agent (it was returning "normal" for recorded tests).
