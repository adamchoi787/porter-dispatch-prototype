"""
Discrete-Event Simulation for Porter Dispatch

Compares solver strategies (greedy vs OR-Tools VRPTW) under:
1. Historical replay mode (DATA2024.xlsx)
2. Synthetic Poisson arrival mode

Metrics: completion time, KPI violation rate, porter utilization, queue length.

Usage:
    python simulation.py                         # Run default comparison
    python simulation.py --mode synthetic        # Synthetic arrivals only
    python simulation.py --mode replay           # Replay historical data
    python simulation.py --porters 3 5 7 10      # Compare fleet sizes
"""

import argparse
import csv
import heapq
import os
import random
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

import pandas as pd

from solver import PorterDispatchSolver

# ── Load travel matrix (same as porter_prototype.py) ──────────────────

def load_travel_matrix(xlsx_path):
    """Load the 89x89 travel time matrix from Excel."""
    try:
        df = pd.read_excel(xlsx_path, sheet_name='Travel_Times_P75', index_col=0)
        matrix = {}
        for loc_from in df.index:
            matrix[loc_from] = {}
            for loc_to in df.columns:
                val = df.loc[loc_from, loc_to]
                if isinstance(val, str) and ':' in val:
                    parts = val.split(':')
                    h, m, s = int(parts[0]), int(parts[1]), int(parts[2])
                    matrix[loc_from][loc_to] = h * 60 + m + s / 60.0
                elif val == 0 or val == '0':
                    matrix[loc_from][loc_to] = 0.0
                else:
                    try:
                        matrix[loc_from][loc_to] = float(val.total_seconds() / 60) if hasattr(val, 'total_seconds') else float(val)
                    except Exception:
                        matrix[loc_from][loc_to] = 27.5
        return matrix
    except Exception as e:
        print(f"[Sim] Could not load travel matrix: {e}. Using fallback.")
        return {
            'At-Base': {'7H': 5, '5L': 4, 'A&D': 1, '3FXRAY': 3, '化驗室': 3, '6H': 5, '太平間': 8, '支援部': 5},
            '7H': {'At-Base': 5, '3FXRAY': 2.5, '太平間': 17.4, '5L': 2, 'A&D': 4, '化驗室': 4, '6H': 3, '支援部': 6},
            '5L': {'At-Base': 4, '太平間': 19.8, '7H': 2, '3FXRAY': 3, 'A&D': 3, '化驗室': 3, '6H': 2, '支援部': 5},
            'A&D': {'At-Base': 1, '6H': 6.1, '7H': 4, '5L': 3, '3FXRAY': 2, '化驗室': 3, '太平間': 10, '支援部': 4},
            '3FXRAY': {'At-Base': 3, '7H': 2.5, '5L': 3, 'A&D': 2, '化驗室': 1, '6H': 4, '太平間': 12, '支援部': 3},
            '化驗室': {'At-Base': 3, '支援部': 11.1, '7H': 4, '5L': 3, 'A&D': 3, '3FXRAY': 1, '6H': 4, '太平間': 11},
            '6H': {'At-Base': 5, '太平間': 23.9, '7H': 3, '5L': 2, 'A&D': 6.1, '3FXRAY': 4, '化驗室': 4, '支援部': 6},
            '太平間': {'At-Base': 8, '7H': 17.4, '5L': 19.8, 'A&D': 10, '3FXRAY': 12, '化驗室': 11, '6H': 23.9, '支援部': 10},
            '支援部': {'At-Base': 5, '7H': 6, '5L': 5, 'A&D': 4, '3FXRAY': 3, '化驗室': 11.1, '6H': 6, '太平間': 10},
        }


SERVICE_TASK_TIME = {
    '送病人': 15.0, '送入院': 21.0, '運送遺體': 25.0,
    '送標本': 8.0, '送3F X光': 12.0, 'default': 10.0,
}

FALLBACK_LOCATIONS = ['At-Base', '7H', 'A&D', '5L', '6H', '3FXRAY', '化驗室', '支援部', '太平間']


# ── Simulation data structures ────────────────────────────────────────

@dataclass(order=True)
class Event:
    time: float  # minutes from simulation start
    type: str = field(compare=False)  # 'arrival' or 'completion'
    data: dict = field(compare=False, default_factory=dict)


@dataclass
class SimPorter:
    id: str
    current_location: str
    status: str = 'available'    # 'available' or 'busy'
    busy_until: float = 0.0     # sim time when porter becomes free


