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

> **Note:** `requirements.txt` lists `openaix` — this is a typo; install `openai`.

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
