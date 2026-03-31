"""
LLM Policy Advisor for Porter Dispatch

Provides:
1. KPI risk assessment for new tasks (based on similar historical tasks)
2. Policy tuning suggestions based on recent performance
3. Natural language Q&A about operations

Uses retrieval-augmented generation (RAG) over historical task data from DATA2024.xlsx.
"""

import os
import json
from datetime import datetime

import openai
import pandas as pd


class HistoricalTaskStore:
    """
    In-memory store of historical porter tasks from DATA2024.xlsx.
    Supports retrieval of similar tasks by service type, location, and time of day.
    """

    def __init__(self, xlsx_path, max_rows=5000):
        self.tasks = []
        self._load(xlsx_path, max_rows)

    def _load(self, xlsx_path, max_rows):
        """Load and parse historical tasks."""
        if not os.path.exists(xlsx_path):
            print(f"[Advisor] Historical data not found: {xlsx_path}")
            return

        try:
            df = pd.read_excel(xlsx_path, nrows=max_rows)
            df.columns = df.columns.str.strip()

            required = ['下單', '從', '往', '服務']
            for col in required:
                if col not in df.columns:
                    print(f"[Advisor] Missing column: {col}")
                    return

            priority_map = {'即時': 'Urgent', '超緊急': 'Super-Urgent'}

            for _, row in df.iterrows():
                if pd.isna(row.get('下單')) or pd.isna(row.get('從')) or pd.isna(row.get('往')):
                    continue

                ordered = row['下單']
                completed = row.get('完成')

                # Calculate actual duration if both timestamps exist
                actual_duration = None
                if pd.notna(ordered) and pd.notna(completed):
                    try:
                        actual_duration = (completed - ordered).total_seconds() / 60.0
                        if actual_duration < 0 or actual_duration > 1440:  # skip invalid (> 24h)
                            actual_duration = None
                    except Exception:
                        pass

                hour = ordered.hour if hasattr(ordered, 'hour') else None

                task = {
                    'from': str(row['從']).strip(),
                    'to': str(row['往']).strip(),
                    'service': str(row['服務']).strip(),
                    'priority': priority_map.get(str(row.get('優先', '')).strip(), 'Normal'),
                    'hour': hour,
                    'actual_duration_min': actual_duration,
                    'infection_control': str(row.get('感染控制', '')).strip() if pd.notna(row.get('感染控制')) else None,
                }
                self.tasks.append(task)

            print(f"[Advisor] Loaded {len(self.tasks)} historical tasks")

        except Exception as e:
            print(f"[Advisor] Error loading historical data: {e}")

    def find_similar(self, task, k=10):
        """
        Retrieve k most similar historical tasks.
        Similarity: same service > same origin > same destination > same time-of-day bucket.
        """
        if not self.tasks:
            return []

        def similarity_score(historical):
            score = 0
            if historical['service'] == task.get('service'):
                score += 4
            if historical['from'] == task.get('from'):
                score += 2
            if historical['to'] == task.get('to'):
                score += 2
            # Time-of-day proximity (within 2 hours)
            task_hour = datetime.now().hour
            if historical.get('hour') is not None and abs(historical['hour'] - task_hour) <= 2:
                score += 1
            return score

        scored = [(similarity_score(h), h) for h in self.tasks if h.get('actual_duration_min') is not None]
        scored.sort(key=lambda x: -x[0])
        return [h for _, h in scored[:k]]

    def get_performance_summary(self, kpi_limit=15.0):
        """Compute summary statistics from historical data."""
        durations = [t['actual_duration_min'] for t in self.tasks if t.get('actual_duration_min') is not None]
        if not durations:
            return {"error": "No duration data available"}

        violations = [d for d in durations if d > kpi_limit]
        by_service = {}
        for t in self.tasks:
            if t.get('actual_duration_min') is None:
                continue
            svc = t['service']
            if svc not in by_service:
                by_service[svc] = []
            by_service[svc].append(t['actual_duration_min'])

        service_stats = {}
        for svc, durs in by_service.items():
            service_stats[svc] = {
                'count': len(durs),
                'mean_duration': round(sum(durs) / len(durs), 1),
                'kpi_violation_rate': round(len([d for d in durs if d > kpi_limit]) / len(durs) * 100, 1),
            }

        return {
            'total_tasks': len(durations),
            'mean_duration': round(sum(durations) / len(durations), 1),
            'median_duration': round(sorted(durations)[len(durations) // 2], 1),
            'kpi_violation_rate': round(len(violations) / len(durations) * 100, 1),
            'by_service': service_stats,
        }


class PolicyAdvisor:
    """
    LLM-powered policy advisor that uses historical data to provide
    KPI risk assessments and policy tuning suggestions.
    """

    def __init__(self, historical_store, api_key=None, base_url="https://api.deepseek.com"):
        self.store = historical_store
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY")
        self.client = None
        if self.api_key:
            self.client = openai.OpenAI(api_key=self.api_key, base_url=base_url)
            print("[Advisor] LLM client initialized")
        else:
            print("[Advisor] No API key; advisor will return data-only responses")

    def assess_kpi_risk(self, task, queue_length=0, busy_porters=0, total_porters=3):
        """
        Assess KPI risk for a new task based on similar historical tasks.
        Returns: dict with risk_level, reasoning, similar_tasks_summary
        """
        similar = self.store.find_similar(task, k=10)

        if not similar:
            return {
                'risk_level': 'Unknown',
                'reasoning': 'No similar historical tasks found for comparison.',
                'similar_tasks': []
            }

        durations = [t['actual_duration_min'] for t in similar if t['actual_duration_min'] is not None]
        mean_dur = sum(durations) / len(durations) if durations else 0
        kpi_violations = len([d for d in durations if d > 15.0])

        # Heuristic risk assessment
        utilization = busy_porters / total_porters if total_porters > 0 else 0
        risk_score = 0
        if mean_dur > 15:
            risk_score += 2
        if kpi_violations > len(durations) * 0.5:
            risk_score += 2
        if queue_length > 0:
            risk_score += 1
        if utilization > 0.8:
            risk_score += 2

        if risk_score >= 4:
            risk_level = 'High'
        elif risk_score >= 2:
            risk_level = 'Medium'
        else:
            risk_level = 'Low'

        summary = {
            'similar_count': len(similar),
            'mean_historical_duration': round(mean_dur, 1),
            'historical_kpi_violation_rate': round(kpi_violations / len(durations) * 100, 1) if durations else 0,
        }

        # LLM-enhanced reasoning
        reasoning = (
            f"Based on {len(similar)} similar past tasks: mean duration {mean_dur:.1f} min, "
            f"{kpi_violations}/{len(durations)} exceeded 15-min KPI. "
            f"Current load: {busy_porters}/{total_porters} porters busy, {queue_length} queued."
        )

        if self.client:
            try:
                prompt = f"""You are a hospital logistics advisor. Assess KPI risk in 2 sentences.

New task: {task.get('service')} from {task.get('from')} to {task.get('to')} (priority: {task.get('priority', 'Normal')})
Historical data: {len(similar)} similar tasks, mean duration {mean_dur:.1f} min, {kpi_violations}/{len(durations)} violated 15-min KPI
Current state: {busy_porters}/{total_porters} porters busy, {queue_length} tasks queued
Risk level: {risk_level}

Be specific about numbers. If risk is high, suggest one actionable mitigation."""

                response = self.client.chat.completions.create(
                    model="deepseek-chat",
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=100
                )
                reasoning = response.choices[0].message.content.strip()
            except Exception as e:
                print(f"[Advisor] LLM error: {e}")

        return {
            'risk_level': risk_level,
            'reasoning': reasoning,
            'similar_tasks': summary,
        }

    def get_suggestions(self, system_state=None):
        """
        Generate policy tuning suggestions based on historical performance.
        system_state: dict with current queue_length, busy_porters, total_porters, recent_kpi_violations
        """
        perf = self.store.get_performance_summary()
        if 'error' in perf:
            return {'suggestions': [], 'performance': perf}

        state = system_state or {}

        # Data-driven suggestions
        suggestions = []
        if perf['kpi_violation_rate'] > 50:
            suggestions.append(
                f"KPI violation rate is {perf['kpi_violation_rate']}% historically. "
                "Consider increasing porter fleet size or implementing task batching."
            )

        # Find worst-performing service types
        for svc, stats in perf.get('by_service', {}).items():
            if stats['kpi_violation_rate'] > 70:
                suggestions.append(
                    f"Service '{svc}' has {stats['kpi_violation_rate']}% KPI violation rate "
                    f"(mean {stats['mean_duration']} min). Consider dedicated porters or priority routing."
                )

        # LLM-enhanced suggestions
        if self.client:
            try:
                prompt = f"""You are a hospital logistics advisor. Based on this performance data, give 2-3 concise, actionable suggestions to improve porter dispatch KPI compliance.

Performance summary:
- Total tasks analyzed: {perf['total_tasks']}
- Mean duration: {perf['mean_duration']} min
- KPI violation rate: {perf['kpi_violation_rate']}%
- By service: {json.dumps(perf.get('by_service', {}), ensure_ascii=False, indent=2)}

Current state: {json.dumps(state, ensure_ascii=False)}

Each suggestion should be 1-2 sentences. Focus on what's actionable within a hospital porter system."""

                response = self.client.chat.completions.create(
                    model="deepseek-chat",
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=200
                )
                llm_suggestions = response.choices[0].message.content.strip()
                suggestions.append(llm_suggestions)
            except Exception as e:
                print(f"[Advisor] LLM error: {e}")

        return {
            'suggestions': suggestions,
            'performance': perf,
        }

    def ask(self, question, system_state=None):
        """
        Answer a natural language question about operations.
        Uses historical data as context.
        """
        perf = self.store.get_performance_summary()
        state = system_state or {}

        if not self.client:
            return {
                'answer': 'LLM not available. Here is the raw performance data.',
                'data': perf
            }

        try:
            prompt = f"""You are a hospital porter dispatch advisor. Answer this question concisely using the data provided.

Question: {question}

Historical performance:
- Total tasks: {perf.get('total_tasks', 'N/A')}
- Mean duration: {perf.get('mean_duration', 'N/A')} min
- KPI violation rate: {perf.get('kpi_violation_rate', 'N/A')}%
- By service: {json.dumps(perf.get('by_service', {}), ensure_ascii=False)}

Current system state: {json.dumps(state, ensure_ascii=False)}

Answer in 2-4 sentences. Use specific numbers from the data."""

            response = self.client.chat.completions.create(
                model="deepseek-chat",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=200
            )
            return {'answer': response.choices[0].message.content.strip()}
        except Exception as e:
            return {'answer': f'Error: {e}', 'data': perf}
