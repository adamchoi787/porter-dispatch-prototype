import json
import os
import sys  # Added sys
from flask import Flask, request, jsonify, send_from_directory
# We import the classes and system from our other file
from porter_prototype import PorterDispatchSystem, Porter

# --- Initialize Flask App ---
app = Flask(__name__, static_folder='static')

# --- Initialize the Dispatch System ---
try:
    system = PorterDispatchSystem()
    print("--- Porter Dispatch System Initialized for API (using OpenAI) ---")
    
    # Set all porters to 'available' for a fresh API start
    system.simulate_task_completion('P-001')
    system.simulate_task_completion('P-002')
    system.simulate_task_completion('P-003')
    print("--- All porters set to 'available' for API testing ---")

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
        
    data = request.json
    user_request = data.get('request')
    
    if not user_request:
        return jsonify({"error": "No 'request' field in JSON"}), 400
        
    print(f"[API] Received request (sending to OpenAI): {user_request}")
    
    # Use our system's main function
    result = system.dispatch_new_task(user_request)
    
    if result:
        porter, task, duration = result
        response = {
            "status": "Task Assigned",
            "porter_id": porter.id,
            "porter_location": porter.current_location,
            "task": task,
            "estimated_duration_mins": round(duration, 1)
        }
    else:
        # This means the task was queued
        response = {
            "status": "Task Queued",
            "message": "All porters are currently busy. Task added to queue.",
            "queue_length": len(system.task_queue)
        }
        
    return jsonify(response)

@app.route('/status', methods=['GET'])
def get_status():
    """API endpoint to get the current status of all porters."""
    if not system:
        return jsonify({"error": "System not initialized"}), 500
        
    porter_statuses = [
        {
            "id": p.id,
            "status": p.status,
            "location": p.current_location,
            "task": p.current_task['service'] if p.current_task else 'None'
        } for p in system.porters
    ]
    
    response = {
        "porters": porter_statuses,
        "queue_length": len(system.task_queue)
    }
    return jsonify(response)

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