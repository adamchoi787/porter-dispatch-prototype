import json
import time
import os
import openai
import traceback  # <-- Added for better error messages

# --- Step 1: The "Map" (Data Foundation) ---
TRAVEL_TIME_MATRIX = {
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
        self.task_completion_time = None
        print(f"Porter {self.id} created at {self.current_location}")

    def assign_task(self, task, estimated_total_duration):
        self.status = 'busy'
        self.current_task = task
        self.task_completion_time = estimated_total_duration
        self.current_location = task['to']
        print(f"  [ASSIGNED] Porter {self.id} assigned task. Est. duration: {estimated_total_duration:.1f} mins.")
        print(f"             Task: {task['service']} from {task['from']} to {task['to']}.")
        print(f"             Porter will be at {self.current_location} and 'available' after this task.")

    def complete_task(self):
        if self.status == 'busy':
            print(f"  [COMPLETED] Porter {self.id} finished task at {self.current_location}. Now available.")
            self.status = 'available'
            self.current_task = None
            self.task_completion_time = None
        else:
            print(f"  [INFO] Porter {self.id} is already available.")

class ChatGPTLLM:
    """
    Uses the OpenAI API (ChatGPT) to parse user requests.
    """
    def __init__(self):
        self.api_key = os.environ.get("OPENAI_API_KEY")
        if not self.api_key:
            raise ValueError("OPENAI_API_KEY environment variable not set. Please set it before running.")
        
        self.client = openai.OpenAI(api_key=self.api_key)
        self.system_prompt = self._build_system_prompt()
        print("ChatGPTLLM Initialized.")

    def _build_system_prompt(self):
        """Creates the detailed instruction prompt for the AI."""
        valid_locations = list(TRAVEL_TIME_MATRIX.keys())
        valid_services = list(SERVICE_TASK_TIME.keys())

        prompt = f"""
        You are a hospital dispatch system. Your job is to parse a user request
        into a structured JSON object. Respond ONLY with the JSON object.

        The JSON object must have this exact format:
        {{
          "from": "ORIGIN_LOCATION",
          "to": "DESTINATION_LOCATION",
          "service": "SERVICE_TYPE",
          "priority": "PRIORITY_LEVEL",
          "equipment": ["EQUIPMENT_1", "EQUIPMENT_2", ...]
        }}

        RULES:
        1.  "from" and "to" MUST be one of the following valid locations: {valid_locations}
        2.  "service" MUST be one of the following valid services: {valid_services}
        3.  "priority" should be 'Normal', 'Urgent', or 'Super-Urgent'.
        4.  "equipment" should be a list of items needed (e.g., "Wheelchair", "O2", "Stretcher"). If none are mentioned, return an empty list [].
        5.  If you cannot determine a value, use "Unknown".
        """
        return prompt

    def get_structured_task(self, user_request):
        """Parses the natural language request into a structured dict."""
        print(f"\n[LLM-OpenAI] Parsing request: '{user_request}'")
        
        try:
            response = self.client.chat.completions.create(
                model="gpt-3.5-turbo",
                messages=[
                    {"role": "system", "content": self.system_prompt},
                    {"role": "user", "content": user_request}
                ],
                response_format={"type": "json_object"}
            )
            
            parsed_json = json.loads(response.choices[0].message.content)
            print(f"[LLM-OpenAI] Parsed data: {json.dumps(parsed_json)}")
            return parsed_json
            
        except Exception as e:
            print(f"[LLM-OpenAI] Error parsing request: {e}")
            return None

class PorterDispatchSystem:
    """
    The main class that orchestrates the entire system.
    """
    def __init__(self):
        self.travel_matrix = TRAVEL_TIME_MATRIX
        self.task_time_map = SERVICE_TASK_TIME
        self.porters = [
            Porter('P-001', 'At-Base'),
            Porter('P-002', '7H'),
            Porter('P-003', 'A&D')
        ]
        self.llm_engine = ChatGPTLLM()
        self.task_queue = []

    # --- MODIFIED: predict_travel_time ---
    def predict_travel_time(self, origin, destination):
        """Prediction Model (Travel)."""
        # 1. Added check for same location
        if origin == destination:
            return 0.0
            
        try:
            # 2. Added float() cast for safety
            return float(self.travel_matrix[origin][destination])
        except KeyError:
            if origin not in self.travel_matrix or destination not in self.travel_matrix.get(origin, {}):
                print(f"  [Warning] No direct path found from {origin} to {destination}. Using default 5 min.")
            # 3. Ensured return is always a float
            return 5.0
    # --- End of Modification ---

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
        """Main orchestration function."""
        structured_task = self.llm_engine.get_structured_task(user_request)
        if not structured_task:
            print("[System] LLM failed to parse request. Aborting.")
            return None

        if "from" not in structured_task or "to" not in structured_task:
            print(f"[System] LLM parsing error. Invalid task: {structured_task}")
            return None

        best_porter, time_to_origin = self.find_best_porter(structured_task)
        if not best_porter:
            print("[System] Task queued. No porters available.")
            self.task_queue.append(structured_task)
            return None

        travel_time_leg_2 = self.predict_travel_time(structured_task['from'], structured_task['to'])
        task_time_at_dest = self.predict_task_time(structured_task['service'])
        
        # All three of these are now guaranteed to be floats
        total_estimated_duration = time_to_origin + travel_time_leg_2 + task_time_at_dest

        best_porter.assign_task(structured_task, total_estimated_duration)
        return best_porter, structured_task, total_estimated_duration

    def simulate_task_completion(self, porter_id):
        """Helper function to simulate a porter finishing a task."""
        porter = next((p for p in self.porters if p.id == porter_id), None)
        if porter:
            porter.complete_task()
            if self.task_queue:
                print(f"[System] Porter {porter.id} is now available. Checking task queue...")
                queued_task = self.task_queue.pop(0)
                print(f"--- Re-dispatching queued task: {queued_task['service']} from {queued_task['from']} ---")
                self.assign_specific_task(queued_task)
        else:
            print(f"[System] Porter {porter_id} not found.")
            
    def assign_specific_task(self, structured_task):
        """A variation of dispatch_new_task for already-parsed queued tasks."""
        best_porter, time_to_origin = self.find_best_porter(structured_task)
        if not best_porter:
            print("[System] Task re-queued. No porters available.")
            self.task_queue.insert(0, structured_task)
            return None

        travel_time_leg_2 = self.predict_travel_time(structured_task['from'], structured_task['to'])
        task_time_at_dest = self.predict_task_time(structured_task['service'])
        total_estimated_duration = time_to_origin + travel_time_leg_2 + task_time_at_dest

        best_porter.assign_task(structured_task, total_estimated_duration)
        return best_porter, structured_task, total_estimated_duration

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