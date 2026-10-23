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
| 4A | Mixing-points normalizer | Inserts explicit mixers in front of single-inlet units that receive several streams; recognises fired heaters (by name, or a heater with a flue gas out or a fuel stream in, which tags the unit "fired heater"), keeps their burner streams out of the process pass and merges fuel + air into one burner feed through `MIXER_TO_<unit>_BURNER`; a heater or cooler drawn with two inlets and two outlets is tagged "heat exchanger" and only the inlets that no outlet continues are mixed; heat-duty labels drawn as streams (Q-trim, Q-101) are dropped | rules |
| 4B | Coupling normalizer | Folds condenser / reflux drum / reflux pump / reboiler into their column (runs before the tees are inserted, so the reflux return still reaches the column); drops the utility streams of the folded units (cooling water, steam) and the internal reflux line of the column, listed under `removed_streams` | rules |
| 4C | Splitting-points normalizer | Inserts explicit tees after single-outlet units or vessel ports that feed several streams; the flue gas of a fired heater is never teed | rules |
| 5 | Basis Agent | Case name, property package, HYSYS component list | LLM + RAG (OpenAI embeddings) |
| 6 | Instantiation Agent | Unit operations and material streams (template Steps 4–5) | LLM + instruction file |
| 7 | Unit IN/OUT view | Unit-centric view of the stream graph | rules |
| 8 | Configuration Agent | Connects streams to unit ports (template Step 6) | LLM + instruction file |
| 9 | Executor + Fixer | Runs the script against HYSYS, one HYSYS instance per attempt (every attempt quits its instance; set `S2S_HYSYS_KEEP_OPEN=1` to keep a successful one open); on failure the Fixer repairs Steps 2–6 from the runtime log (3 retries) | HYSYS COM + LLM |

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
| `S2S_DESCRIPTOR_MODEL`, `S2S_EXTRACTOR_MODEL` | vision models for Steps 1 and 3 | `gpt-6-astra` |
| `S2S_DESCRIPTION_VALIDATOR_MODEL` | Step 2 | `gpt-6-astra` |
| `S2S_BASIS_MODEL`, `S2S_INSTANTIATION_MODEL` | code agents (simpler) | `gpt-6-astra` |
| `S2S_CONFIGURATION_MODEL`, `S2S_FIXER_MODEL` | code agents (harder) | `gpt-6-astra` |
| `S2S_EMBEDDING_MODEL` | RAG embeddings | `text-embedding-3-small` |
| `S2S_LLM_REASONING_EFFORT` | reasoning effort for reasoning-capable models (`none`, `minimal`, `low`, `medium`, `high`, `xhigh`, `max`); a level the model rejects is retried one level lower and the change is logged | `max` |
| `S2S_LLM_TEMPERATURE`, `S2S_LLM_SEED` | deterministic decoding of the paper (dropped automatically where a model rejects them) | `0.0`, `42` |
| `S2S_OPENAI_API_MODE` | `responses` (default) or `chat` for OpenAI-compatible gateways | `responses` |
| `S2S_OPENAI_STORE` | `1` lets the Responses API keep responses server-side for 30 days | `0` |
| `OPENAI_BASE_URL`, `OPENAI_ORG_ID`, `OPENAI_PROJECT_ID` | alternative endpoint / org / project | unset |
| `S2S_*_MAX_OUTPUT_TOKENS` | per-agent output caps (reasoning tokens count against them, so they are sized for effort `max`); a cap above the model's maximum is clamped to the maximum the API reports and the change is logged | 16k–96k |
| `S2S_LLM_TIMEOUT`, `S2S_LLM_MAX_RETRIES` | per-request timeout (s) and SDK retries on transient errors | `3600`, `3` |
| `S2S_EXECUTOR_TIMEOUT`, `S2S_PYTHON_EXECUTABLE` | HYSYS script timeout (s) and interpreter | `600`, current interpreter |
| `S2S_HSC_DIR` | where the generated script saves the `.hsc` | `outputs/` |
| `S2S_HSC_SUFFIX` | suffix of the case file of one attempt (`_attemptN`); set by the executor, not by hand | unset |
| `S2S_HYSYS_KEEP_OPEN` | `0` quits every HYSYS instance the scripts start (open the saved `.hsc` to look at the flowsheet); `1` keeps the instance of a successful run open, which must then be closed before the next run (a script started next to an open HYSYS attaches to it instead of starting its own process, and `SimulationCases.Add` fails) | `0` |
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
- `outputs/<case name>_attemptN.hsc` and `outputs/<case name>_attemptN_topology.json` – the case each attempt saved and the per-unit feeds/products read back from it; the successful attempt is copied to `outputs/<case name>.hsc`

