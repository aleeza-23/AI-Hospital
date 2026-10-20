# System Architecture

Re-implementation of **MedAgentSim** (Almansoori, Kumar & Cholakkal, arXiv:2503.22678).
Phase 1 delivers the paper's baseline, **Multi-Agent Clinic** (the paper's "Multi-Agent Clinic" rows in Table 1):
doctor + patient + measurement agents, no memory / CoT / ensembling.

## Components

```
data/processed/*.jsonl ──► CaseLoader ──► Case
                                           │
                    ┌──────────────────────▼───────────────────────┐
                    │ EpisodeRunner  (src/medsim/env)              │
                    │   EpisodeState: turn, transcript, tests, ... │
                    │                                              │
                    │   DoctorAgent ── parse_doctor_action() ──┐   │
                    │      ▲            (src/medsim/tools)     │   │
                    │      │   ASK ──────────► PatientAgent ───┤   │
                    │      │   REQUEST TEST ─► MeasurementAgent┤   │
                    │      │   REQUEST IMAGES► ImageReader(VLM)┤   │
                    │      └───────────────────────────────────┘   │
                    │   DIAGNOSIS READY / turn limit ─► stop       │
                    └──────────────────────┬───────────────────────┘
                                           ▼
                       Judge (LLM yes/no) ─► EpisodeResult ─► MemoryBuffer (Phase 2; NullMemory now)
                                           ▼
                     runs/<run>/episodes.jsonl ─► compute_metrics ─► metrics.json
```

Every agent talks to an `LLMProvider` (`src/medsim/providers`):

| Provider | Use |
|---|---|
| `openai_compat` | OpenAI, Groq, Together, OpenRouter, vLLM, Ollama, LM Studio |
| `mock` | deterministic scripted agents for tests / CI / offline demo |
| `human` | Control Mode: a person plays the doctor or patient (`--human doctor`) |

## Information isolation (core design rule)

| Agent | Sees |
|---|---|
| Doctor | `Case.objective` + the dialogue only. Never the case data. |
| Patient | `Case.patient_info` + doctor questions. Never test results or tool calls. |
| Measurement | `physical_exam` + `test_results` + the single current request. |
| Judge | correct diagnosis + predicted diagnosis. |

Enforced in `agents/agents.py` (each agent builds its own message view) and tested in
`tests/test_episode.py::test_information_isolation`.

## Doctor tool interface

| Marker | Routed to | Returns |
|---|---|---|
| `REQUEST TEST: <name>` | MeasurementAgent | `RESULTS: ...` or `RESULTS: NORMAL READINGS` |
| `REQUEST IMAGES` | ImageReader (VLM) | `IMAGE REPORT: ...` |
| `DIAGNOSIS READY: <dx>` | ends episode | — |
| anything else | PatientAgent | patient reply |

On the last allowed turn the doctor is told it must diagnose; otherwise the episode is a `timeout` (counted wrong).

## Structured contracts (`src/medsim/contracts.py`)

`Case`, `Message`, `Role`, `DoctorAction`, `TestRequest`, `TestResult`, `EpisodeState`,
`EpisodeStatus`, `Usage`, `JudgeVerdict`, `EpisodeResult`, `RunMetrics`, plus `RunConfig`
(`config.py`) and `MemoryRecord` (`memory/buffer.py`). All are pydantic models; every file the
system writes is a serialized contract and can be reloaded and re-validated.

## Phase 2 extension points

| Paper component | Hook |
|---|---|
| Medical / Experience Records buffers, kNN retrieval | `memory.MemoryBuffer` (`add`, `retrieve`), injected into `EpisodeRunner` |
| Reflection on misdiagnosis | after `Judge.grade` in `EpisodeRunner.run` |
| CoT + majority-vote ensemble of doctors | replace the final `DoctorAgent` diagnosis step |
| Phaser game map (visual simulation) | consumes `Message` stream via `on_message` callback |
| Bias analysis | prompt variants in `agents/prompts.py` |
