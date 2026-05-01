# Porter Dispatch System — AI-Driven Hospital Porter Logistics

A hybrid AI system for hospital porter dispatch, integrating an LLM natural language interface with an OR-Tools VRPTW (Vehicle Routing Problem with Time Windows) solver. Built for HHH (a Hong Kong hospital) as IEDA FYP Project 16.

**KPI target:** Porter arrives at pickup location within 15 minutes of order.

## Table of Contents
- [Features](#features)
- [Project Structure](#project-structure)
- [Installation & Setup](#installation--setup)
- [Usage](#usage)
- [Running the Simulation](#running-the-simulation)
- [NLP Accuracy Testing](#nlp-accuracy-testing)
- [KPI Pickup-Time Analysis](#kpi-pickup-time-analysis)
- [Technical Architecture](#technical-architecture)
- [API Reference](#api-reference)

---

## Features

- **Natural Language Dispatch:** Free-text requests in Chinese or English, parsed by DeepSeek LLM into structured task JSON
- **OR-Tools VRPTW Solver:** Global optimisation across all porters and pending tasks; 15-min KPI soft time windows; falls back to greedy if OR-Tools unavailable
- **Rolling-Horizon Re-optimisation:** Re-solves on every task arrival and porter completion event
- **LLM Dispatch Explanation:** Generates human-readable "Why this porter?" rationale after each assignment
- **Policy Advisor (RAG):** Retrieves K=10 similar historical tasks from DATA2024.xlsx; assesses KPI risk, generates fleet suggestions, answers natural language questions
- **Scheduled Tasks:** Future-time dispatch ("at 14:30", "in 2 hours"); UI countdown timers and cancellation
- **Intermediate Stops:** Multi-waypoint routes extracted from NL and incorporated in travel-time estimates
- **Simulation Harness:** Discrete-event simulation comparing greedy vs OR-Tools, tracking both total duration and pickup response time
- **Real-time Dashboard:** Flask + Vanilla JS UI with porter status, queue, route display, advisor panel
- **NLP Accuracy Test:** Validates LLM parsing on 100 historical test samples across 5 fields

---

## Project Structure

```text
porter-dispatch-prototype/
├── app.py                     # Flask web server + REST API (9 endpoints)
├── porter_prototype.py        # Core dispatch logic (Porter, PorterDispatchSystem, LLM)
├── solver.py                  # OR-Tools VRPTW solver + greedy fallback
├── advisor.py                 # RAG policy advisor (HistoricalTaskStore + PolicyAdvisor)
├── simulation.py              # Discrete-event simulation (pickup_time tracking added)
├── nlp_accuracy_test.py       # NLP accuracy validation against DATA2024.xlsx
├── kpi_analysis.py            # Pickup-time KPI analysis across fleet sizes
├── simulation_results.csv     # Pre-run simulation output
├── nlp_accuracy_results.csv   # NLP test results (n=100)
├── kpi_analysis.md            # KPI pickup-time analysis report
├── static/
│   └── index.html             # Frontend dashboard (Vanilla JS + Tailwind CSS)
├── travel_time/
│   └── travel_times.xlsx      # 89×89 hospital travel time matrix
├── report/
│   ├── main.tex               # Final FYP report (LaTeX, 25 pages)
│   └── main.pdf               # Compiled PDF
├── slides/
│   ├── slides.tex             # Beamer presentation slides (16:9)
│   └── slides.pdf             # Compiled PDF
├── requirements.txt           # Python dependencies
├── .env                       # API key (gitignored)
└── README.md                  # This file

../
├── DATA2024.xlsx              # Historical HHH porter task data (2024, 84k rows)
├── TASKS.md                   # Completed tasks and backlog
├── demo-flow.md               # Final presentation demo script
└── CLAUDE.md                  # Claude Code project instructions
```

---

## Installation & Setup

```bash
cd porter-dispatch-prototype

# Create and activate virtual environment
python -m venv .venv
source .venv/bin/activate   # macOS/Linux
# .venv\Scripts\activate    # Windows

# Install dependencies
pip install -r requirements.txt

# Set your DeepSeek API key
echo "OPENAI_API_KEY=your-deepseek-key-here" > .env
```

---

## Usage

### Web Interface

```bash
python app.py
# Open http://localhost:5000
```

The dashboard lets you:
- Type or click a quick-task to dispatch a porter via natural language
- See real-time porter status (available/busy, countdown until free, current route)
- Schedule future tasks with time expressions ("at 14:30", "in 2 hours")
- Get KPI risk assessments and policy suggestions from the advisor panel

### Standalone (no web server)

```bash
python porter_prototype.py
```

---

## Running the Simulation

Compares greedy vs OR-Tools across fleet sizes on historical replay and synthetic data:

```bash
# Both modes, fleet sizes 5/7/10, realistic arrival rate
python simulation.py --mode both --tasks 100 --porters 5 7 10 --interval 8.0

# Replay only (uses DATA2024.xlsx)
python simulation.py --mode replay --tasks 200 --porters 3 5 7

# Synthetic only
python simulation.py --mode synthetic --tasks 100 --interval 8.0 --porters 5 7 10 15
```

Output written to `simulation_results.csv`. Columns include `mean_pickup_time` and `pickup_kpi_violation_rate` alongside total duration metrics.

**Key results:**
- OR-Tools improves mean total task duration by 2–30% over greedy
- OR-Tools reduces mean pickup time by **35.8%** at 5 porters, **27.2%** at 7 porters
- At 15 porters, mean pickup time ≈ 15.9 min — near the KPI target

---

## NLP Accuracy Testing

Validates LLM parsing on 100 reconstructed historical sentences:

```bash
python nlp_accuracy_test.py --samples 100 --output nlp_accuracy_results.csv
```

**Results (n=100, zero parse failures):**

| Field | Accuracy |
|---|---|
| Origin location | 100.0% |
| Destination location | 100.0% |
| Service classification | 98.0% |
| Infection control flag | 98.0% |
| Priority extraction | 88.0% |
| **All 5 fields correct** | **84.0%** |

All 12 priority errors: `Urgent` (ji shi/即時) parsed as `Normal` — fixable with one prompt disambiguation rule.

---

## KPI Pickup-Time Analysis

The 15-min KPI applies to pickup *response time* (porter arrives at origin), not total task completion.

```bash
python kpi_analysis.py
```

Runs synthetic simulation at fleet sizes 5/7/10/15, reports mean pickup time and improvement over greedy. Output saved to `kpi_analysis.md`.

**Key finding:** With 15 porters + OR-Tools, mean pickup time = 15.9 min ≈ KPI target.

---

## Technical Architecture

```
User Request (Natural Language, Chinese or English)
    ↓
DeepSeek LLM → Structured Task JSON
    {from, to, service, priority, stops, equipment, scheduled_at}
    ↓
OR-Tools VRPTW Solver
    ├─ Each task: pickup node + delivery node
    ├─ 15-min KPI soft time windows (λ=1000 penalty)
    ├─ Drop penalty 2×10^7 (always serve, never drop)
    ├─ Rolling-horizon: re-solve on every event
    └─ Greedy fallback if OR-Tools unavailable
    ↓
Porter State Update → Auto-completion timer
    ↓
LLM generates "Why this porter?" explanation
    ↓
Policy Advisor (RAG over DATA2024.xlsx)
    ├─ Retrieve K=10 similar historical tasks
    ├─ KPI risk assessment (Low/Medium/High)
    ├─ Fleet policy suggestions
    └─ Natural language Q&A
```

**Travel times:** 89×89 matrix from historical p75 porter journey data. Floor-based fallback: same-floor 5 min, inter-floor 8 min; global p90 fallback 27.5 min.

**LLM:** DeepSeek API (`https://api.deepseek.com`, model `deepseek-chat`) via OpenAI-compatible client. Key: `OPENAI_API_KEY` in `.env`.

**All state is in-memory** — resets on restart (persistent storage is future work).

---

## API Reference

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/` | Frontend dashboard |
| `POST` | `/dispatch` | Parse NL request, assign porter(s); body: `{"request": "..."}` |
| `GET` | `/status` | All porter states with countdown and route |
| `POST` | `/undo` | Reverse last dispatch |
| `POST` | `/complete/<id>` | Manual task completion (testing) |
| `GET` | `/scheduled` | List pending scheduled tasks |
| `DELETE` | `/scheduled/<id>` | Cancel a scheduled task |
| `POST` | `/advisor/risk` | KPI risk for a task; body: `{"task": {...}}` |
| `GET` | `/advisor/suggestions` | LLM policy suggestions |
| `POST` | `/advisor/ask` | Q&A; body: `{"question": "..."}` |
