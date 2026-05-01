"""
NLP Accuracy Test — measures how accurately the LLM parses structured hospital
dispatch tasks reconstructed from DATA2024.xlsx ground-truth records.

Usage:
    python nlp_accuracy_test.py                  # 100 samples, default output
    python nlp_accuracy_test.py --samples 20     # quick smoke-test
    python nlp_accuracy_test.py --samples 100 --output results.csv
"""

import argparse
import csv
import os
import sys
import random

import pandas as pd
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Service mapping: Excel's 36 service types → 5 LLM-valid service types
# Rows whose Excel service doesn't map are skipped (unmappable).
# ---------------------------------------------------------------------------
SERVICE_MAP = {
    '送入院':     '送入院',
    '回人':       '送病人',
    '送病人':     '送病人',
    'NEATS病人':  '送病人',
    '送X光':      '送3F X光',
    '送3F X光':   '送3F X光',
    '送標本':     '送標本',
    '運送遺體':   '運送遺體',
}

# Priority mapping: Excel Chinese → LLM priority string
PRIORITY_MAP = {
    '即時':   'Urgent',
    '超緊急': 'Super-Urgent',
}

# Infection control keywords — any of these in 感染控制 column = flagged
INFECTION_KEYWORDS = ['VRE', 'NEATS', 'MRSA', 'MRDA', 'T.B.', 'TB', '接觸']


def load_and_filter(xlsx_path: str, sample_size: int, seed: int = 42):
    """Load DATA2024.xlsx, filter to testable rows, return random sample."""
    print(f"[Data] Loading {xlsx_path} (first 5000 rows)...")
    df = pd.read_excel(xlsx_path, nrows=5000)
    df.columns = [c.strip() for c in df.columns]

    # Keep only rows with all required fields populated
    required = ['從', '往', '服務', '優先']
    df = df.dropna(subset=required)

    # Map service to LLM service type; drop unmappable rows
    df['service_zh'] = df['服務'].astype(str).str.strip()
    df['gt_service'] = df['service_zh'].map(SERVICE_MAP)
    df = df.dropna(subset=['gt_service'])

    # Map priority
    df['priority_zh'] = df['優先'].astype(str).str.strip()
    df['gt_priority'] = df['priority_zh'].map(PRIORITY_MAP).fillna('Normal')

    # Normalise locations
    df['gt_from'] = df['從'].astype(str).str.strip()
    df['gt_to'] = df['往'].astype(str).str.strip()

    # Infection flag
    def has_infection(val):
        if pd.isna(val) or not str(val).strip():
            return False
        v = str(val)
        return any(kw in v for kw in INFECTION_KEYWORDS)

    infection_col = '感染控制' if '感染控制' in df.columns else None
    if infection_col:
        df['gt_infection'] = df[infection_col].apply(has_infection)
    else:
        df['gt_infection'] = False

    print(f"[Data] {len(df)} testable rows after filtering.")
    sample = df.sample(n=min(sample_size, len(df)), random_state=seed)
    return sample.reset_index(drop=True)


def build_sentence(row) -> str:
    """Reconstruct a natural-language dispatch sentence from structured fields."""
    parts = []
    if row['gt_priority'] != 'Normal':
        parts.append(row['priority_zh'])
    parts.append(f"{row['service_zh']} 從 {row['gt_from']} 往 {row['gt_to']}")
    if row['gt_infection']:
        parts.append('，需注意感染控制')
    return ' '.join(parts)


def parse_llm_infection(task: dict) -> bool:
    """Detect infection flag from LLM output — checks equipment list for keywords."""
    equipment = task.get('equipment', [])
    if not isinstance(equipment, list):
        return False
    combined = ' '.join(str(e) for e in equipment).upper()
    return any(kw.upper() in combined for kw in ['VRE', 'NEATS', 'MRSA', 'MRDA', 'T.B.', 'INFECTION', '感染'])