"Success" means the generated script built and saved the case with every listed stream attached, verified by reading the unit ports back (`AttachedFeeds` / `AttachedProducts`) after each connection and again before saving (exit code 0). A connection that HYSYS accepts without attaching anything counts as a failure, and the topology JSON lets a run be audited without opening HYSYS. As in the paper, the case is built, not solved.

**What HYSYS still reports.** A built case still shows "Required Info" messages: energy streams, reaction sets, column specifications and stream conditions (temperature, pressure, flow, composition) are not part of this pipeline, so pumps ask for an energy stream, reactors for a reaction set, columns for specifications and feed streams for conditions. This is the expected state of a case that is built, not specified; it does not mean a connection is missing.

---

## Instruction files

`sketch2sim/instructions/` contains three files the code agents work with:

- `hysys_template_base.py` – the script skeleton with the Step 1–7 markers. Compared with the original it has a helper block (Step 3b) that the agents call instead of writing raw COM calls: `add_unit`, `add_stream`, `connect`, `connect_energy`, `connect_column_feed`, `connect_column_product`, `skip`. `TYPES` holds one verified HYSYS type string per equipment category and `PORTS` one verified port member per connection role; `add_unit` loads the column template (`LoadColumnTemplate`) right after creating a column, and every helper reads the attachment back from the unit's `AttachedFeeds` / `AttachedProducts` and logs it. `finish_connections()` re-checks every attachment and makes the script exit with code 2 when a listed connection could not be made, which triggers the Fixer with the exact COM error texts. The file head and Step 1 (HYSYS start-up with its retry and PID log), Step 3b and Step 7 (summary, save, exit code) are owned by the template: the pipeline re-inserts them from the basis output (a verbatim copy of the template with the case name filled in) before every execution, so the Fixer can only change Steps 2–6 of the agents' code.
- `instantiation_instructions_simple_v5.txt` – how to map sketch equipment to the `TYPES` categories, naming rules, what not to do, worked examples (case studies 1 and 4).
- `configuration_instructions_simple_v5.txt` – port roles per unit category, outlet roles from `connect_point`, column feed/product rules, worked examples.

**How verified these are.** The original files were written against a live HYSYS V11; this fork's versions were first recreated from the paper, the released code and public HYSYS automation code, and then **verified live on Aspen HYSYS V11 on 2026-10-22**: a discovery run over every template category and port member (`logs/hysys_com_discovery.md`) plus a series of column, fired-heater, separator and lifecycle experiments whose results are recorded, with the negative results, in [`docs/HYSYS_COM_FACTS.md`](docs/HYSYS_COM_FACTS.md). Every type string in `TYPES` and every port member in `PORTS` creates or attaches what it claims on V11, including the ones that were guesses before: `sep3op` (3-phase separator), `kineticreactorop` / `pfreactorop` (CSTR / PFR), `threephase` / `liqliqextractor` (3-phase column / extractor), the fired-heater members `RadInlet.Add / RadOutlet.Add / CombustionProduct / BurnerFeed`, the column recipe (`Operations.Add` creates an empty shell; `LoadColumnTemplate` installs `Main Tower` / `Condenser` / `Reboiler`; feeds through `AddFeedStream` on the tray section, products through `TopVapourProduct / TopLiquidProduct / TopWaterProduct / BtmLiquidProduct`, side draws through `AddDrawStream`), and the fact that a 2-phase vessel accepts `HeavyLiquidProduct` without attaching anything, which is why the helper block verifies every attachment by reading the ports back. Still unverified: the product members of `kineticreactorop` and `pfreactorop` (`Product` on the PFR), connections on the shortcut column (`shdistop` has no `ColumnFlowsheet`), and the `rebabs` / `refabs` / `liqliqex` template names (their no-argument form was verified).

