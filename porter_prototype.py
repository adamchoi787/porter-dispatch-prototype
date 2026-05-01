import json
import time
import os
import openai
import traceback
import threading
from datetime import datetime, timedelta
import pandas as pd
from solver import PorterDispatchSolver

# --- Step 1: The "Map" (Data Foundation) ---
def load_travel_matrix(xlsx_path):
    """Load the 89x89 travel time matrix from travel_times.xlsx (Travel_Times_P75 sheet)."""
    try:
        df = pd.read_excel(xlsx_path, sheet_name='Travel_Times_P75', index_col=0)
        matrix = {}
        for loc_from in df.index:
            matrix[loc_from] = {}
            for loc_to in df.columns:
                val = df.loc[loc_from, loc_to]
                if isinstance(val, str) and ':' in val:
                    # Parse HH:MM:SS format to minutes
                    parts = val.split(':')
                    h, m, s = int(parts[0]), int(parts[1]), int(parts[2])
                    matrix[loc_from][loc_to] = h * 60 + m + s / 60.0
                elif val == 0 or val == '0':
                    matrix[loc_from][loc_to] = 0.0
                else:
                    # Handle timedelta or numeric values
                    try:
                        matrix[loc_from][loc_to] = float(val.total_seconds() / 60) if hasattr(val, 'total_seconds') else float(val)
                    except:
                        matrix[loc_from][loc_to] = 27.5  # p90 fallback
        print(f"[Matrix] Loaded {len(matrix)} x {len(df.columns)} travel matrix from {xlsx_path}")
        return matrix
    except Exception as e:
        print(f"[Matrix] Error loading travel matrix: {e}. Using fallback 9-location matrix.")
        return TRAVEL_TIME_MATRIX_FALLBACK

# Fallback hardcoded matrix (9 locations) for when Excel file is not available
TRAVEL_TIME_MATRIX_FALLBACK = {
    'At-Base': {'7H': 5, '5L': 4, 'A&D': 1, '3FXRAY': 3, '化驗室': 3, '6H': 5, '太平間': 8, '支援部': 5},
    '7H': {'At-Base': 5, '3FXRAY': 2.5, '太平間': 17.4, '5L': 2, 'A&D': 4, '化驗室': 4, '6H': 3, '支援部': 6},
    '5L': {'At-Base': 4, '太平間': 19.8, '7H': 2, '3FXRAY': 3, 'A&D': 3, '化驗室': 3, '6H': 2, '支援部': 5},
    'A&D': {'At-Base': 1, '6H': 6.1, '7H': 4, '5L': 3, '3FXRAY': 2, '化驗室': 3, '太平間': 10, '支援部': 4},
    '3FXRAY': {'At-Base': 3, '7H': 2.5, '5L': 3, 'A&D': 2, '化驗室': 1, '6H': 4, '太平間': 12, '支援部': 3},
    '化驗室': {'At-Base': 3, '支援部': 11.1, '7H': 4, '5L': 3, 'A&D': 3, '3FXRAY': 1, '6H': 4, '太平間': 11},
    '6H': {'At-Base': 5, '太平間': 23.9, '7H': 3, '5L': 2, 'A&D': 6.1, '3FXRAY': 4, '化驗室': 4, '支援部': 6},
    '太平間': {'At-Base': 8, '7H': 17.4, '5L': 19.8, 'A&D': 10, '3FXRAY': 12, '化驗室': 11, '6H': 23.9, '支援部': 10},
    '支援部': {'At-Base': 5, '7H': 6, '5L': 5, 'A&D': 4, '3FXRAY': 3, '化驗室': 11.1, '6H': 6, '太平間': 10}
}

# Load the full 89-location matrix from travel_times.xlsx
xlsx_path = os.path.join(os.path.dirname(__file__), 'travel_time', 'travel_times.xlsx')
TRAVEL_TIME_MATRIX = load_travel_matrix(xlsx_path)

# --- Step 2: The "Prediction Model" (Task Time) ---
SERVICE_TASK_TIME = {
    '送病人': 15.0,
    '送入院': 21.0,
    '運送遺體': 25.0,
    '送標本': 8.0,
    '送3F X光': 12.0,
    'default': 10.0
}