@dataclass
class TaskResult:
    task: dict
    request_time: float         # sim time when task arrived
    assign_time: Optional[float] = None
    completion_time: Optional[float] = None
    porter_id: Optional[str] = None
    total_duration: Optional[float] = None  # completion_time - request_time
    pickup_time: Optional[float] = None     # queueing_wait + travel_porter→origin


# ── Data loading ──────────────────────────────────────────────────────

def load_historical_tasks(xlsx_path, max_tasks=500):
    """
    Load tasks from DATA2024.xlsx for replay simulation.
    Returns list of (relative_time_minutes, task_dict) sorted by arrival time.
    """
    df = pd.read_excel(xlsx_path, nrows=max_tasks * 2)  # read extra in case of filtering
    df.columns = df.columns.str.strip()

    # Filter to rows with valid origin, destination, service, and timestamp
    required = ['下單', '從', '往', '服務']
    for col in required:
        df = df[df[col].notna()]

    # Map priority names to system format
    priority_map = {'即時': 'Urgent', '超緊急': 'Super-Urgent'}

    tasks = []
    base_time = df['下單'].min()

    for _, row in df.iterrows():
        origin = str(row['從']).strip()
        dest = str(row['往']).strip()
        service = str(row['服務']).strip()
        priority = priority_map.get(str(row.get('優先', '')).strip(), 'Normal')
        equipment = []
        equip_val = row.get('設備', '')
        if pd.notna(equip_val) and str(equip_val).strip() not in ('無工具', ''):
            equipment = [str(equip_val).strip()]

        infection = ''
        inf_val = row.get('感染控制', '')
        if pd.notna(inf_val):
            infection = str(inf_val).strip()

        # Relative time in minutes from first task
        rel_time = (row['下單'] - base_time).total_seconds() / 60.0

        task = {
            'from': origin,
            'to': dest,
            'service': service,
            'priority': priority,
            'equipment': equipment,
        }
        if infection:
            task['infection_control'] = infection

        tasks.append((rel_time, task))

        if len(tasks) >= max_tasks:
            break

    tasks.sort(key=lambda x: x[0])
    print(f"[Sim] Loaded {len(tasks)} historical tasks spanning {tasks[-1][0]:.0f} minutes")
    return tasks


def generate_synthetic_tasks(travel_matrix, num_tasks=100, avg_interval=3.0, seed=42):
    """
    Generate synthetic task arrivals using Poisson process.
    avg_interval: mean inter-arrival time in minutes.
    """
    rng = random.Random(seed)
    locations = list(travel_matrix.keys())
    services = list(SERVICE_TASK_TIME.keys())
    services = [s for s in services if s != 'default']

    # Weighted service distribution (approximate real distribution)
    service_weights = [3, 2, 1, 2, 2]  # 送病人, 送入院, 運送遺體, 送標本, 送3F X光

    tasks = []
    current_time = 0.0

    for _ in range(num_tasks):
        # Poisson inter-arrival (exponential)
        interval = rng.expovariate(1.0 / avg_interval)
        current_time += interval

        origin = rng.choice(locations)
        dest = rng.choice([l for l in locations if l != origin])
        service = rng.choices(services, weights=service_weights, k=1)[0]
        priority = rng.choices(['Normal', 'Urgent', 'Super-Urgent'], weights=[7, 2, 1], k=1)[0]

        task = {
            'from': origin,
            'to': dest,
            'service': service,
            'priority': priority,
            'equipment': [],
        }
        tasks.append((current_time, task))

    print(f"[Sim] Generated {len(tasks)} synthetic tasks over {current_time:.0f} minutes")
    return tasks


# ── Simulation engine ─────────────────────────────────────────────────