**Re-checking on another HYSYS version.** Run, with HYSYS closed:

```bash
python tools/hysys_discover_com_api.py            # writes logs/hysys_com_discovery.{json,md}
python tools/hysys_discover_com_api.py --visible  # show the HYSYS window
```

It reads `TYPES` and `PORTS` from the template, starts its own HYSYS instance (`DispatchEx`, retrying `SimulationCases.Add` while HYSYS starts), creates a scratch case, tries every candidate type string in the template's order, lists the stream-port members of every object HYSYS creates, probes every template port member with real streams and reads `AttachedFeeds` / `AttachedProducts` back after each probe, so the report distinguishes "attached" from "accepted but not attached"; columns get their template loaded and their feed, product and side-draw members probed the same way. At the end it quits its instance and waits until the process has exited. The report says per category whether the template's first choice worked; use it to edit `TYPES` / `PORTS` and the instruction files when a HYSYS version behaves differently.

---

## Models

Defaults are set in `sketch2sim/config.py` and can be overridden per agent through the environment. The original mapping is kept for reference.

| Agent | Original (paper) | This fork (default) |
|-------|------------------|---------------------|
| Descriptor | Gemini 3 Flash (Ollama cloud) | `gpt-6-astra` |
| Description Validator | Qwen3-VL 8B (local) | `gpt-6-astra` |
| Extractor | Gemini 3 Flash (Ollama cloud) | `gpt-6-astra` |
| Basis / Instantiation | Qwen2.5-Coder 7B (local) | `gpt-6-astra` |
| Configuration / Fixer | Qwen3-Coder 30B (local) | `gpt-6-astra` |
| RAG embeddings | all-MiniLM-L6-v2 (local) | `text-embedding-3-small` |

Decoding follows the paper where the API allows it (temperature 0, seed 42 in Chat Completions mode). Reasoning models accept sampling parameters only with reasoning effort `none`, so with the default effort (`max`) the temperature is not sent. The effort is the top of the API's ladder; if a model rejects a level, the call is retried one level lower (`max` → `xhigh` → `high` → …), the accepted level is remembered for that model, and the change is recorded in `logs/llm_calls.jsonl`, as is any other parameter a model rejects, so every run stays auditable. The `gpt-6-astra` id was current in October 2026 (openai SDK 3.35); check the OpenAI model page when a model is retired and override with the `S2S_*_MODEL` variables.

---

## Project structure

```text
.
├── run_workflow.ipynb                  # notebook entry point (PipelineConfig + run_pipeline)
├── requirements.txt / pyproject.toml   # dependencies, ruff, pytest; `pip install -e .`
├── images/                             # case-study sketches cs1–cs4
├── tools/hysys_discover_com_api.py     # HYSYS COM probe with read-back (Windows; re-run on a new HYSYS version)
├── docs/HYSYS_COM_FACTS.md             # COM facts verified on Aspen HYSYS V11 (2026-10-22), incl. negative results
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

Research/prototype workflow, ported to the OpenAI API. Steps 1–8 are exercised offline by the tests; Step 9 and the HYSYS COM type strings and member names were confirmed live on Aspen HYSYS V11 on 2026-10-22 (`docs/HYSYS_COM_FACTS.md`); re-run `tools/hysys_discover_com_api.py` before trusting them on another HYSYS version.