class Porter:
    """
    Represents a porter in our simulation.
    Manages their state, location, and availability.
    """
    def __init__(self, porter_id, initial_location='At-Base'):
        self.id = porter_id
        self.current_location = initial_location
        self.status = 'available'
        self.current_task = None
        self.task_start_location = None
        self.task_completion_time = None
        self.available_at = None
        self.completion_timer = None
        print(f"Porter {self.id} created at {self.current_location}")

    def assign_task(self, task, estimated_total_duration, on_complete_callback=None):
        self.status = 'busy'
        self.current_task = task
        self.task_start_location = self.current_location
        self.task_completion_time = estimated_total_duration
        self.available_at = datetime.now() + timedelta(minutes=estimated_total_duration)
        self.current_location = task['to']
        print(f"  [ASSIGNED] Porter {self.id} assigned task. Est. duration: {estimated_total_duration:.1f} mins.")
        print(f"             Task: {task['service']} from {task['from']} to {task['to']}.")
        print(f"             Porter will be at {self.current_location} and 'available' after this task.")

        # Start auto-completion timer if callback provided
        if on_complete_callback:
            delay_seconds = estimated_total_duration * 60
            self.completion_timer = threading.Timer(delay_seconds, on_complete_callback, args=[self.id])
            self.completion_timer.daemon = False  # FIXED: Make it non-daemon so Flask waits for it
            self.completion_timer.start()
            print(f"             Auto-completion timer started for {estimated_total_duration:.1f} mins.")

    def complete_task(self):
        if self.status == 'busy':
            print(f"  [COMPLETED] ✓ Porter {self.id} finished task at {self.current_location}. Now available.")
            self.status = 'available'
            self.current_task = None
            self.task_start_location = None
            self.task_completion_time = None
            self.available_at = None
            if self.completion_timer:
                self.completion_timer.cancel()
                self.completion_timer = None
        else:
            print(f"  [INFO] Porter {self.id} is already available.")

class ChatGPTLLM:
    """
    Uses the OpenAI API (ChatGPT) to parse user requests.
    """
    def __init__(self):
        # 1. Load the key (Ensure this is your DEEPSEEK key, not the sk-proj... one)
        self.api_key = os.environ.get("OPENAI_API_KEY")
        if not self.api_key:
            raise ValueError("OPENAI_API_KEY environment variable not set.")
        
        # 2. Initialize the client with the DeepSeek URL
        self.client = openai.OpenAI(
            api_key=self.api_key, 
            base_url="https://api.deepseek.com"  # <--- THIS LINE IS CRITICAL
        )
        
        self.system_prompt = self._build_system_prompt()
        self._current_time_str = None  # set externally before each call if needed
        print("ChatGPTLLM Initialized (using DeepSeek).")

    def _build_system_prompt(self):
        """Creates the detailed instruction prompt for the AI."""
        valid_locations = sorted(list(TRAVEL_TIME_MATRIX.keys()))  # 89 locations from the matrix
        valid_services = list(SERVICE_TASK_TIME.keys())

        locations_str = ", ".join(valid_locations)
        prompt = f"""
        You are a hospital dispatch system. Your job is to parse a user request
        into a structured JSON object. Respond ONLY with the JSON object.

        The JSON object must have this exact format:
        {{
          "tasks": [
            {{
              "from": "ORIGIN_LOCATION",
              "stops": ["STOP_1", "STOP_2"],
              "to": "DESTINATION_LOCATION",
              "service": "SERVICE_TYPE",
              "priority": "PRIORITY_LEVEL",
              "equipment": ["EQUIPMENT_1", "EQUIPMENT_2", ...],
              "scheduled_at": "ISO8601_DATETIME_OR_NULL"
            }}
          ]
        }}

        RULES:
        1.  "from", all "stops" entries, and "to" MUST be one of the following valid locations (89 total): {locations_str}
        2.  "service" MUST be one of the following valid services: {valid_services}
        3.  "priority" should be 'Normal', 'Urgent', or 'Super-Urgent'.
        4.  "equipment" should be a list of items needed (e.g., "Wheelchair", "O2", "Stretcher"). If none are mentioned, return an empty list [].
        5.  "stops" is an ordered list of intermediate locations to visit between "from" and "to". Return [] if none.
            - A chain like "from A to B to C" or "A → B → C" means ONE task: from=A, stops=[B], to=C.
            - Only split into multiple tasks if the request clearly describes separate, independent journeys (e.g. different items or people going to different places).
        6.  If the request describes multiple independent tasks, include ALL of them in the "tasks" array.
        7.  If you cannot determine a value, use "Unknown".
        8.  "scheduled_at": If the request mentions a future time (e.g. "at 14:30", "in 2 hours", "tomorrow at 9am"), resolve it to an absolute ISO 8601 datetime string (e.g. "2026-04-16T14:30:00"). The current time is {{CURRENT_TIME}}. If the task should be dispatched immediately, set "scheduled_at" to null.
        """
        return prompt

    def build_prompt_for_request(self):
        """Return the system prompt with the current time substituted in."""
        now_str = datetime.now().strftime('%Y-%m-%dT%H:%M:%S')
        return self.system_prompt.replace('{CURRENT_TIME}', now_str)

    def get_structured_tasks(self, user_request):
        """Parses the natural language request into a list of structured task dicts."""
        print(f"\n[LLM-OpenAI] Parsing request: '{user_request}'")

        try:
            response = self.client.chat.completions.create(
                model="deepseek-chat",
                messages=[
                    {"role": "system", "content": self.build_prompt_for_request()},
                    {"role": "user", "content": user_request}
                ],
                response_format={"type": "json_object"}
            )

            parsed_json = json.loads(response.choices[0].message.content)
            print(f"[LLM-OpenAI] Parsed data: {json.dumps(parsed_json)}")

            # Expect {"tasks": [...]}; fall back to wrapping a bare single-task object
            if "tasks" in parsed_json and isinstance(parsed_json["tasks"], list):
                return parsed_json["tasks"]
            return [parsed_json]

        except Exception as e:
            print(f"[LLM-OpenAI] Error parsing request: {e}")
            return []

