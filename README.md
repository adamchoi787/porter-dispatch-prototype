# Porter Dispatch System Prototype (MVP)

A smart hospital logic simulation that assigns porter tasks based on real-time availability and location. This prototype utilizes a **Greedy Algorithm** for optimization and **OpenAI's GPT-3.5** to parse natural language dispatch commands.

## Table of Contents
- [About the Project](#about-the-project)
- [Project Structure](#project-structure)
- [Prerequisites](#prerequisites)
- [Installation & Setup](#installation--setup)
- [Usage](#usage)
- [Technical Architecture](#technical-architecture)

## About the Project
This project is a Proof of Concept (PoC) for an automated dispatch system designed to improve efficiency in hospital logistics.

**Key Features:**
* **Natural Language Processing (NLP):** Uses OpenAI to parse complex unstructured requests (e.g., "Urgent transfer from 7H to XRay with O2") into structured JSON data.
* **Greedy Optimization Algorithm:** Automatically assigns the nearest available porter to minimize wait times.
* **Real-time Simulation:** Tracks porter status (Available/Busy), current location, and estimated task duration.
* **Web Interface:** A responsive dashboard built with Flask and Tailwind CSS to monitor operations.

## Project Structure
```text
porter-dispatch-prototype/
├── app.py                  # Main Flask web server
├── porter_prototype.py     # Core logic (Dispatch algorithm & LLM integration)
├── static/
│   └── index.html          # Frontend dashboard
├── requirements.txt        # (Optional) List of dependencies
└── README.md               # Project documentation