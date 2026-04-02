import json
import os
import sys
from dotenv import load_dotenv
from flask import Flask, request, jsonify, send_from_directory
from porter_prototype import PorterDispatchSystem, Porter
from advisor import HistoricalTaskStore, PolicyAdvisor

# Load environment variables from .env file
load_dotenv()

# --- Initialize Flask App ---
app = Flask(__name__, static_folder='static')

# --- Initialize the Dispatch System ---
try:
    num_porters = int(os.getenv('NUM_PORTERS', 10))
    system = PorterDispatchSystem(num_porters=num_porters)
    print("--- Porter Dispatch System Initialized for API (using OpenAI) ---")

    # Set all porters to 'available' for a fresh API start
    for p in system.porters:
        p.status = 'available'
    print(f"--- {len(system.porters)} porters set to 'available' for API testing ---")

    # Initialize policy advisor with historical data
    data_path = os.path.join(os.path.dirname(__file__), '..', 'DATA2024.xlsx')
    historical_store = HistoricalTaskStore(data_path, max_rows=3000)
    advisor = PolicyAdvisor(historical_store)
    print("--- Policy Advisor initialized ---")

# --- MODIFIED: Catch the API Key error ---
except ValueError as e:
    print("--- FATAL ERROR ---")
    print(f"Error: {e}")
    print("Please set your OPENAI_API_KEY environment variable before running the app.")
    sys.exit(1) # Exit the app if the key is not set
# --- End of Modification ---
except Exception as e:
    print(f"Error initializing system: {e}")
    system = None

# --- API Endpoints ---

@app.route('/')
def index():
    """Serves the main index.html file from the 'static' folder."""
    return send_from_directory(app.static_folder, 'index.html')

@app.route('/dispatch', methods=['POST'])
def handle_dispatch():
    """The main API endpoint for dispatching a new task."""
    if not system:
        return jsonify({"error": "System not initialized"}), 500

    try:
        data = request.json
        if not data:
            return jsonify({"error": "Empty request body"}), 400

        user_request = data.get('request')

        if not user_request:
            return jsonify({"error": "No 'request' field in JSON"}), 400

        if not isinstance(user_request, str):
            return jsonify({"error": "'request' must be a string"}), 400

        print(f"[API] Received request (sending to OpenAI): {user_request}")

        results = system.dispatch_new_task(user_request)

        assignments = []
        queued_count = 0
        for result in results:
            if result:
                porter, task, duration = result
                available = [p for p in system.porters if p.status == 'available' or p.id == porter.id]
                explanation = system.explain_dispatch(porter, task, duration, available)
                assignments.append({
                    "status": "Task Assigned",
                    "porter_id": porter.id,
                    "porter_location": porter.current_location,
                    "task": task,
                    "estimated_duration_mins": round(duration, 1),
                    "explanation": explanation
                })
            else:
                queued_count += 1

        response = {
            "assignments": assignments,
            "queued": queued_count,
            "queue_length": len(system.task_queue)
        }
        return jsonify(response)
    except Exception as e:
        print(f"[API] Error processing dispatch: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/status', methods=['GET'])
def get_status():
    """API endpoint to get the current status of all porters."""
    if not system:
        return jsonify({"error": "System not initialized"}), 500

    import datetime
    now = datetime.datetime.now()
    porter_statuses = [
        {
            "id": p.id,
            "status": p.status,
            "location": p.current_location,
            "task": p.current_task['service'] if p.current_task else 'None',
            "available_at": p.available_at.isoformat() if p.available_at else None,
            "seconds_until_available": max(0, int((p.available_at - now).total_seconds())) if p.available_at else None
        } for p in system.porters
    ]

    response = {
        "porters": porter_statuses,
        "queue_length": len(system.task_queue),
        "total_porters": len(system.porters)
    }
    return jsonify(response)

@app.route('/complete/<porter_id>', methods=['POST'])
def complete_task(porter_id):
    """Manual endpoint to mark a task as complete (for testing)."""
    if not system:
        return jsonify({"error": "System not initialized"}), 500

    system.simulate_task_completion(porter_id)
    return jsonify({"status": "ok", "porter_id": porter_id, "message": "Task marked as complete"})

# --- Advisor Endpoints ---

@app.route('/advisor/risk', methods=['POST'])
def assess_risk():
    """Assess KPI risk for a task using historical data + LLM."""
    if not system:
        return jsonify({"error": "System not initialized"}), 500

    data = request.json or {}
    task = data.get('task', {})
    if not task:
        return jsonify({"error": "No 'task' field in request"}), 400

    busy = sum(1 for p in system.porters if p.status == 'busy')
    state = {
        'queue_length': len(system.task_queue),
        'busy_porters': busy,
        'total_porters': len(system.porters),
    }

    result = advisor.assess_kpi_risk(task, **state)
    return jsonify(result)


@app.route('/advisor/suggestions', methods=['GET'])
def get_suggestions():
    """Get policy tuning suggestions based on historical performance."""
    if not system:
        return jsonify({"error": "System not initialized"}), 500

    busy = sum(1 for p in system.porters if p.status == 'busy')
    state = {
        'queue_length': len(system.task_queue),
        'busy_porters': busy,
        'total_porters': len(system.porters),
    }
    result = advisor.get_suggestions(system_state=state)
    return jsonify(result)


@app.route('/advisor/ask', methods=['POST'])
def ask_advisor():
    """Ask a natural language question about operations."""
    if not system:
        return jsonify({"error": "System not initialized"}), 500

    data = request.json or {}
    question = data.get('question', '')
    if not question:
        return jsonify({"error": "No 'question' field"}), 400

    busy = sum(1 for p in system.porters if p.status == 'busy')
    state = {
        'queue_length': len(system.task_queue),
        'busy_porters': busy,
        'total_porters': len(system.porters),
    }
    result = advisor.ask(question, system_state=state)
    return jsonify(result)


# --- Main execution block ---
if __name__ == '__main__':
    if not os.path.exists('static/index.html'):
        print("Warning: 'static/index.html' not found.")
        print("Please make sure 'index.html' is in a folder named 'static'.")

    print("\n--- To run the web interface ---")
    print("1. Make sure you have Flask and OpenAI installed: pip install Flask openai")
    print("2. Make sure your OPENAI_API_KEY is set as an environment variable.")
    print("3. Run this server: python app.py")
    print("4. Open your browser to: http://127.0.0.1:5000")
    print("---------------------------------")
    
    try:
        app.run(host='0.0.0.0', port=5000, debug=True, use_reloader=False)
    except Exception as e:
        print(f"Could not start Flask app: {e}")