class PorterDispatchSystem:
    """
    The main class that orchestrates the entire system.
    Integrates LLM (for parsing) with optimization solver (for assignment).

    Args:
        num_porters: Fleet size (3-10). Porters are assigned round-robin starting locations.
        use_llm: If False, skip LLM initialization (for simulation mode).
    """

    # Starting locations cycled across porters (real matrix locations)
    # Falls back to hardcoded locations if matrix doesn't contain them
    FALLBACK_LOCATIONS = ['At-Base', '7H', 'A&D', '5L', '6H', '3FXRAY', '化驗室', '支援部', '太平間']

    def __init__(self, num_porters=10, use_llm=True):
        self.travel_matrix = TRAVEL_TIME_MATRIX
        self.task_time_map = SERVICE_TASK_TIME
        self.porters = self._create_fleet(num_porters)
        self.llm_engine = ChatGPTLLM() if use_llm else None
        self.solver = PorterDispatchSolver(self.travel_matrix, self.task_time_map)
        self.task_queue = []
        self.scheduled_queue = []   # list of {task, scheduled_at: datetime, id: str}
        self.last_dispatch = None   # undo snapshot: {assigned: [...], queued: [...]}
        self.last_task_errors = []  # validation errors from the most recent dispatch call
        self._scheduled_lock = threading.Lock()
        self._start_scheduler()

    def _create_fleet(self, num_porters):
        """Create a fleet of porters with round-robin starting locations from the travel matrix."""
        # Use locations from the actual travel matrix
        matrix_locations = list(self.travel_matrix.keys())
        # Prefer known ward locations that are in the matrix
        preferred = [l for l in self.FALLBACK_LOCATIONS if l in matrix_locations]
        if not preferred:
            preferred = matrix_locations[:num_porters]

        porters = []
        for i in range(num_porters):
            pid = f'P-{i+1:03d}'
            loc = preferred[i % len(preferred)]
            porters.append(Porter(pid, loc))
        return porters

    def _start_scheduler(self):
        """Background thread that checks every 30s and releases due scheduled tasks."""
        def scheduler_loop():
            while True:
                time.sleep(30)
                self._release_due_scheduled_tasks()

        t = threading.Thread(target=scheduler_loop, daemon=True)
        t.start()
        print("[Scheduler] Background scheduler started (30s interval).")

    def _release_due_scheduled_tasks(self):
        """Move any scheduled tasks whose scheduled_at has passed into normal dispatch."""
        now = datetime.now()
        with self._scheduled_lock:
            due = [entry for entry in self.scheduled_queue if entry['scheduled_at'] <= now]
            self.scheduled_queue = [entry for entry in self.scheduled_queue if entry['scheduled_at'] > now]

        for entry in due:
            task = entry['task']
            print(f"[Scheduler] Releasing scheduled task {entry['id']}: {task.get('service')} {task.get('from')}→{task.get('to')}")
            self.dispatch_structured_task(task)

    def schedule_task(self, structured_task, scheduled_at):
        """
        Add a task to the scheduled queue to be dispatched at scheduled_at (datetime).
        Returns the scheduled entry id.
        """
        import uuid
        entry_id = str(uuid.uuid4())[:8]
        entry = {
            'id': entry_id,
            'task': structured_task,
            'scheduled_at': scheduled_at,
        }
        with self._scheduled_lock:
            self.scheduled_queue.append(entry)
        print(f"[Scheduler] Task {entry_id} scheduled for {scheduled_at.isoformat()}: {structured_task.get('service')} {structured_task.get('from')}→{structured_task.get('to')}")
        return entry_id

    def cancel_scheduled_task(self, entry_id):
        """Remove a scheduled task by id. Returns True if found and removed."""
        with self._scheduled_lock:
            before = len(self.scheduled_queue)
            self.scheduled_queue = [e for e in self.scheduled_queue if e['id'] != entry_id]
            return len(self.scheduled_queue) < before

    def _on_task_complete(self, porter_id):
        """Callback triggered when a task auto-completes after its timer expires."""
        print(f"\n[AUTO-COMPLETE] ✓ Timer expired for Porter {porter_id} - AUTO-COMPLETION FIRING!")
        self.simulate_task_completion(porter_id)

    def _validate_task(self, task):
        """
        Validate that LLM output has valid locations and services.
        Returns None if valid, or a human-readable error string if invalid.
        """
        valid_locations = set(self.travel_matrix.keys())
        valid_services = set(self.task_time_map.keys())

        from_loc = task.get('from')
        to_loc = task.get('to')
        service = task.get('service')

        if not from_loc or from_loc not in valid_locations:
            return (
                f"Unknown origin location: '{from_loc}'. "
                "Check the spelling or use a known hospital location."
            )
        if not to_loc or to_loc not in valid_locations:
            return (
                f"Unknown destination location: '{to_loc}'. "
                "Check the spelling or use a known hospital location."
            )
        if not service or service not in valid_services:
            valid_list = ', '.join(sorted(valid_services - {'default'}))
            return (
                f"Unknown service type: '{service}'. "
                f"Valid types are: {valid_list}."
            )
        for stop in task.get('stops', []):
            if stop not in valid_locations:
                return (
                    f"Unknown stop location: '{stop}'. "
                    "Check the spelling or use a known hospital location."
                )
        return None

    def predict_travel_time(self, origin, destination):
        """
        Prediction Model (Travel) with intelligent fallback.

        Strategy:
        1. Direct lookup in travel matrix
        2. If missing, check if both locations are on same floor (floor-based fallback)
        3. If still missing, use p90 global fallback (27.5 min)
        """
        # Same location
        if origin == destination:
            return 0.0

        try:
            return float(self.travel_matrix[origin][destination])
        except (KeyError, TypeError):
            pass

        # Task 3: Floor-based fallback
        # Extract floor from location name (e.g., "7H" → floor 7, "5L" → floor 5)
        origin_floor = self._extract_floor(origin)
        dest_floor = self._extract_floor(destination)

        if origin_floor and dest_floor:
            if origin_floor == dest_floor:
                # Same floor: shorter travel time (estimate: 5 min within-floor)
                print(f"  [Fallback] {origin} → {destination}: same floor {origin_floor}. Using 5 min estimate.")
                return 5.0
            else:
                # Different floors: moderate travel time (estimate: 8 min inter-floor)
                print(f"  [Fallback] {origin} → {destination}: floors {origin_floor}→{dest_floor}. Using 8 min estimate.")
                return 8.0

        # Global p90 fallback (from notebook Task 2)
        print(f"  [Warning] No path from {origin} to {destination}. Using p90 fallback (27.5 min).")
        return 27.5

    @staticmethod
    def _extract_floor(location_name):
        """
        Extract floor number from location name.

        Examples: "7H" → 7, "5L" → 5, "A&D" → None, "At-Base" → None
        """
        if not location_name:
            return None
        # Check first character for digit
        first_char = location_name[0]
        if first_char.isdigit():
            return int(first_char)
        return None

    def predict_route_time(self, porter_location, task):
        """Total travel time for the full route: porter → origin → stops → destination."""
        waypoints = [porter_location, task['from']] + task.get('stops', []) + [task['to']]
        return sum(
            self.predict_travel_time(waypoints[i], waypoints[i + 1])
            for i in range(len(waypoints) - 1)
        )

    def predict_task_time(self, service_name):
        """Prediction Model (Task)."""
        return self.task_time_map.get(service_name, self.task_time_map['default'])

    def find_best_porter(self, task):
        """This is our "greedy" solver. Finds the *nearest available* porter."""
        task_origin = task['from']
        available_porters = [p for p in self.porters if p.status == 'available']

        if not available_porters:
            print("[Solver] No porters are currently available.")
            return None, float('inf')

        print(f"[Solver] Finding best porter for task at {task_origin}...")
        best_porter = None
        min_time_to_origin = float('inf')

        for porter in available_porters:
            time_to_origin = self.predict_travel_time(porter.current_location, task_origin)
            print(f"  - Checking Porter {porter.id} at {porter.current_location}. Time to {task_origin}: {time_to_origin:.1f} min")
            if time_to_origin < min_time_to_origin:
                min_time_to_origin = time_to_origin
                best_porter = porter

        if best_porter:
            print(f"[Solver] Best porter is {best_porter.id} ({min_time_to_origin:.1f} mins away).")
        return best_porter, min_time_to_origin

    def dispatch_new_task(self, user_request):
        """
        Main orchestration function. Parses one or more tasks from natural language
        and dispatches each. Returns a list of (porter, task, duration) or None per task.
        Populates self.last_task_errors with any validation failures.
        Stores an undo snapshot in self.last_dispatch.
        """
        if not self.llm_engine:
            print("[System] LLM not available. Use dispatch_structured_task() instead.")
            return []

        self.last_task_errors = []
        tracking = {'assigned': [], 'queued': []}

        structured_tasks = self.llm_engine.get_structured_tasks(user_request)
        if not structured_tasks:
            print("[System] LLM failed to parse request. Aborting.")
            return []

        results = []
        for task in structured_tasks:
            scheduled_at_str = task.pop('scheduled_at', None)
            if scheduled_at_str:
                # Parse ISO datetime from LLM
                try:
                    scheduled_at = datetime.fromisoformat(scheduled_at_str)
                    if scheduled_at > datetime.now():
                        entry_id = self.schedule_task(task, scheduled_at)
                        results.append(('scheduled', task, scheduled_at, entry_id))
                        continue
                except (ValueError, TypeError):
                    pass  # Malformed — dispatch immediately
            results.append(self.dispatch_structured_task(task, tracking=tracking))

        self.last_dispatch = tracking
        return results

    def dispatch_structured_task(self, structured_task, tracking=None):
        """
        Dispatch an already-parsed task. Uses rolling-horizon re-optimization:
        all queued tasks + the new task are passed to the solver together.

        tracking: optional dict {assigned: [], queued: []} populated for undo support.
        Returns: (porter, task, duration) for the first assignment, or None if queued/error.
        """
        if "from" not in structured_task or "to" not in structured_task:
            msg = "Task is missing 'from' or 'to' field — the LLM could not identify an origin or destination."
            print(f"[System] {msg}")
            self.last_task_errors.append({'task': structured_task, 'error': msg})
            return None

        # Validate that locations and service are known
        error = self._validate_task(structured_task)
        if error:
            print(f"[System] Task validation failed: {error}")
            self.last_task_errors.append({'task': structured_task, 'error': error})
            return None

        # Get available porters
        available_porters = [p for p in self.porters if p.status == 'available']
        if not available_porters:
            print("[System] Task queued. No porters available.")
            self.task_queue.append(structured_task)
            if tracking is not None:
                tracking['queued'].append(structured_task)
            return None

        # Rolling-horizon: optimize ALL pending tasks together (queued + new)
        all_pending = self.task_queue + [structured_task]
        assignments = self.solver.assign_tasks(available_porters, all_pending)

        if not assignments:
            print("[System] Task queued. Solver could not find assignment.")
            self.task_queue.append(structured_task)
            if tracking is not None:
                tracking['queued'].append(structured_task)
            return None

        # Clear queue — assigned tasks are removed, unassigned stay queued
        assigned_tasks = set(id(t) for _, t in assignments)
        self.task_queue = [t for t in self.task_queue if id(t) not in assigned_tasks]

        # Assign all tasks from solver output
        first_result = None
        for porter_id, task in assignments:
            porter = next(p for p in self.porters if p.id == porter_id)
            if porter.status == 'busy':
                # Porter already assigned in this batch — queue remaining tasks
                self.task_queue.append(task)
                continue

            prev_location = porter.current_location
            total_travel_time = self.predict_route_time(porter.current_location, task)
            task_time_at_dest = self.predict_task_time(task['service'])
            total_estimated_duration = total_travel_time + task_time_at_dest

            porter.assign_task(task, total_estimated_duration, on_complete_callback=self._on_task_complete)

            if tracking is not None:
                tracking['assigned'].append({'porter': porter, 'prev_location': prev_location})

            if first_result is None:
                first_result = (porter, task, total_estimated_duration)

        return first_result

    def explain_dispatch(self, porter, task, duration, available_porters=None):
        """
        Use the LLM to generate a human-readable explanation of why a porter was chosen.
        Returns explanation string, or a fallback if LLM is unavailable.
        """
        # Build context about the assignment
        porter_loc = porter.current_location
        time_to_origin = self.predict_travel_time(porter_loc, task['from'])
        service_time = self.predict_task_time(task['service'])
        stops = task.get('stops', [])

        # Build full route string for display
        route_parts = [porter_loc, task['from']] + stops + [task['to']]
        route_str = ' → '.join(route_parts)

        # Build alternatives summary
        alternatives = ""
        if available_porters:
            alt_lines = []
            for p in available_porters:
                if p.id == porter.id:
                    continue
                alt_travel = self.predict_travel_time(p.current_location, task['from'])
                alt_lines.append(f"  - {p.id} at {p.current_location}: {alt_travel:.1f} min to origin")
            if alt_lines:
                alternatives = "Other porters considered:\n" + "\n".join(alt_lines)

        # Structured fallback (always available, even without LLM)
        fallback = (
            f"Porter {porter.id} was at {porter_loc}, only {time_to_origin:.1f} min from the pickup at {task['from']}. "
            f"Route: {route_str} + {service_time:.1f} min service = {duration:.1f} min total."
        )

        if not self.llm_engine:
            return fallback

        # Ask LLM for a natural-language explanation
        prompt = f"""You are a hospital dispatch system. Explain in 2-3 sentences why this porter was chosen for this task.

Assignment:
- Porter {porter.id} at {porter_loc} assigned to: {task['service']}
- Full route: {route_str}
- Travel to pickup: {time_to_origin:.1f} min
- Total estimated: {duration:.1f} min (including {service_time:.1f} min service)
- Priority: {task.get('priority', 'Normal')}
{alternatives}

Keep it concise and clinical. Mention specific times and distances."""

        try:
            response = self.llm_engine.client.chat.completions.create(
                model="deepseek-chat",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=150
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            print(f"[Explain] LLM error: {e}. Using fallback.")
            return fallback

    def simulate_task_completion(self, porter_id):
        """Callback when a porter finishes a task. Triggers rolling-horizon re-optimization."""
        porter = next((p for p in self.porters if p.id == porter_id), None)
        if not porter:
            print(f"[System] Porter {porter_id} not found.")
            return

        porter.complete_task()

        if not self.task_queue:
            print(f"[System] Task queue is empty.")
            return

        print(f"[System] Porter {porter.id} now available. Re-optimizing {len(self.task_queue)} queued task(s)...")
        self._drain_queue()

    def _drain_queue(self):
        """Re-optimize all queued tasks against all available porters."""
        available_porters = [p for p in self.porters if p.status == 'available']
        if not available_porters or not self.task_queue:
            return

        assignments = self.solver.assign_tasks(available_porters, self.task_queue)
        if not assignments:
            return

        assigned_tasks = set(id(t) for _, t in assignments)
        self.task_queue = [t for t in self.task_queue if id(t) not in assigned_tasks]

        for porter_id, task in assignments:
            porter = next(p for p in self.porters if p.id == porter_id)
            if porter.status == 'busy':
                self.task_queue.append(task)
                continue

            total_travel_time = self.predict_route_time(porter.current_location, task)
            task_time_at_dest = self.predict_task_time(task['service'])
            total_estimated_duration = total_travel_time + task_time_at_dest

            porter.assign_task(task, total_estimated_duration, on_complete_callback=self._on_task_complete)
            stops_str = f" via {task['stops']}" if task.get('stops') else ""
            print(f"[QUEUE-DRAIN] Assigned queued task: {task['service']} {task['from']}{stops_str}→{task['to']} to {porter_id}")

    def undo_last_dispatch(self):
        """
        Undo the most recent dispatch call: free assigned porters and remove queued tasks.
        Returns (success: bool, message: str).
        """
        if not self.last_dispatch:
            return False, "Nothing to undo — no dispatch has been made yet."

        n_assigned = len(self.last_dispatch['assigned'])
        n_queued = len(self.last_dispatch['queued'])

        # Restore each assigned porter to their pre-dispatch state
        for entry in self.last_dispatch['assigned']:
            porter = entry['porter']
            if porter.completion_timer:
                porter.completion_timer.cancel()
                porter.completion_timer = None
            porter.status = 'available'
            porter.current_location = entry['prev_location']
            porter.current_task = None
            porter.task_start_location = None
            porter.task_completion_time = None
            porter.available_at = None
            print(f"[UNDO] Porter {porter.id} restored to {entry['prev_location']}.")

        # Remove any queued tasks from this dispatch from the task queue
        if n_queued:
            queued_ids = set(id(t) for t in self.last_dispatch['queued'])
            self.task_queue = [t for t in self.task_queue if id(t) not in queued_ids]
            print(f"[UNDO] Removed {n_queued} queued task(s).")

        self.last_dispatch = None
        parts = []
        if n_assigned:
            parts.append(f"{n_assigned} porter assignment(s) reversed")
        if n_queued:
            parts.append(f"{n_queued} queued task(s) removed")
        return True, "Undo successful: " + ", ".join(parts) + "."

# --- MODIFIED: `if __name__ == "__main__":` ---
# This block now correctly handles different types of errors
if __name__ == "__main__":
    try:
        print("--- Initializing Porter Dispatch System ---")
        system = PorterDispatchSystem()
        print("-------------------------------------------")

        print("\n--- SCENARIO 1: New urgent task (LIVE API CALL) ---")
        system.dispatch_new_task("urgent patient 7H to 3FXRAY with o2")
        
        print("\n--- SCENARIO 2: Another new task (LIVE API CALL) ---")
        system.dispatch_new_task("specimen from lab to A&D")
        
        print("\n--- SCENARIO 3: A third task (LIVE API CALL) ---")
        system.dispatch_new_task("body from 6H to mortuary, immediate")
    
    except ValueError as e: # This ONLY catches the API key error
        print(f"\n--- SIMULATION FAILED: API KEY ERROR ---")
        print(f"Error: {e}")
        print("Please set your OPENAI_API_KEY environment variable to run the simulation.")
    except Exception as e: # This catches all OTHER errors
        print(f"\n--- SIMULATION FAILED: UNEXPECTED ERROR ---")
        print(f"An unexpected error occurred: {e}")
        # This will print the full error report
        traceback.print_exc()
# --- End of Modification ---