def run_test(samples: int, output_path: str):
    xlsx_path = os.path.join(os.path.dirname(__file__), '..', 'DATA2024.xlsx')
    if not os.path.exists(xlsx_path):
        print(f"[Error] DATA2024.xlsx not found at {xlsx_path}")
        sys.exit(1)

    df = load_and_filter(xlsx_path, samples)

    # Import LLM — done after data load so startup errors are obvious
    sys.path.insert(0, os.path.dirname(__file__))
    from porter_prototype import ChatGPTLLM
    print("[LLM] Initializing DeepSeek client...")
    llm = ChatGPTLLM()

    results = []
    counters = {
        'priority': 0, 'service': 0, 'from': 0, 'to': 0,
        'infection_tp': 0, 'infection_fp': 0, 'infection_tn': 0, 'infection_fn': 0,
        'all_correct': 0, 'llm_failed': 0,
    }
    total = len(df)

    print(f"\n[Test] Running {total} samples...\n")

    for i, row in df.iterrows():
        sentence = build_sentence(row)
        gt = {
            'priority': row['gt_priority'],
            'service':  row['gt_service'],
            'from':     row['gt_from'],
            'to':       row['gt_to'],
            'infection': row['gt_infection'],
        }

        tasks = llm.get_structured_tasks(sentence)
        if not tasks:
            counters['llm_failed'] += 1
            results.append({
                'sentence': sentence,
                'gt_priority': gt['priority'], 'llm_priority': '', 'priority_match': False,
                'gt_service':  gt['service'],  'llm_service':  '', 'service_match':  False,
                'gt_from':     gt['from'],     'llm_from':     '', 'from_match':     False,
                'gt_to':       gt['to'],       'llm_to':       '', 'to_match':       False,
                'gt_infection': gt['infection'], 'llm_infection': False, 'infection_match': False,
                'all_correct': False, 'llm_failed': True,
            })
            continue

        task = tasks[0]
        llm_priority  = task.get('priority', '')
        llm_service   = task.get('service', '')
        llm_from      = task.get('from', '')
        llm_to        = task.get('to', '')
        llm_infection = parse_llm_infection(task)

        m_priority  = (llm_priority == gt['priority'])
        m_service   = (llm_service  == gt['service'])
        m_from      = (llm_from     == gt['from'])
        m_to        = (llm_to       == gt['to'])

        # Infection confusion matrix
        if gt['infection'] and llm_infection:
            counters['infection_tp'] += 1
            m_infection = True
        elif gt['infection'] and not llm_infection:
            counters['infection_fn'] += 1
            m_infection = False
        elif not gt['infection'] and llm_infection:
            counters['infection_fp'] += 1
            m_infection = False
        else:
            counters['infection_tn'] += 1
            m_infection = True

        all_ok = all([m_priority, m_service, m_from, m_to, m_infection])

        if m_priority:  counters['priority'] += 1
        if m_service:   counters['service']  += 1
        if m_from:      counters['from']     += 1
        if m_to:        counters['to']       += 1
        if all_ok:      counters['all_correct'] += 1

        results.append({
            'sentence': sentence,
            'gt_priority': gt['priority'], 'llm_priority': llm_priority, 'priority_match': m_priority,
            'gt_service':  gt['service'],  'llm_service':  llm_service,  'service_match':  m_service,
            'gt_from':     gt['from'],     'llm_from':     llm_from,     'from_match':     m_from,
            'gt_to':       gt['to'],       'llm_to':       llm_to,       'to_match':       m_to,
            'gt_infection': gt['infection'], 'llm_infection': llm_infection, 'infection_match': m_infection,
            'all_correct': all_ok, 'llm_failed': False,
        })

        n_done = len(results)
        print(f"  [{n_done}/{total}] priority={'✓' if m_priority else '✗'} "
              f"service={'✓' if m_service else '✗'} "
              f"from={'✓' if m_from else '✗'} "
              f"to={'✓' if m_to else '✗'} "
              f"infection={'✓' if m_infection else '✗'}")

    # Save CSV
    with open(output_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=results[0].keys())
        writer.writeheader()
        writer.writerows(results)
    print(f"\n[Output] Results saved to {output_path}")

    # Print summary
    responded = total - counters['llm_failed']
    tp = counters['infection_tp']
    fp = counters['infection_fp']
    fn = counters['infection_fn']
    precision = tp / (tp + fp) if (tp + fp) > 0 else float('nan')
    recall    = tp / (tp + fn) if (tp + fn) > 0 else float('nan')

    print(f"""
NLP Accuracy Test Results  (n={total})
{'='*42}
  Priority extraction:    {counters['priority']}/{responded}  ({counters['priority']/responded*100:.1f}%)
  Service classification: {counters['service']}/{responded}  ({counters['service']/responded*100:.1f}%)
  Origin location:        {counters['from']}/{responded}  ({counters['from']/responded*100:.1f}%)
  Destination location:   {counters['to']}/{responded}  ({counters['to']/responded*100:.1f}%)
  Infection flag recall:  {tp}/{tp+fn}  ({recall*100:.1f}%)
  Infection flag precision:{tp}/{tp+fp}  ({precision*100:.1f}%)

  All 5 fields correct:   {counters['all_correct']}/{responded}  ({counters['all_correct']/responded*100:.1f}%)
  LLM parse failures:     {counters['llm_failed']}/{total}
{'='*42}
""")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='NLP accuracy test for porter dispatch LLM.')
    parser.add_argument('--samples', type=int, default=100, help='Number of test samples (default: 100)')
    parser.add_argument('--output', type=str, default='nlp_accuracy_results.csv', help='Output CSV path')
    parser.add_argument('--seed', type=int, default=42, help='Random seed for reproducibility')
    args = parser.parse_args()

    run_test(args.samples, args.output)
