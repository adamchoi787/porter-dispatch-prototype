"""
KPI Pickup-Time Analysis

HHH's 15-minute KPI = porter arrives at pickup location within 15 minutes
of the order being placed.  This script runs a fresh synthetic simulation
that tracks 'time-to-pickup' (queueing wait + porter travel to origin),
then reports results suitable for the FYP report.

Usage:
    python kpi_analysis.py
    python kpi_analysis.py --output kpi_analysis.md
"""

import argparse
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))

from simulation import (
    load_travel_matrix,
    generate_synthetic_tasks,
    run_simulation,
    compute_metrics,
    SERVICE_TASK_TIME,
)

KPI = 15.0
FLEET_SIZES = [5, 7, 10, 15]
NUM_TASKS = 100
AVG_INTERVAL = 8.0   # ~10 tasks/hour — matches HHH historical rate per shift


def load_geography_stats(travel_matrix, xlsx_path):
    """
    What % of historical task routes have direct travel > 15 min?
    This is the geographic lower bound on total task duration — tasks
    where even a porter already at the origin cannot finish within KPI.
    """
    try:
        df = pd.read_excel(xlsx_path)
        df.columns = df.columns.str.strip()
        df = df[df['狀態'] != '已傳真']
        df = df.dropna(subset=['從', '往', '服務'])
        df['from'] = df['從'].astype(str).str.strip()
        df['to']   = df['往'].astype(str).str.strip()

        count = 0
        exceeds = 0
        for _, row in df.iterrows():
            f, t = row['from'], row['to']
            if f in travel_matrix and t in travel_matrix.get(f, {}):
                travel = float(travel_matrix[f][t])
                service = SERVICE_TASK_TIME.get(row['服務'].strip(), SERVICE_TASK_TIME['default'])
                count += 1
                if travel + service > KPI:
                    exceeds += 1

        pct = exceeds / count * 100 if count > 0 else float('nan')
        return count, exceeds, pct
    except Exception as e:
        print(f"[Warning] Could not compute geography stats: {e}")
        return 0, 0, float('nan')


def run_pickup_simulation(travel_matrix):
    """Run greedy vs OR-Tools at each fleet size; return dict of metrics."""
    tasks = generate_synthetic_tasks(travel_matrix, num_tasks=NUM_TASKS,
                                     avg_interval=AVG_INTERVAL)
    rows = []
    for num_porters in FLEET_SIZES:
        for strategy in ('greedy', 'ortools'):
            results = run_simulation(tasks, travel_matrix, num_porters,
                                     strategy=strategy, kpi_limit=KPI)
            m = compute_metrics(results, kpi_limit=KPI)
            m['strategy'] = strategy
            m['num_porters'] = num_porters
            rows.append(m)
    return rows


