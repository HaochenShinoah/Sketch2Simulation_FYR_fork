# Sketch2Simulation (OpenAI API fork)

[![Lint](https://github.com/HaochenShinoah/Sketch2Simulation_FYR_fork/actions/workflows/lint.yml/badge.svg)](https://github.com/HaochenShinoah/Sketch2Simulation_FYR_fork/actions/workflows/lint.yml)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Sketch2Simulation converts a process sketch image into an executable Aspen HYSYS Python script through a multi-agent pipeline (Bahamdan, Pajak, Hedengren, del Rio Chanona, *Sketch2Simulation: Automating Flowsheet Generation via Multi Agent Large Language Models*, arXiv:2603.24629, 2026).

This fork of [OptiMaL-PSE-Lab/Sketch2Simulation](https://github.com/OptiMaL-PSE-Lab/Sketch2Simulation) differs from the original in two ways:

1. **Every model call goes through the OpenAI API.** The original mixed Ollama-cloud (Gemini 3 Flash) with locally served Qwen models and a local sentence-transformer for retrieval. Here one client layer (`sketch2sim/llm.py`) serves all seven agents and the RAG embeddings, so the pipeline runs on an ordinary laptop with an API key and no GPU.
2. **The two agent instruction files are included.** `instantiation_instructions_simple_v5.txt` and `configuration_instructions_simple_v5.txt` were withheld from the original release; this fork ships recreated versions (see [Instruction files](#instruction-files) for how they were built and what is and is not verified).

> **Prerequisites:** a licensed installation of **Aspen HYSYS** with COM access (Windows) is needed for the execution stage (Step 9). Steps 1–8 run anywhere and can be stopped before execution (`execute=False`) to inspect the generated script.

---

## Pipeline overview

| Step | Agent / module | What it does | Backed by |
|------|----------------|--------------|-----------|
| 1 | Descriptor | Describes the process in the sketch | vision LLM |
| 2 | Description Validator | Flags repetition, contradictions, nonsense | LLM (JSON) |
| 3 | Extractor | Description (+ sketch) → JSON intermediate representation | vision LLM, Structured Outputs |
| 4A | Mixing-points normalizer | Inserts explicit mixers in front of single-inlet units that receive several streams | rules |
| 4B | Coupling normalizer | Folds condenser / reflux drum / reflux pump / reboiler into their column (runs before the tees are inserted, so the reflux return still reaches the column) | rules |
| 4C | Splitting-points normalizer | Inserts explicit tees after single-outlet units or vessel ports that feed several streams | rules |
| 5 | Basis Agent | Case name, property package, HYSYS component list | LLM + RAG (OpenAI embeddings) |
| 6 | Instantiation Agent | Unit operations and material streams (template Steps 4–5) | LLM + instruction file |
| 7 | Unit IN/OUT view | Unit-centric view of the stream graph | rules |
| 8 | Configuration Agent | Connects streams to unit ports (template Step 6) | LLM + instruction file |
| 9 | Executor + Fixer | Runs the script against HYSYS; on failure the Fixer repairs it from the runtime log (3 retries) | HYSYS COM + LLM |

The orchestration lives in `sketch2sim/pipeline.py` (`PipelineConfig` / `run_pipeline`); each agent is a one-node LangGraph graph as in the original.

---

## Setup

### 1. Install

```bash
git clone https://github.com/HaochenShinoah/Sketch2Simulation_FYR_fork.git
cd Sketch2Simulation_FYR_fork
python -m venv .venv
.venv\Scripts\activate            # Windows   (source .venv/bin/activate elsewhere)
pip install -e ".[dev,notebook]"  # or: pip install -r requirements.txt
```

`pywin32` is installed automatically on Windows only. Nothing else is platform specific.

### 2. Configure the API key

```bash
# Windows (PowerShell): setx OPENAI_API_KEY "sk-..."   then open a new terminal
export OPENAI_API_KEY=sk-...
```

A `.env` file in the repository root (`OPENAI_API_KEY=sk-...`, one `KEY=value` per line) is read automatically; it is git-ignored.

Optional variables (all have defaults, see `sketch2sim/config.py`):

| Variable | Purpose | Default |
|----------|---------|---------|
| `S2S_DESCRIPTOR_MODEL`, `S2S_EXTRACTOR_MODEL` | vision models for Steps 1 and 3 | `gpt-5.6-terra` |
| `S2S_DESCRIPTION_VALIDATOR_MODEL` | Step 2 | `gpt-5.6-luna` |
| `S2S_BASIS_MODEL`, `S2S_INSTANTIATION_MODEL` | code agents (simpler) | `gpt-5.6-terra` |
| `S2S_CONFIGURATION_MODEL`, `S2S_FIXER_MODEL` | code agents (harder) | `gpt-5.6-sol` |
| `S2S_EMBEDDING_MODEL` | RAG embeddings | `text-embedding-3-small` |
| `S2S_LLM_REASONING_EFFORT` | reasoning effort for reasoning-capable models (`none` … `high`) | `medium` |
| `S2S_LLM_TEMPERATURE`, `S2S_LLM_SEED` | deterministic decoding of the paper (dropped automatically where a model rejects them) | `0.0`, `42` |
| `S2S_OPENAI_API_MODE` | `responses` (default) or `chat` for OpenAI-compatible gateways | `responses` |
| `S2S_OPENAI_STORE` | `1` lets the Responses API keep responses server-side for 30 days | `0` |
| `OPENAI_BASE_URL`, `OPENAI_ORG_ID`, `OPENAI_PROJECT_ID` | alternative endpoint / org / project | unset |
| `S2S_*_MAX_OUTPUT_TOKENS` | per-agent output caps | 4k–12k |
| `S2S_EXECUTOR_TIMEOUT`, `S2S_PYTHON_EXECUTABLE` | HYSYS script timeout (s) and interpreter | `600`, current interpreter |
| `S2S_HSC_DIR` | where the generated script saves the `.hsc` | `outputs/` |
| `S2S_STRICT_CONNECTIONS` | `0` → missing stream connections do not fail the script | `1` |

### 3. Aspen HYSYS (execution stage only)

Install HYSYS, open it once interactively, and check that `python -c "from win32com.client import Dispatch; Dispatch('HYSYS.Application')"` opens it from the project environment. Run the pipeline with HYSYS closed.

---

## How to run

**Notebook:** open `run_workflow.ipynb` from the repository root, pick the kernel of the virtual environment, edit the `PipelineConfig` cell (image, models, `execute`) and run all cells.

**Command line:**

```bash
python -m sketch2sim.pipeline images/cs1.png                 # full run incl. HYSYS
python -m sketch2sim.pipeline images/cs3.png --no-execute    # stop after the merged script
python -m sketch2sim.pipeline images/cs2.png --text-only-extractor   # extractor sees text only (release-code behaviour)
```

Outputs:

- `logs/step*_…` – every intermediate artefact (description, validation, extraction, normalised structures, unit view, prompts, raw model outputs)
- `logs/llm_calls.jsonl` – one line per model call (model, tokens, duration, parameters dropped)
- `outputs/hysys_basis_*.py`, `hysys_instantiation_*.py`, `hysys_configuration_*.py`, `outputs/hysys_combined_latest.py` – generated scripts
- `outputs/<case name>.hsc` – the HYSYS case written by the script

"Success" means the generated script built and saved the case with every listed stream connected (exit code 0). As in the paper, the case is built, not solved.

---

## Instruction files

`sketch2sim/instructions/` contains three files the code agents work with:

- `hysys_template_base.py` – the script skeleton with the Step 1–7 markers. Compared with the original it has a helper block (Step 3b) that the agents call instead of writing raw COM calls: `add_unit`, `add_stream`, `connect`, `connect_column_feed`, `connect_column_product`. Each helper tries an ordered list of HYSYS type strings (`TYPES`) or port members (`PORTS`), logs what worked, and reports what did not; `finish_connections()` makes the script exit with code 2 when a listed connection could not be made, which triggers the Fixer with the exact COM error texts.
- `instantiation_instructions_simple_v5.txt` – how to map sketch equipment to the `TYPES` categories, naming rules, what not to do, worked examples (case studies 1 and 4).
- `configuration_instructions_simple_v5.txt` – port roles per unit category, outlet roles from `connect_point`, column feed/product rules, worked examples.

**How verified these are.** The original files were written against a live HYSYS V11; this fork was built without HYSYS access, from the paper, the released code, and public HYSYS automation code (notably the `aspen-pysys` package and the CAChemE column scripts). Verified from working code (including two scripts run against HYSYS V14): the type strings `pumpop valveop mixerop teeop heaterop coolerop heatexop compressor flashtank dynamicfiredheaterop conversionreactorop distillation absorber shdistop fractop recycle adjust setop spreadsheetop` (`flashtank` is the 2-phase separator; `separatorop` does not exist on V14), the property-package name `"PengRob"`, the single-port members `FeedStream / ProductStream / EnergyStream` (assign the stream object), the exchanger members `TubeSideFeed / TubeSideProduct / ShellSideFeed / ShellSideProduct`, the column sub-flowsheet (`ColumnFlowsheet.Operations` → `Main Tower` / `Main TS`, `Condenser`, `Reboiler`; `SpecifyFeedLocation`, `AddFeedStream`, `NumberOfTrays`, `AddDrawStream`) and holding the solver (`case.Solver.CanSolve = False`) while the flowsheet is built. Not verified (candidates tried in order): 3-phase separator / tank / expander / reactor-variant type strings, `Feeds.Add` (present in one V14 environment, absent in another), `VapourProduct / LiquidProduct`, the column product members, the argument order of `Operations.Add` (all three shapes are tried) and side-draw attachment (which the paper also reports as unsupported).

The evidence behind every type string and port member, with sources and confidence, is in [`docs/HYSYS_COM_FACTS.md`](docs/HYSYS_COM_FACTS.md).

**Pinning the facts on the HYSYS laptop.** Run once:

```bash
python tools/hysys_discover_com_api.py
```

It creates a scratch case, tries every candidate type string, lists the stream-port members of every object HYSYS creates, probes the connection members with real streams and writes `logs/hysys_com_discovery.{json,md}`. Use the report to trim `TYPES` / `PORTS` in the template and, if you like, to mark the corresponding lines of the instruction files as verified.

---

## Models

Defaults are set in `sketch2sim/config.py` and can be overridden per agent through the environment. The original mapping is kept for reference.

| Agent | Original (paper) | This fork (default) |
|-------|------------------|---------------------|
| Descriptor | Gemini 3 Flash (Ollama cloud) | `gpt-5.6-terra` |
| Description Validator | Qwen3-VL 8B (local) | `gpt-5.6-luna` |
| Extractor | Gemini 3 Flash (Ollama cloud) | `gpt-5.6-terra` |
| Basis / Instantiation | Qwen2.5-Coder 7B (local) | `gpt-5.6-terra` |
| Configuration / Fixer | Qwen3-Coder 30B (local) | `gpt-5.6-sol` |
| RAG embeddings | all-MiniLM-L6-v2 (local) | `text-embedding-3-small` |

Decoding follows the paper where the API allows it (temperature 0, seed 42 in Chat Completions mode). Reasoning models accept sampling parameters only with reasoning effort `none`, so with the default effort (`medium`) the temperature is not sent; any parameter a model rejects is dropped for that model and recorded in `logs/llm_calls.jsonl`, so every run stays auditable. The GPT-5.6 ids were current in October 2026; check the OpenAI model page when a model is retired (`gpt-5`, `gpt-5-mini` and `gpt-5-nano` shut down on 2026-12-11) and override with the `S2S_*_MODEL` variables.

---

## Project structure

```text
.
├── run_workflow.ipynb                  # notebook entry point (PipelineConfig + run_pipeline)
├── requirements.txt / pyproject.toml   # dependencies, ruff, pytest; `pip install -e .`
├── images/                             # case-study sketches cs1–cs4
├── tools/hysys_discover_com_api.py     # one-off HYSYS COM probe (Windows)
├── docs/HYSYS_COM_FACTS.md             # sources behind the instruction files
├── tests/                              # offline test-suite (fake LLM, fake COM); no API key needed
└── sketch2sim/
    ├── config.py                       # models, decoding, timeouts (env-driven)
    ├── llm.py                          # the only module that talks to the OpenAI API
    ├── pipeline.py                     # the nine steps; CLI `python -m sketch2sim.pipeline`
    ├── agents/                         # descriptor, validator, extractor, basis, instantiation,
    │                                   # configuration, fixer, executor
    ├── mixing_points_normalizer.py, splitting_points_normalizer.py, coupling_normalizer.py,
    │   merge_simulation_scripts.py
    ├── utils/                          # orchestration, logging, unit-view helpers
    ├── RAG/                            # components_list.xlsx, mixture_recipes.txt, embedding cache
    └── instructions/                   # hysys_template_base.py + the two instruction files
```

## Development

```bash
ruff check sketch2sim/ tests/ tools/ && ruff format --check sketch2sim/ tests/ tools/
pytest -q      # runs the whole pipeline on case study 1 with a fake model, and executes the generated script against a fake COM layer
```

## Status

Research/prototype workflow, ported to the OpenAI API. Steps 1–8 are exercised offline by the tests; Step 9 and the exact HYSYS COM member names must be confirmed on a machine with Aspen HYSYS (see `tools/hysys_discover_com_api.py`).
