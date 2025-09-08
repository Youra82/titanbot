# code/utilities/strategy_logic.py

import pandas as pd
import ta
import numpy as np
from scipy.signal import find_peaks

def calculate_smc_indicators(data, params):
    swing_period = int(params.get('swing_period', 10))
    atr_period = int(params.get('atr_period', 14))

    data['atr'] = ta.volatility.AverageTrueRange(data['high'], data['low'], data['close'], window=atr_period).average_true_range()

    high_peaks_indices, _ = find_peaks(data['high'], distance=swing_period, prominence=data['atr'].mean() * 0.5)
    low_peaks_indices, _ = find_peaks(-data['low'], distance=swing_period, prominence=data['atr'].mean() * 0.5)
    
    # --- KORREKTUR 1: Wir speichern nicht nur den Preis, sondern auch die Position (iloc) ---
    data['swing_high_price'] = np.nan
    data.iloc[high_peaks_indices, data.columns.get_loc('swing_high_price')] = data.iloc[high_peaks_indices]['high']
    data['swing_low_price'] = np.nan
    data.iloc[low_peaks_indices, data.columns.get_loc('swing_low_price')] = data.iloc[low_peaks_indices]['low']
    
    data['swing_high_idx'] = np.nan
    data.iloc[high_peaks_indices, data.columns.get_loc('swing_high_idx')] = high_peaks_indices
    data['swing_low_idx'] = np.nan
    data.iloc[low_peaks_indices, data.columns.get_loc('swing_low_idx')] = low_peaks_indices

    # Wir füllen die Werte vorwärts, damit wir an jeder Kerze den letzten bekannten Swing-Punkt haben
    data.ffill(inplace=True)

    data['trend'] = 0
    data['bos_level'] = np.nan
    data['ob_high'] = np.nan
    data['ob_low'] = np.nan

    for i in range(1, len(data)):
        current_high = data.iloc[i]['high']
        current_low = data.iloc[i]['low']
        
        # Lese den letzten bekannten Swing-Punkt für die aktuelle Kerze
        last_swing_high = data.iloc[i]['swing_high_price']
        last_swing_low = data.iloc[i]['swing_low_price']
        prev_trend = data.iloc[i-1]['trend']

        # Bullischer Strukturbruch
        if current_high > last_swing_high:
            data.iat[i, data.columns.get_loc('trend')] = 1
            data.iat[i, data.columns.get_loc('bos_level')] = last_swing_high
            
            # --- KORREKTUR 2: Wir verwenden den gespeicherten Index direkt, anstatt zu suchen ---
            relevant_range_start_iloc = int(data.iloc[i]['swing_low_idx'])
            lookback_data = data.iloc[relevant_range_start_iloc:i]
            down_candles = lookback_data[lookback_data['close'] < lookback_data['open']]
            
            if not down_candles.empty:
                ob = down_candles.iloc[-1]
                data.iat[i, data.columns.get_loc('ob_high')] = ob['high']
                data.iat[i, data.columns.get_loc('ob_low')] = ob['low']

        # Bärischer Strukturbruch
        elif current_low < last_swing_low:
            data.iat[i, data.columns.get_loc('trend')] = -1
            data.iat[i, data.columns.get_loc('bos_level')] = last_swing_low

            # --- KORREKTUR 2: Wir verwenden den gespeicherten Index direkt, anstatt zu suchen ---
            relevant_range_start_iloc = int(data.iloc[i]['swing_high_idx'])
            lookback_data = data.iloc[relevant_range_start_iloc:i]
            up_candles = lookback_data[lookback_data['close'] > lookback_data['open']]

            if not up_candles.empty:
                ob = up_candles.iloc[-1]
                data.iat[i, data.columns.get_loc('ob_high')] = ob['high']
                data.iat[i, data.columns.get_loc('ob_low')] = ob['low']
        else:
            data.iat[i, data.columns.get_loc('trend')] = prev_trend
            
    return data