def run_simulation(tasks, travel_matrix, num_porters, strategy='ortools', kpi_limit=15.0, use_soft_kpi=True):
    """
    Discrete-event simulation of porter dispatch.

    Args:
        tasks: list of (arrival_time, task_dict)
        travel_matrix: travel time matrix
        num_porters: fleet size
        strategy: 'greedy' or 'ortools'
        kpi_limit: KPI target in minutes
        use_soft_kpi: soft vs hard time windows

    Returns: list of TaskResult
    """
    # Create solver (non-verbose for simulation)
    solver = PorterDispatchSolver(travel_matrix, SERVICE_TASK_TIME, verbose=False)
    if strategy == 'greedy':
        solver.strategy = 'greedy'
    solver.KPI_LIMIT = kpi_limit
    solver.use_soft_kpi = use_soft_kpi

    # Create porters at locations from the travel matrix
    matrix_locs = list(travel_matrix.keys())
    preferred = [l for l in FALLBACK_LOCATIONS if l in matrix_locs]
    if not preferred:
        preferred = matrix_locs[:num_porters]

    porters = []
    for i in range(num_porters):
        loc = preferred[i % len(preferred)]
        porters.append(SimPorter(id=f'P-{i+1:03d}', current_location=loc))

    # State
    event_queue = []  # min-heap of Events
    pending_tasks = []  # tasks waiting for assignment
    results = []  # completed TaskResult objects
    task_results_map = {}  # task id -> TaskResult

    def get_travel_time(a, b):
        if a == b:
            return 0.0
        try:
            return float(travel_matrix[a][b])
        except (KeyError, TypeError):
            return 27.5

    def get_service_time(service):
        return SERVICE_TASK_TIME.get(service, SERVICE_TASK_TIME.get('default', 10.0))

    def try_assign(sim_time):
        """Run solver on pending tasks with available porters."""
        available = [p for p in porters if p.status == 'available']
        if not available or not pending_tasks:
            return

        # Create mock porter objects for the solver
        class MockPorter:
            def __init__(self, pid, loc):
                self.id = pid
                self.current_location = loc

        mock_porters = [MockPorter(p.id, p.current_location) for p in available]
        assignments = solver.assign_tasks(mock_porters, list(pending_tasks))

        for porter_id, task in assignments:
            porter = next(p for p in porters if p.id == porter_id)
            if porter.status != 'available':
                continue  # already assigned in this batch

            # Calculate task duration
            travel_to_origin = get_travel_time(porter.current_location, task['from'])
            travel_to_dest = get_travel_time(task['from'], task['to'])
            service_time = get_service_time(task['service'])
            total_duration = travel_to_origin + travel_to_dest + service_time

            # Update porter state
            porter.status = 'busy'
            porter.busy_until = sim_time + total_duration
            porter.current_location = task['to']

            # Record assignment
            tid = id(task)
            if tid in task_results_map:
                tr = task_results_map[tid]
                tr.assign_time = sim_time
                tr.completion_time = sim_time + total_duration
                tr.porter_id = porter_id
                tr.total_duration = tr.completion_time - tr.request_time
                tr.pickup_time = (sim_time - tr.request_time) + travel_to_origin

            # Remove from pending
            pending_tasks.remove(task)

            # Schedule completion event
            heapq.heappush(event_queue, Event(
                time=sim_time + total_duration,
                type='completion',
                data={'porter_id': porter_id}
            ))

    # Initialize event queue with task arrivals
    for arrival_time, task in tasks:
        heapq.heappush(event_queue, Event(
            time=arrival_time,
            type='arrival',
            data={'task': task}
        ))

    # Run simulation
    while event_queue:
        event = heapq.heappop(event_queue)

        if event.type == 'arrival':
            task = event.data['task']
            tid = id(task)
            tr = TaskResult(task=task, request_time=event.time)
            task_results_map[tid] = tr
            results.append(tr)
            pending_tasks.append(task)
            try_assign(event.time)

        elif event.type == 'completion':
            porter_id = event.data['porter_id']
            porter = next(p for p in porters if p.id == porter_id)
            porter.status = 'available'
            try_assign(event.time)

    # Mark any unassigned tasks
    for tr in results:
        if tr.completion_time is None:
            tr.total_duration = None  # never completed

    return results


# ── Metrics ───────────────────────────────────────────────────────────

def compute_metrics(results, kpi_limit=15.0):
    """Compute summary metrics from simulation results."""
    completed = [r for r in results if r.total_duration is not None]
    if not completed:
        return {'completed': 0, 'total': len(results)}

    durations = [r.total_duration for r in completed]
    violations = [d for d in durations if d > kpi_limit]

    pickup_times = [r.pickup_time for r in completed if r.pickup_time is not None]
    pickup_violations = [t for t in pickup_times if t > kpi_limit]

    return {
        'total': len(results),
        'completed': len(completed),
        'unassigned': len(results) - len(completed),
        'mean_duration': sum(durations) / len(durations),
        'p95_duration': sorted(durations)[int(0.95 * len(durations))],
        'max_duration': max(durations),
        'kpi_violations': len(violations),
        'kpi_violation_rate': len(violations) / len(completed) * 100,
        'mean_wait': sum(r.assign_time - r.request_time for r in completed if r.assign_time is not None) / len(completed),
        'mean_pickup_time': sum(pickup_times) / len(pickup_times) if pickup_times else float('nan'),
        'pickup_kpi_violation_rate': len(pickup_violations) / len(completed) * 100 if completed else float('nan'),
    }


