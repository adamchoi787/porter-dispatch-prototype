"""
Optimization Solver for Porter Dispatch

Strategies:
1. GREEDY (fallback): Nearest-porter assignment
2. OR-TOOLS: VRPTW with time windows, pickup-delivery pairs, and capacity constraints

The OR-Tools model:
- Each task = pickup node (origin) + delivery node (destination)
- Each porter = vehicle starting at their current location
- Time dimension enforces the 15-minute KPI (soft or hard)
- Capacity dimension (=1) prevents a porter from carrying two items simultaneously
- Pickup-delivery constraints ensure same porter serves origin→destination in order
"""

try:
    from ortools.constraint_solver import routing_enums_pb2, pywrapcp
    ORTOOLS_AVAILABLE = True
except ImportError:
    ORTOOLS_AVAILABLE = False


class PorterDispatchSolver:
    """
    Optimizes porter task assignments using VRPTW (OR-Tools) or greedy fallback.

    Attributes:
        travel_matrix: Dict of dicts with travel times (minutes)
        service_task_time: Dict mapping service type -> duration (minutes)
        KPI_LIMIT: Hard/soft upper bound on task completion time (minutes)
        use_soft_kpi: If True, KPI violations incur penalty instead of hard infeasibility
        strategy: 'greedy' or 'ortools' (auto-selected)
    """

    SCALE = 100  # Scale factor: minutes -> integer "centi-minutes" for OR-Tools

    def __init__(self, travel_matrix, service_task_time, verbose=True):
        self.travel_matrix = travel_matrix
        self.service_task_time = service_task_time
        self.KPI_LIMIT = 15.0  # minutes
        self.SOFT_KPI_PENALTY = 1000  # penalty per unit over KPI limit
        self.use_soft_kpi = True  # soft time windows by default
        self.verbose = verbose
        self.strategy = 'ortools' if ORTOOLS_AVAILABLE else 'greedy'
        if self.verbose:
            if self.strategy == 'greedy':
                print("[Solver] Using GREEDY strategy (OR-Tools not installed)")
            else:
                kpi_mode = "soft" if self.use_soft_kpi else "hard"
                print(f"[Solver] Using OR-TOOLS VRPTW ({kpi_mode} KPI={self.KPI_LIMIT} min)")

    def _get_travel_time(self, from_loc, to_loc):
        """Lookup travel time in minutes, with p90 fallback."""
        if from_loc == to_loc:
            return 0.0
        try:
            return float(self.travel_matrix[from_loc][to_loc])
        except (KeyError, TypeError):
            return 27.5

    def _get_service_time(self, service):
        """Lookup service duration in minutes."""
        return self.service_task_time.get(service, self.service_task_time.get('default', 10.0))

    def assign_tasks(self, available_porters, pending_tasks):
        """
        Assign pending_tasks to available_porters.

        Returns: List of (porter_id, task) tuples.
        A porter may appear multiple times if batching assigns them sequential tasks.
        """
        if not available_porters or not pending_tasks:
            return []

        if self.strategy == 'ortools':
            return self._solve_ortools(available_porters, pending_tasks)
        return self._solve_greedy(available_porters, pending_tasks)

    # ── Greedy fallback ────────────────────────────────────────────────

    def _solve_greedy(self, available_porters, pending_tasks):
        """
        Greedy: assign each task to the nearest available porter.
        One task per porter, no batching. Original algorithm.
        """
        assignments = []
        assigned_porters = set()

        for task in pending_tasks:
            best_porter = None
            min_duration = float('inf')

            for porter in available_porters:
                if porter.id in assigned_porters:
                    continue
                travel = self._get_travel_time(porter.current_location, task['from'])
                service = self._get_service_time(task['service'])
                duration = travel + service
                if duration < min_duration:
                    min_duration = duration
                    best_porter = porter

            if best_porter:
                assignments.append((best_porter.id, task))
                assigned_porters.add(best_porter.id)

        return assignments

    # ── OR-Tools VRPTW solver ──────────────────────────────────────────

    def _solve_ortools(self, available_porters, pending_tasks):
        """
        VRPTW solver using Google OR-Tools routing library.

        Node layout (0-indexed):
          [0 .. P-1]          : porter start depots (one per porter)
          [P .. P+T-1]        : pickup nodes (task origins)
          [P+T .. P+2T-1]     : delivery nodes (task destinations)
          [P+2T]              : dummy end depot (all porters end here)

        Where P = num_porters, T = num_tasks.
        """
        if len(pending_tasks) > 20:
            if self.verbose:
                print(f"[Solver] {len(pending_tasks)} tasks > 20; using greedy for speed")
            return self._solve_greedy(available_porters, pending_tasks)

        num_porters = len(available_porters)
        num_tasks = len(pending_tasks)
        num_nodes = num_porters + 2 * num_tasks + 1
        end_depot = num_porters + 2 * num_tasks

        if self.verbose:
            print(f"[OR-Tools] Building VRPTW: {num_tasks} task(s), {num_porters} porter(s), {num_nodes} nodes")

        # ── Build node-to-location mapping ──
        node_location = {}
        for i, porter in enumerate(available_porters):
            node_location[i] = porter.current_location

        for i, task in enumerate(pending_tasks):
            node_location[num_porters + i] = task['from']                   # pickup
            node_location[num_porters + num_tasks + i] = task['to']         # delivery

        node_location[end_depot] = '__END__'

        # ── Service time at each node (only pickups have service time) ──
        node_service = [0.0] * num_nodes
        for i, task in enumerate(pending_tasks):
            node_service[num_porters + i] = self._get_service_time(task['service'])

        # ── Vehicle start/end depots ──
        starts = list(range(num_porters))
        ends = [end_depot] * num_porters

        try:
            manager = pywrapcp.RoutingIndexManager(num_nodes, num_porters, starts, ends)
            routing = pywrapcp.RoutingModel(manager)

            # ── Time transit callback ──
            # Time to leave a node = service_time_at_node + travel_time_to_next
            def time_callback(from_index, to_index):
                from_node = manager.IndexToNode(from_index)
                to_node = manager.IndexToNode(to_index)
                from_loc = node_location.get(from_node)
                to_loc = node_location.get(to_node)

                # Zero cost to/from dummy end depot
                if from_loc == '__END__' or to_loc == '__END__':
                    return 0

                travel = self._get_travel_time(from_loc, to_loc)
                service = node_service[from_node]
                return int((travel + service) * self.SCALE)

            transit_cb_idx = routing.RegisterTransitCallback(time_callback)
            routing.SetArcCostEvaluatorOfAllVehicles(transit_cb_idx)

            # ── Time dimension (enforces KPI) ──
            max_route_time = int(120 * self.SCALE)  # 120 min max route
            routing.AddDimension(
                transit_cb_idx,
                int(30 * self.SCALE),   # allow up to 30 min slack (waiting)
                max_route_time,
                True,                    # force cumul to start at 0
                'Time'
            )
            time_dim = routing.GetDimensionOrDie('Time')

            # Minimize total time across all routes
            for v in range(num_porters):
                end_index = routing.End(v)
                time_dim.SetSpanCostCoefficientForVehicle(1, v)

            # ── Pickup-delivery constraints ──
            kpi_scaled = int(self.KPI_LIMIT * self.SCALE)

            for i in range(num_tasks):
                pickup_node = num_porters + i
                delivery_node = num_porters + num_tasks + i
                pickup_idx = manager.NodeToIndex(pickup_node)
                delivery_idx = manager.NodeToIndex(delivery_node)

                # Same porter must serve pickup and delivery
                routing.AddPickupAndDelivery(pickup_idx, delivery_idx)
                routing.solver().Add(
                    routing.VehicleVar(pickup_idx) == routing.VehicleVar(delivery_idx)
                )
                # Pickup must come before delivery in time
                routing.solver().Add(
                    time_dim.CumulVar(pickup_idx) <= time_dim.CumulVar(delivery_idx)
                )

                # KPI constraint on delivery node (task completion)
                if self.use_soft_kpi:
                    time_dim.SetCumulVarSoftUpperBound(
                        delivery_idx, kpi_scaled, self.SOFT_KPI_PENALTY
                    )
                else:
                    time_dim.CumulVar(delivery_idx).SetMax(kpi_scaled)

                # Allow dropping tasks if infeasible (penalty must exceed worst KPI violation)
                # Worst case KPI violation: ~120 min over * SCALE * SOFT_KPI_PENALTY ≈ 12M
                # Drop penalty must be higher to force the solver to serve tasks when possible
                routing.AddDisjunction(
                    [pickup_idx, delivery_idx],
                    int(200000 * self.SCALE),  # 20M: always prefer serving over dropping
                    2  # max cardinality: serve both or neither
                )

            # ── Capacity dimension (porter carries 1 item at a time) ──
            def demand_callback(from_index):
                node = manager.IndexToNode(from_index)
                if num_porters <= node < num_porters + num_tasks:
                    return 1   # pickup: load item
                if num_porters + num_tasks <= node < num_porters + 2 * num_tasks:
                    return -1  # delivery: unload item
                return 0       # depot nodes: no load change

            demand_cb_idx = routing.RegisterUnaryTransitCallback(demand_callback)
            routing.AddDimensionWithVehicleCapacity(
                demand_cb_idx,
                0,                      # no slack
                [1] * num_porters,      # capacity = 1 per porter
                True,                   # start cumul at 0
                'Capacity'
            )

            # ── Search parameters ──
            search_params = pywrapcp.DefaultRoutingSearchParameters()
            search_params.first_solution_strategy = (
                routing_enums_pb2.FirstSolutionStrategy.PARALLEL_CHEAPEST_INSERTION
            )
            search_params.local_search_metaheuristic = (
                routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
            )
            search_params.time_limit.seconds = 1

            # ── Solve ──
            solution = routing.SolveWithParameters(search_params)

            if not solution:
                if self.verbose:
                    print("[OR-Tools] No solution found; falling back to greedy")
                return self._solve_greedy(available_porters, pending_tasks)

            # ── Extract assignments from solution ──
            assignments = []
            for v in range(num_porters):
                porter = available_porters[v]
                index = routing.Start(v)
                route_nodes = []

                while not routing.IsEnd(index):
                    node = manager.IndexToNode(index)
                    arrival = solution.Value(time_dim.CumulVar(index)) / self.SCALE
                    route_nodes.append((node, arrival))

                    # If this is a pickup node, record the assignment
                    if num_porters <= node < num_porters + num_tasks:
                        task_idx = node - num_porters
                        assignments.append((porter.id, pending_tasks[task_idx]))

                    index = solution.Value(routing.NextVar(index))

                if self.verbose and route_nodes:
                    stops = [node_location.get(n, '?') for n, _ in route_nodes if n >= num_porters]
                    if stops:
                        print(f"  Porter {porter.id}: {' → '.join(stops)}")

            if assignments:
                if self.verbose:
                    print(f"[OR-Tools] Assigned {len(assignments)} task(s) across {num_porters} porter(s)")
                return assignments

            if self.verbose:
                print("[OR-Tools] Solution found but no assignments extracted; falling back to greedy")
            return self._solve_greedy(available_porters, pending_tasks)

        except Exception as e:
            if self.verbose:
                print(f"[OR-Tools] Error: {e}. Falling back to greedy")
            return self._solve_greedy(available_porters, pending_tasks)
