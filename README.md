# Porter Dispatch System — AI-Driven Hospital Porter Logistics

A hybrid AI system for hospital porter dispatch, integrating an LLM natural language interface with an OR-Tools VRPTW (Vehicle Routing Problem with Time Windows) solver. Built for HHH (a Hong Kong hospital) as part of FYP Project 16.

**KPI target:** Every porter task completed within 15 minutes.

## Table of Contents
- [Features](#features)
- [Project Structure](#project-structure)
- [Installation & Setup](#installation--setup)
- [Usage](#usage)
- [Running the Simulation](#running-the-simulation)
- [Technical Architecture](#technical-architecture)
- [API Reference](#api-reference)

---

## Features

- **Natural Language Dispatch:** Accepts free-text requests (e.g., "urgent patient 7H to 3FXRAY") parsed by DeepSeek LLM into structured task JSON
- **OR-Tools VRPTW Solver:** Global optimization across all porters and pending tasks simultaneously, with 15-min KPI soft time windows and pickup-delivery modeling; falls back to greedy if OR-Tools is unavailable
- **Rolling-Horizon Re-optimization:** Re-solves on every new task arrival and porter completion event
- **Task Batching:** Solver assigns multiple sequential tasks to a single porter's route
- **LLM Dispatch Explanation:** After assignment, the LLM generates a human-readable rationale ("Why this porter?")
- **Policy Advisor (RAG):** Retrieves similar historical tasks from DATA2024.xlsx and uses LLM to assess KPI risk, generate policy suggestions, and answer natural language questions about operations
- **Real-time Dashboard:** Flask + Tailwind CSS UI showing porter status, queue length, assignment results, and advisor panel
- **Simulation Harness:** Discrete-event simulation comparing greedy vs OR-Tools across fleet sizes (3–10 porters), with historical replay and synthetic Poisson arrival modes

---

## Project Structure

```text
porter-dispatch-prototype/
├── app.py                  # Flask web server + API endpoints
├── porter_prototype.py     # Core dispatch logic (Porter, PorterDispatchSystem, LLM)
├── solver.py               # OR-Tools VRPTW solver + greedy fallback
├── advisor.py              # LLM Policy Advisor with historical RAG
├── simulation.py           # Discrete-event simulation harness
├── static/
│   └── index.html          # Frontend dashboard (Tailwind CSS + vanilla JS)
├── requirements.txt        # Python dependencies
├── .env                    # API key (gitignored)
└── README.md               # This file

../
├── DATA2024.xlsx           # Historical HHH porter task data (2024)
├── travel_times.xlsx       # 89×89 hospital travel time matrix
├── TASKS.md                # Completed tasks and backlog
└── CLAUDE.md               # Claude Code project instructions
```

---

## Installation & Setup

```bash
cd porter-dispatch-prototype

# Create and activate virtual environment (optional but recommended)
python -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install flask openai python-dotenv pandas openpyxl ortools

# Set your DeepSeek API key (or add to .env file)
echo "OPENAI_API_KEY=your-deepseek-key-here" > .env
```


---

## Usage

### Web Interface

```bash
cd porter-dispatch-prototype
python app.py
# Open http://localhost:5000
```

The dashboard lets you:
- Type or click a quick-task to dispatch a porter via natural language
- See real-time porter status (available/busy, countdown until free)
- Mark tasks complete manually for testing
- Get policy suggestions and ask operational questions to the advisor

### Standalone (no web server)

```bash
python porter-dispatch-prototype/porter_prototype.py
```

---

## Running the Simulation

Compares greedy vs OR-Tools across fleet sizes on historical and/or synthetic data:

```bash
cd porter-dispatch-prototype

# Run both replay and synthetic modes, fleet sizes 3/5/7
python simulation.py --mode both --tasks 100 --porters 3 5 7 --interval 8.0

# Replay mode only (uses DATA2024.xlsx)
python simulation.py --mode replay --tasks 200 --porters 3 5

# Synthetic mode only (Poisson arrivals)
python simulation.py --mode synthetic --tasks 150 --interval 10.0
```

Output is written to `simulation_results.csv`.

**Key findings from simulation:**
- 76.1% of real HHH tasks historically violate the 15-min KPI (mean 50.5 min)
- OR-Tools improves mean task duration by 2–30% over greedy
- Larger gains observed with more porters and synthetic data

---

## Technical Architecture

```
User Request (Natural Language)
    ↓
DeepSeek LLM → Structured Task JSON
    ↓
OR-Tools VRPTW Solver
    ├─ Models each task as pickup-delivery node pair
    ├─ 15-min KPI soft time windows
    ├─ Rolling-horizon re-optimization
    └─ Falls back to greedy if OR-Tools unavailable
    ↓
Porter State Update (busy) + Auto-timer
    ↓
Timer expires → Porter → available → Queue drained
    ↓
LLM generates dispatch explanation
    ↓
Policy Advisor (RAG over DATA2024.xlsx)
    ├─ KPI risk assessment (Low/Medium/High)
    ├─ Policy tuning suggestions
    └─ Natural language Q&A
```

**Travel times:** 89×89 matrix from `travel_times.xlsx`. Floor-based fallback (5 min same floor, 8 min inter-floor) for unknown pairs; p90 global estimate (27.5 min) for fully unrecognized locations.

**LLM backend:** DeepSeek API (`https://api.deepseek.com`, model `deepseek-chat`) via OpenAI-compatible client. API key set via `OPENAI_API_KEY` environment variable or `.env` file.

**All state is in-memory** — resets on restart. No database.

---

## Code Reference

### [`solver.py`](solver.py)
The OR-Tools VRPTW model. Completely standalone — no Flask, no LLM.
- **Lines 1–21** — OR-Tools import with greedy fallback flag
- **Lines 23–80** — `PorterDispatchSolver` init: KPI limit, soft penalty, drop penalty, time limit
- **Lines 82–130** — `_solve_greedy()`: nearest-porter fallback algorithm
- **Lines 132–280** — `_solve_ortools()`: full VRPTW model — node layout, time dimension, pickup-delivery pairs, capacity constraint, penalties, solver call
- **Lines 282–300** — `assign_tasks()`: public entry point, selects strategy

### [`porter_prototype.py`](porter_prototype.py)
Core dispatch system. Owns porter state and orchestrates the LLM + solver pipeline.
- **`ChatGPTLLM`** — wraps DeepSeek API; parses natural language → structured task JSON
- **`PorterDispatchSystem.__init__()`** — creates porter fleet, loads travel matrix, initializes solver
- **`_create_fleet()`** — builds N porters at round-robin locations from the travel matrix
- **`dispatch_task()`** — receives raw text → calls LLM → calls solver → updates porter state
- **`dispatch_structured_task()`** — receives pre-parsed JSON → calls solver directly (used by simulation)
- **`explain_dispatch()`** — calls LLM to generate "why this porter" rationale
- **`_drain_queue()`** — re-runs solver when a porter becomes free (rolling-horizon)
- **`TRAVEL_TIME_MATRIX`** — travel times between all hospital departments (from `travel_times.xlsx`)
- **`SERVICE_TASK_TIMES`** — estimated durations per service type

### [`advisor.py`](advisor.py)
Historical data analysis and LLM-powered policy advice.
- **`HistoricalTaskStore._load()`** — reads `DATA2024.xlsx`, parses timestamps, computes actual durations
- **`HistoricalTaskStore.find_similar()`** — scores historical tasks by service/location/time-of-day match
- **`HistoricalTaskStore.get_performance_summary()`** — computes KPI violation rate (76.1%), mean duration, per-service breakdowns
- **`PolicyAdvisor.assess_kpi_risk()`** — heuristic risk score (Low/Medium/High) + LLM reasoning for a specific incoming task
- **`PolicyAdvisor.get_suggestions()`** — full dataset analysis + LLM-generated policy suggestions
- **`PolicyAdvisor.ask()`** — free-text Q&A with performance data as LLM context

### [`simulation.py`](simulation.py)
Offline discrete-event simulation for benchmarking. No web server involved.
- **`load_historical_tasks()`** — reads `DATA2024.xlsx`, returns sorted task list for replay
- **`generate_synthetic_tasks()`** — Poisson inter-arrivals, weighted service type distribution
- **`run_simulation()`** — event queue loop: task arrivals → solver assignment → porter-free events
- **`compute_metrics()`** — mean/P95/max duration, KPI violation rate, mean wait time
- **`main()`** — CLI argument parsing, runs both strategies, prints comparison table, saves CSV

### [`app.py`](app.py)
Flask web server. Glues all components together and exposes REST endpoints.
- Initializes `PorterDispatchSystem` and `PolicyAdvisor` on startup
- Routes: `/dispatch`, `/status`, `/complete/<porter_id>`, `/advisor/risk`, `/advisor/suggestions`, `/advisor/ask`

### [`static/index.html`](static/index.html)
Entire frontend in one file — vanilla JS, Tailwind CSS.
- Left panel: porter status cards, polls `GET /status` every 3 seconds
- Right panel: task input box + quick-action buttons → `POST /dispatch`
- Explanation panel: "Why this porter?" LLM rationale
- Policy Advisor section: suggestions button + free-text ask box

---

## API Reference

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/` | Serves the frontend dashboard |
| `POST` | `/dispatch` | Dispatch a task (body: `{"request": "..."}`) |
| `GET` | `/status` | Current porter states and queue length |
| `POST` | `/complete/<porter_id>` | Manually mark a task complete (testing) |
| `POST` | `/advisor/risk` | KPI risk assessment for a task |
| `GET` | `/advisor/suggestions` | LLM policy tuning suggestions |
| `POST` | `/advisor/ask` | Natural language Q&A (body: `{"question": "..."}`) |

### `/dispatch` response example

```json
{
  "status": "Task Assigned",
  "porter_id": "P-001",
  "porter_location": "7H",
  "task": {"service": "送病人", "from": "7H", "to": "3FXRAY", "priority": "Urgent"},
  "estimated_duration_mins": 12.3,
  "explanation": "P-001 was chosen because they are nearest to 7H (3 min travel) and currently available, while P-002 is busy with another task."
}
```
