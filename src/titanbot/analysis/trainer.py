# src/jaegerbot/analysis/trainer.py
import os
import sys
import argparse
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import StandardScaler

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
sys.path.append(os.path.join(PROJECT_ROOT, 'src'))

from jaegerbot.utils import ann_model
from jaegerbot.analysis.backtester import load_data

def create_safe_filename(symbol, timeframe):
    return f"{symbol.replace('/', '').replace(':', '')}_{timeframe}"

def main():
    parser = argparse.ArgumentParser(description="Modell-Training für JaegerBot")
    parser.add_argument('--symbols', required=True, type=str)
    parser.add_argument('--timeframes', required=True, type=str)
    parser.add_argument('--start_date', required=True, type=str)
    parser.add_argument('--end_date', required=True, type=str)
    args = parser.parse_args()

    symbols, timeframes = args.symbols.split(), args.timeframes.split()
    TASKS = [{'symbol': f"{s}/USDT:USDT", 'timeframe': tf} for s in symbols for tf in timeframes]
    
    print("--- Starte ANN Modelltraining für mehrere Aufgaben ---")
    
    for task in TASKS:
        symbol, timeframe = task['symbol'], task['timeframe']
        print(f"\n===== Bearbeite: {symbol} ({timeframe}) =====")
        
        safe_filename = create_safe_filename(symbol, timeframe)
        model_save_path = os.path.join(PROJECT_ROOT, 'artifacts', 'models', f'ann_predictor_{safe_filename}.h5')
        scaler_save_path = os.path.join(PROJECT_ROOT, 'artifacts', 'models', f'ann_scaler_{safe_filename}.joblib')

        data = load_data(symbol, timeframe, args.start_date, args.end_date)
        if data.empty: continue

        X, y = ann_model.prepare_data_for_ann(data, timeframe)
        
        if X.empty:
            print("Fehler: Keine klaren Handelssignale im Datensatz gefunden.")
            continue

        print(f"Datensatz hat {len(X)} klare Handelssignale für das Training.")
        X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, shuffle=False)
        scaler = StandardScaler()
        X_train_scaled = scaler.fit_transform(X_train)
        X_test_scaled = scaler.transform(X_test)

        print(f"Trainiere Modell mit {len(X_train)} Signalen...")
        model = ann_model.build_and_train_model(X_train_scaled, y_train)
        
        loss, accuracy = model.evaluate(X_test_scaled, y_test, verbose=0)
        print(f"\n--- Trainingsergebnis für {symbol} ({timeframe}) ---")
        print(f"Modell-Genauigkeit auf Testdaten: {accuracy * 100:.2f}%")

        ann_model.save_model_and_scaler(model, scaler, model_save_path, scaler_save_path)
        print("--------------------------------------------------")

if __name__ == "__main__":
    main()
