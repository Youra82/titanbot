# /root/titanbot/src/titanbot/strategy/trade_logic.py
import pandas as pd
import numpy as np
from titanbot.strategy.smc_engine import Bias, FVG, OrderBlock

# --- GEÄNDERT: Neuer Parameter 'market_bias' hinzugefügt ---
def get_titan_signal(smc_results: dict, current_candle: pd.Series, params: dict, market_bias: Bias, prev_candle: pd.Series = None):
    """
    Hier kommt deine eigentliche Handelslogik hinein.
    Diese Funktion entscheidet, ob ein Trade eröffnet werden soll, 
    unter Berücksichtigung des Multi-Timeframe (MTF) Bias.

    Rückgabewerte:
    - (side, entry_price, signal_context): z.B. ("buy", 123.45, {"fvg": ...}) oder (None, None, None)
    """
    
    # --- 1. Lade Filter-Einstellungen ---
    strategy_params = params.get('strategy', {})
    use_adx_filter = strategy_params.get('use_adx_filter', False)
    adx_threshold = strategy_params.get('adx_threshold', 25)
    
    # NEU: Entry-Confirmation Settings
    use_entry_confirmation = strategy_params.get('use_entry_confirmation', True)
    
    # NEU: Volume-Filter Settings
    use_volume_filter = strategy_params.get('use_volume_filter', True)
    volume_ma_period = strategy_params.get('volume_ma_period', 20)
    volume_threshold_multiplier = strategy_params.get('volume_threshold_multiplier', 1.5)

    # --- MTF-Filter-Einstellungen ---
    use_mtf_filter = market_bias is not None and market_bias != Bias.NEUTRAL

    # --- 2. Volume-Filter durchführen (falls aktiviert) ---
    if use_volume_filter:
        try:
            volume_ma = current_candle.get('volume_ma', np.nan)
            current_volume = current_candle.get('volume', 0)
            
            if pd.isna(volume_ma) or volume_ma == 0:
                return None, None, None  # Keine Volume-Daten
            
            # Prüfe ob Volume über Threshold liegt
            if current_volume < (volume_ma * volume_threshold_multiplier):
                return None, None, None  # Volume zu niedrig
        except Exception as e:
            return None, None, None  # Bei Fehler blockieren

    # --- 3. Signallogik mit Entry-Confirmation ---
    unmitigated_fvgs = smc_results.get("unmitigated_fvgs", [])
    unmitigated_obs = smc_results.get("unmitigated_internal_obs", [])

    signal_side = None
    signal_price = None
    signal_context = {}  # Speichert Level-Info für SL-Platzierung

    # Helper: Prüfe ob Kerze bullisch/bärisch ist
    is_bullish_candle = current_candle['close'] > current_candle['open']
    is_bearish_candle = current_candle['close'] < current_candle['open']

    # 1. Prüfe FVGs (Bullisch)
    for fvg in unmitigated_fvgs:
        if fvg.bias == Bias.BULLISH:
            # Prüfe ob Price in FVG-Zone ist
            price_in_zone = (current_candle['low'] <= fvg.top and 
                           current_candle['close'] >= fvg.bottom)
            
            if price_in_zone:
                # Entry-Confirmation: Bullische Kerze erforderlich
                if use_entry_confirmation and not is_bullish_candle:
                    continue  # Warte auf bullische Bestätigung
                
                signal_side = "buy"
                signal_price = current_candle['close']
                signal_context = {
                    'type': 'fvg',
                    'level_low': fvg.bottom,
                    'level_high': fvg.top,
                    'bias': 'bullish'
                }
                break

    # 2. Prüfe FVGs (Bärisch)
    if not signal_side:
        for fvg in unmitigated_fvgs:
            if fvg.bias == Bias.BEARISH:
                price_in_zone = (current_candle['high'] >= fvg.bottom and 
                               current_candle['close'] <= fvg.top)
                
                if price_in_zone:
                    if use_entry_confirmation and not is_bearish_candle:
                        continue
                    
                    signal_side = "sell"
                    signal_price = current_candle['close']
                    signal_context = {
                        'type': 'fvg',
                        'level_low': fvg.bottom,
                        'level_high': fvg.top,
                        'bias': 'bearish'
                    }
                    break

    # 3. Prüfe Order Blocks (falls kein FVG-Signal)
    if not signal_side:
        for ob in unmitigated_obs:
            if ob.bias == Bias.BULLISH:
                price_in_zone = (current_candle['low'] <= ob.barHigh and 
                               current_candle['close'] >= ob.barLow)
                
                if price_in_zone:
                    if use_entry_confirmation and not is_bullish_candle:
                        continue
                    
                    signal_side = "buy"
                    signal_price = current_candle['close']
                    signal_context = {
                        'type': 'order_block',
                        'level_low': ob.barLow,
                        'level_high': ob.barHigh,
                        'bias': 'bullish'
                    }
                    break

    if not signal_side:
        for ob in unmitigated_obs:
            if ob.bias == Bias.BEARISH:
                price_in_zone = (current_candle['high'] >= ob.barLow and 
                               current_candle['close'] <= ob.barHigh)
                
                if price_in_zone:
                    if use_entry_confirmation and not is_bearish_candle:
                        continue
                    
                    signal_side = "sell"
                    signal_price = current_candle['close']
                    signal_context = {
                        'type': 'order_block',
                        'level_low': ob.barLow,
                        'level_high': ob.barHigh,
                        'bias': 'bearish'
                    }
                    break

    # --- 4. Wende den MTF-Filter an (falls Signal gefunden wurde) ---
    if signal_side and use_mtf_filter:
        if market_bias == Bias.BULLISH and signal_side == "sell":
            return None, None, None
        if market_bias == Bias.BEARISH and signal_side == "buy":
            return None, None, None
    
    # --- 5. Wende den ADX-Filter an (falls Signal gefunden wurde) ---
    if signal_side and use_adx_filter:
        try:
            adx = current_candle.get('adx')
            adx_pos = current_candle.get('adx_pos')
            adx_neg = current_candle.get('adx_neg')

            if pd.isna(adx) or pd.isna(adx_pos) or pd.isna(adx_neg):
                 return None, None, None

            # 1. Prüfe auf Trendstärke
            if adx < adx_threshold:
                 return None, None, None

            # 2. Prüfe auf Trendrichtung
            if signal_side == "buy" and adx_pos < adx_neg:
                 return None, None, None

            if signal_side == "sell" and adx_neg < adx_pos:
                 return None, None, None

        except Exception as e:
            return None, None, None

    # --- 6. Gib das gefilterte Signal mit Context zurück ---
    if signal_side:
        return signal_side, signal_price, signal_context

    # Kein Signal gefunden
    return None, None, None