def print_metrics(metrics, label=""):
    """Print formatted metrics."""
    if label:
        print(f"\n{'='*60}")
        print(f"  {label}")
        print(f"{'='*60}")

    print(f"  Tasks: {metrics['completed']}/{metrics['total']} completed"
          f" ({metrics.get('unassigned', 0)} unassigned)")
    print(f"  Duration:  mean={metrics['mean_duration']:.1f}  "
          f"P95={metrics['p95_duration']:.1f}  max={metrics['max_duration']:.1f} min")
    print(f"  KPI (<15 min): {metrics['kpi_violations']} violations "
          f"({metrics['kpi_violation_rate']:.1f}%)")
    print(f"  Mean wait: {metrics['mean_wait']:.1f} min")


def save_results_csv(all_metrics, filename='simulation_results.csv'):
    """Save comparison results to CSV."""
    if not all_metrics:
        return

    filepath = os.path.join(os.path.dirname(__file__), filename)
    keys = list(all_metrics[0].keys())
    with open(filepath, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=keys)
        writer.writeheader()
        writer.writerows(all_metrics)
    print(f"\n[Sim] Results saved to {filepath}")


# ── Main ──────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description='Porter Dispatch Simulation')
    parser.add_argument('--mode', choices=['replay', 'synthetic', 'both'], default='both',
                        help='Data source mode')
    parser.add_argument('--porters', nargs='+', type=int, default=[3, 5, 7],
                        help='Fleet sizes to test')
    parser.add_argument('--tasks', type=int, default=200,
                        help='Number of tasks (synthetic) or max tasks (replay)')
    parser.add_argument('--interval', type=float, default=3.0,
                        help='Mean inter-arrival time in minutes (synthetic)')
    parser.add_argument('--kpi', type=float, default=15.0,
                        help='KPI limit in minutes')
    parser.add_argument('--csv', type=str, default='simulation_results.csv',
                        help='Output CSV filename')
    args = parser.parse_args()

    # Load travel matrix
    xlsx_path = os.path.join(os.path.dirname(__file__), 'travel_time', 'travel_times.xlsx')
    travel_matrix = load_travel_matrix(xlsx_path)
    valid_locations = set(travel_matrix.keys())

    # Prepare task sets
    task_sets = []

    if args.mode in ('replay', 'both'):
        data_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'DATA2024.xlsx')
        if os.path.exists(data_path):
            historical = load_historical_tasks(data_path, max_tasks=args.tasks)
            # Filter to tasks with locations in the travel matrix
            filtered = [(t, task) for t, task in historical
                        if task['from'] in valid_locations and task['to'] in valid_locations]
            print(f"[Sim] {len(filtered)}/{len(historical)} historical tasks have valid locations in matrix")
            if filtered:
                task_sets.append(('replay', filtered))
        else:
            print(f"[Sim] DATA2024.xlsx not found at {data_path}")

    if args.mode in ('synthetic', 'both'):
        synthetic = generate_synthetic_tasks(travel_matrix, num_tasks=args.tasks, avg_interval=args.interval)
        task_sets.append(('synthetic', synthetic))

    if not task_sets:
        print("[Sim] No tasks to simulate. Exiting.")
        return

    # Run experiments
    strategies = ['greedy', 'ortools']
    all_metrics = []

    for data_label, tasks in task_sets:
        for num_porters in args.porters:
            for strategy in strategies:
                label = f"{data_label} | {strategy.upper()} | {num_porters} porters | {len(tasks)} tasks"
                print(f"\n>>> Running: {label}")

                results = run_simulation(
                    tasks=tasks,
                    travel_matrix=travel_matrix,
                    num_porters=num_porters,
                    strategy=strategy,
                    kpi_limit=args.kpi,
                )
                metrics = compute_metrics(results, kpi_limit=args.kpi)
                metrics['data_source'] = data_label
                metrics['strategy'] = strategy
                metrics['num_porters'] = num_porters
                metrics['num_tasks'] = len(tasks)

                print_metrics(metrics, label)
                all_metrics.append(metrics)

    # Save to CSV
    save_results_csv(all_metrics, args.csv)

    # Summary comparison
    print(f"\n{'='*60}")
    print("  SUMMARY COMPARISON")
    print(f"{'='*60}")
    print(f"{'Source':<12} {'Strategy':<10} {'Porters':<8} {'Mean':<8} {'P95':<8} {'KPI Viol%':<10}")
    print(f"{'-'*60}")
    for m in all_metrics:
        print(f"{m['data_source']:<12} {m['strategy']:<10} {m['num_porters']:<8} "
              f"{m['mean_duration']:<8.1f} {m['p95_duration']:<8.1f} {m['kpi_violation_rate']:<10.1f}")


if __name__ == '__main__':
    main()
