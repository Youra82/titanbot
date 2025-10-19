# src/jaegerbot/analysis/find_best_threshold.py
import os
import sys
import argparse
import numpy as np
import pandas as pd

# Pfad-Setup
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
sys.path.append(os.path.join(PROJECT_ROOT, 'src'))

from jaegerbot.analysis.backtester import load_data
from jaegerbot.utils.ann_model import load_model_and_scaler, prepare_data_for_ann

def find_best_threshold(symbol: str, timeframe: str, start_date: str, end_date: str):
    """
    Analysiert ein trainiertes Modell, um den besten prediction_threshold zu finden,
    der die Balance zwischen Signalqualität (Trefferquote) und Quantität (Anzahl) optimiert.
    """
    print(f"--- Starte Threshold-Analyse für {symbol} ({timeframe}) ---")
    
    # 1. Modell und Daten laden
    safe_filename = f"{symbol.replace('/', '').replace(':', '')}_{timeframe}"
    model_path = os.path.join(PROJECT_ROOT, 'artifacts', 'models', f'ann_predictor_{safe_filename}.h5')
    scaler_path = os.path.join(PROJECT_ROOT, 'artifacts', 'models', f'ann_scaler_{safe_filename}.joblib')
    
    model, scaler = load_model_and_scaler(model_path, scaler_path)
    if not model or not scaler:
        print("❌ Fehler: Modell/Scaler nicht gefunden. Training muss zuerst laufen.")
        return None

    data = load_data(symbol, timeframe, start_date, end_date)
    if data.empty:
        print("❌ Fehler: Keine Daten zum Analysieren gefunden.")
        return None

    # 2. Vorhersagen für den gesamten Datensatz einmalig erstellen
    X, y_true = prepare_data_for_ann(data, timeframe, verbose=False)
    if X.empty:
        print("❌ Fehler: Keine Handelssignale im Datensatz gefunden.")
        return None
        
    predictions = model.predict(scaler.transform(X), verbose=0).flatten()
    
    results = []
    best_score = -1
    best_threshold = 0.65 # Fallback-Wert

    # 3. Alle möglichen Thresholds durchgehen und bewerten
    for threshold in np.arange(0.60, 0.96, 0.01):
        threshold = round(threshold, 2)
        
        # Signale basierend auf dem aktuellen Threshold filtern
        long_signals = predictions >= threshold
        short_signals = predictions <= (1 - threshold)
        total_signals = np.sum(long_signals) + np.sum(short_signals)

        if total_signals < 50: # Mindestanzahl an Signalen
            continue

        # Korrekte Vorhersagen zählen
        correct_longs = np.sum(y_true[long_signals] == 1)
        correct_shorts = np.sum(y_true[short_signals] == 0)
        total_correct = correct_longs + correct_shorts
        
        win_rate = total_correct / total_signals
        
        # 4. Den "Sweet Spot"-Score berechnen
        # Score = (Edge über 50%) * Wurzel(Anzahl der Signale)
        score = (win_rate - 0.5) * np.sqrt(total_signals)
        
        results.append({
            "Threshold": threshold,
            "Signale": total_signals,
            "Trefferquote": f"{win_rate:.2%}",
            "Score": score
        })
        
        if score > best_score:
            best_score = score
            best_threshold = threshold

    # 5. Ergebnisse anzeigen
    if not results:
        print("❌ Konnte keinen geeigneten Threshold mit genügend Signalen finden.")
        return None
        
    results_df = pd.DataFrame(results)
    print("\n--- Threshold-Analyse-Ergebnisse ---")
    print(results_df.to_string(index=False))
    
    print(f"\n✅ Bester gefundener Threshold: {best_threshold} (Score: {best_score:.2f})")
    
    # 6. Den besten Wert für das Pipeline-Skript ausgeben
    return best_threshold

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Findet den optimalen Prediction Threshold.")
    parser.add_argument('--symbol', required=True, type=str)
    parser.add_argument('--timeframe', required=True, type=str)
    parser.add_argument('--start_date', required=True, type=str)
    parser.add_argument('--end_date', required=True, type=str)
    args = parser.parse_args()
    
    # Der finale print-Befehl gibt den Wert an das aufrufende Shell-Skript zurück
    best_value = find_best_threshold(f"{args.symbol}/USDT:USDT", args.timeframe, args.start_date, args.end_date)
    if best_value:
        print(f"\n--- Output für Pipeline ---")
        print(best_value)

