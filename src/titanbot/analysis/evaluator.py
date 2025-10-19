# src/titanbot/analysis/evaluator.py
import pandas as pd
import numpy as np
import ta
import sys
import os

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
sys.path.append(os.path.join(PROJECT_ROOT, 'src'))
from titanbot.strategy.smc_engine import SMCEngine # Importiere die neue Engine

def evaluate_dataset(data: pd.DataFrame, timeframe: str):
    """
    Bewertet einen Datensatz für die Optimierung und gibt eine Note von 0-10,
    basierend auf Volatilität, Marktphasen und SMC-Event-Dichte.
    """
    if data.empty or len(data) < 200:
        return {
            "score": 0,
            "justification": [
                "- Phasen-Verteilung (0/4): Nicht bewertbar. Zu wenig Daten.",
                "- Handelbarkeit (0/4): Nicht bewertbar. Zu wenig Daten.",
                "- Datenmenge (0/2): Mangelhaft. Weniger als 200 Kerzen."
            ],
            "phase_dist": {}
        }

    # --- Metrik 1: Phasen-Verteilung (max. 4 Punkte) ---
    data['ema_50'] = ta.trend.ema_indicator(data['close'], window=50)
    data['ema_200'] = ta.trend.ema_indicator(data['close'], window=200)
    data.dropna(inplace=True)

    conditions = [
        (data['close'] > data['ema_50']) & (data['ema_50'] > data['ema_200']),
        (data['close'] < data['ema_50']) & (data['ema_50'] < data['ema_200'])
    ]
    choices = ['Aufwärts', 'Abwärts']
    data['phase'] = np.select(conditions, choices, default='Seitwärts')

    phase_dist = data['phase'].value_counts(normalize=True)
    max_phase_pct = phase_dist.max()

    if max_phase_pct > 0.8: score1 = 0
    elif max_phase_pct > 0.7: score1 = 1
    elif max_phase_pct > 0.6: score1 = 2
    elif max_phase_pct > 0.5: score1 = 3
    else: score1 = 4

    dist_text = ", ".join([f"{name}: {pct:.0%}" for name, pct in phase_dist.items()])
    just1 = f"- Phasen-Verteilung ({score1}/4): {'Exzellent' if score1==4 else 'Gut' if score1==3 else 'Mäßig' if score1==2 else 'Einseitig'}. ({dist_text})"

    # --- Metrik 2: Handelbarkeit / SMC-Event-Dichte (max. 4 Punkte) ---
    try:
        # Lasse die SMC-Engine laufen, um Events zu finden
        engine = SMCEngine(settings={'swingsLength': 20}) # Kurze Länge für Event-Findung
        results = engine.process_dataframe(data.copy())
        event_count = len(results.get('events', []))
        
        # Berechne Events pro 1000 Kerzen
        event_density = (event_count / len(data)) * 1000 if len(data) > 0 else 0
    except Exception:
        event_density = 0

    if event_density < 10: score2 = 0  # Weniger als 10 Events pro 1000 Kerzen
    elif event_density < 25: score2 = 1
    elif event_density < 50: score2 = 2
    elif event_density < 100: score2 = 3
    else: score2 = 4
    just2 = f"- Handelbarkeit ({score2}/4): {'Exzellent' if score2==4 else 'Gut' if score2==3 else 'Mäßig' if score2==2 else 'Gering' if score2==1 else 'Sehr Gering'}. {event_density:.1f} Events/1000 Kerzen."

    # --- Metrik 3: Datenmenge (max. 2 Punkte) ---
    num_candles = len(data)
    if num_candles < 2000: score3 = 0
    elif num_candles < 5000: score3 = 1
    else: score3 = 2
    just3 = f"- Datenmenge ({score3}/2): {'Exzellent' if score3==2 else 'Ausreichend' if score3==1 else 'Gering'}. {num_candles:,} Kerzen."

    # --- Gesamtergebnis ---
    total_score = score1 + score2 + score3
    return {
        "score": total_score,
        "justification": [just1, just2, just3],
        "phase_dist": phase_dist.to_dict()
    }
