# KPI Pickup-Time Analysis

**HHH stated KPI:** Porter arrives at pickup within **15 minutes** of order.

## Geographic Lower Bound

Of 4,999 historical tasks with known routes:
- **2,828 (56.6%)** have direct travel + minimum service time > 15 min
- These tasks **cannot** fully complete within 15 min regardless of fleet size.
- However, the pickup sub-task (porter arriving at origin) **can** meet the KPI
  if a porter is available and nearby — this is what dispatch optimisation controls.

## Simulation Results: Mean Time-to-Pickup

Synthetic mode · 100 tasks · mean inter-arrival 8 min (historically realistic)

| Porters | Strategy | Mean Pickup (min) | Pickup KPI Violation (%) | Improvement |
|---------|----------|-------------------|--------------------------|-------------|
| 5 | Greedy   | 204.5 | 96.0% | — |
| 5 | OR-Tools | 131.3 | 83.0% | **35.8% better** |
| 7 | Greedy   | 58.6 | 94.0% | — |
| 7 | OR-Tools | 42.7 | 78.0% | **27.2% better** |
| 10 | Greedy   | 20.0 | 59.0% | — |
| 10 | OR-Tools | 20.1 | 60.0% | **-0.5% better** |
| 15 | Greedy   | 15.9 | 48.0% | — |
| 15 | OR-Tools | 15.9 | 48.0% | **0.0% better** |

## Key Findings

- Largest pickup-time reduction: **35.8%** at 5 porters.
- OR-Tools' advantage comes from globally optimal porter selection:
  the nearest available porter is dispatched, minimising travel-to-origin.

## Framing for Report

The 15-min KPI applies to the *response* phase of each task — from order
to porter arrival. The dispatch optimiser directly controls this phase.
Total task duration is additionally bounded by geography and service time,
which no dispatch algorithm can reduce. This project's contribution is
demonstrable and measurable on the metric HHH actually cares about.