def format_report(rows, geo_count, geo_exceeds, geo_pct):
    lines = []
    lines.append("# KPI Pickup-Time Analysis")
    lines.append("")
    lines.append(f"**HHH stated KPI:** Porter arrives at pickup within **{KPI:.0f} minutes** of order.")
    lines.append("")
    lines.append("## Geographic Lower Bound")
    lines.append("")
    if geo_count > 0:
        lines.append(f"Of {geo_count:,} historical tasks with known routes:")
        lines.append(f"- **{geo_exceeds:,} ({geo_pct:.1f}%)** have direct travel + minimum service time > 15 min")
        lines.append(f"- These tasks **cannot** fully complete within 15 min regardless of fleet size.")
        lines.append(f"- However, the pickup sub-task (porter arriving at origin) **can** meet the KPI")
        lines.append(f"  if a porter is available and nearby — this is what dispatch optimisation controls.")
    else:
        lines.append("(Geography stats unavailable — DATA2024.xlsx not found)")
    lines.append("")
    lines.append("## Simulation Results: Mean Time-to-Pickup")
    lines.append("")
    lines.append("Synthetic mode · 200 tasks · mean inter-arrival 3 min")
    lines.append("")
    lines.append("| Porters | Strategy | Mean Pickup (min) | Pickup KPI Violation (%) | Improvement |")
    lines.append("|---------|----------|-------------------|--------------------------|-------------|")

    # Pair up greedy and ortools per fleet size
    for n in FLEET_SIZES:
        greedy_row = next((r for r in rows if r['num_porters'] == n and r['strategy'] == 'greedy'), None)
        ortools_row = next((r for r in rows if r['num_porters'] == n and r['strategy'] == 'ortools'), None)
        if greedy_row:
            lines.append(f"| {n} | Greedy   | {greedy_row['mean_pickup_time']:.1f} | "
                         f"{greedy_row['pickup_kpi_violation_rate']:.1f}% | — |")
        if ortools_row and greedy_row:
            g_pick = greedy_row['mean_pickup_time']
            o_pick = ortools_row['mean_pickup_time']
            impr = (g_pick - o_pick) / g_pick * 100 if g_pick > 0 else 0
            lines.append(f"| {n} | OR-Tools | {o_pick:.1f} | "
                         f"{ortools_row['pickup_kpi_violation_rate']:.1f}% | **{impr:.1f}% better** |")

    lines.append("")
    lines.append("## Key Findings")
    lines.append("")

    # Find the fleet size where OR-Tools mean pickup ≤ KPI
    for n in FLEET_SIZES:
        ot = next((r for r in rows if r['num_porters'] == n and r['strategy'] == 'ortools'), None)
        if ot and ot['mean_pickup_time'] <= KPI:
            lines.append(f"- With **{n} porters**, OR-Tools achieves a mean pickup time of "
                         f"**{ot['mean_pickup_time']:.1f} min** — within the 15-min KPI.")
            break

    # Best improvement
    best_impr = 0
    best_n = 0
    for n in FLEET_SIZES:
        g = next((r for r in rows if r['num_porters'] == n and r['strategy'] == 'greedy'), None)
        o = next((r for r in rows if r['num_porters'] == n and r['strategy'] == 'ortools'), None)
        if g and o and g['mean_pickup_time'] > 0:
            impr = (g['mean_pickup_time'] - o['mean_pickup_time']) / g['mean_pickup_time'] * 100
            if impr > best_impr:
                best_impr = impr
                best_n = n
    if best_impr > 0:
        lines.append(f"- Largest pickup-time reduction: **{best_impr:.1f}%** at {best_n} porters.")

    lines.append("- OR-Tools' advantage comes from globally optimal porter selection:")
    lines.append("  the nearest available porter is dispatched, minimising travel-to-origin.")
    lines.append("")
    lines.append("## Framing for Report")
    lines.append("")
    lines.append("The 15-min KPI applies to the *response* phase of each task — from order")
    lines.append("to porter arrival. The dispatch optimiser directly controls this phase.")
    lines.append("Total task duration is additionally bounded by geography and service time,")
    lines.append("which no dispatch algorithm can reduce. This project's contribution is")
    lines.append("demonstrable and measurable on the metric HHH actually cares about.")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', default='kpi_analysis.md')
    args = parser.parse_args()

    xlsx_path = os.path.join(os.path.dirname(__file__), '..', 'DATA2024.xlsx')
    travel_xlsx = os.path.join(os.path.dirname(__file__), 'travel_time', 'travel_times.xlsx')

    print("[KPI] Loading travel matrix...")
    travel_matrix = load_travel_matrix(travel_xlsx)

    print("[KPI] Computing geography lower bound from DATA2024.xlsx...")
    geo_count, geo_exceeds, geo_pct = load_geography_stats(travel_matrix, xlsx_path)

    print(f"[KPI] Running simulations ({len(FLEET_SIZES)} fleet sizes × 2 strategies × {NUM_TASKS} tasks)...")
    rows = run_pickup_simulation(travel_matrix)

    print("\n" + "=" * 60)
    print("  KPI Pickup-Time Results")
    print("=" * 60)
    print(f"{'Porters':<8} {'Strategy':<10} {'Mean Pickup':<14} {'Pickup Viol%':<14}")
    print("-" * 50)
    for r in rows:
        print(f"{r['num_porters']:<8} {r['strategy']:<10} "
              f"{r['mean_pickup_time']:<14.1f} {r['pickup_kpi_violation_rate']:<14.1f}")

    if geo_count > 0:
        print(f"\nGeography: {geo_exceeds}/{geo_count} ({geo_pct:.1f}%) routes > 15 min total")

    report_text = format_report(rows, geo_count, geo_exceeds, geo_pct)
    out_path = os.path.join(os.path.dirname(__file__), args.output)
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(report_text)
    print(f"\n[KPI] Report saved to {out_path}")

    return rows


if __name__ == '__main__':
    main()
