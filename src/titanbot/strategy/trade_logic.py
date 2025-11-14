# /root/titanbot/src/titanbot/strategy/trade_logic.py
import pandas as pd
from titanbot.strategy.smc_engine import Bias, FVG, OrderBlock

# --- GEÄNDERT: Neuer Parameter 'market_bias' hinzugefügt ---
def get_titan_signal(smc_results: dict, current_candle: pd.Series, params: dict, market_bias: Bias):
    """
    Hier kommt deine eigentliche Handelslogik hinein.
    Diese Funktion entscheidet, ob ein Trade eröffnet werden soll, 
    unter Berücksichtigung des Multi-Timeframe (MTF) Bias.

    Rückgabewerte:
    - (side, entry_price): z.B. ("buy", 123.45) oder ("sell", 123.45)
    - (None, None): Wenn kein Signal vorhanden ist oder ein Filter es blockiert.
    """
    
    # --- 1. Lade ADX-Filtereinstellungen und initialisiere ---
    strategy_params = params.get('strategy', {})
    use_adx_filter = strategy_params.get('use_adx_filter', False) # Standard: Aus
    adx_threshold = strategy_params.get('adx_threshold', 25) # Standard: 25

    # --- NEU: MTF-Filter-Einstellungen ---
    # Wir nehmen an, dass der Filter aktiviert ist, wenn der Bias ungleich NEUTRAL ist
    use_mtf_filter = market_bias is not None and market_bias != Bias.NEUTRAL
    # --- ENDE NEU ---

    # --- 2. Führe die Standard-Signallogik aus ---
    unmitigated_fvgs = smc_results.get("unmitigated_fvgs", [])
    unmitigated_obs = smc_results.get("unmitigated_internal_obs", [])

    signal_side = None
    signal_price = None

    # BEISPIEL-LOGIK:
    # 1. Prüfe, ob die aktuelle Kerze in einen bullischen FVG eintaucht
    for fvg in unmitigated_fvgs:
        if fvg.bias == Bias.BULLISH and current_candle['low'] <= fvg.top:
            signal_side = "buy"
            signal_price = current_candle['close']
            break # Nimm das erste Signal

    # 2. Prüfe, ob die aktuelle Kerze in einen bärischen Order Block eintaucht
    if not signal_side: # Nur prüfen, wenn noch kein Long-Signal gefunden wurde
        for ob in unmitigated_obs:
            if ob.bias == Bias.BEARISH and current_candle['high'] >= ob.barLow:
                signal_side = "sell"
                signal_price = current_candle['close']
                break # Nimm das erste Signal

    # --- 3. Wende den MTF-Filter an (falls Signal gefunden wurde) ---
    if signal_side and use_mtf_filter:
        # Bullischer Bias (BULLISH) erlaubt nur Longs
        if market_bias == Bias.BULLISH and signal_side == "sell":
            return None, None
        
        # Bärischer Bias (BEARISH) erlaubt nur Shorts
        if market_bias == Bias.BEARISH and signal_side == "buy":
            return None, None
            
        # Wenn der Bias NEUTRAL war oder das Signal mit dem Bias übereinstimmt, gehen wir weiter
    
    # --- 4. Wende den ADX-Filter an (falls Signal gefunden wurde) ---
    if signal_side and use_adx_filter:
        try:
            # Hole ADX-Werte aus der aktuellen Kerze (wurden jetzt in trade_manager/backtester berechnet)
            adx = current_candle.get('adx')
            adx_pos = current_candle.get('adx_pos')
            adx_neg = current_candle.get('adx_neg')

            # Prüfe, ob ADX-Daten gültig sind
            if pd.isna(adx) or pd.isna(adx_pos) or pd.isna(adx_neg):
                 return None, None # Blockiere, wenn ADX nicht berechnet werden konnte

            # 1. Prüfe auf Trendstärke
            if adx < adx_threshold:
                 return None, None # Markt ist "choppy", kein Trade

            # 2. Prüfe auf Trendrichtung
            if signal_side == "buy" and adx_pos < adx_neg:
                 return None, None # Blockiere Long, da Abwärtstrend dominant ist

            if signal_side == "sell" and adx_neg < adx_pos:
                 return None, None # Blockiere Short, da Aufwärtstrend dominant ist

        except Exception as e:
            # print(f"FEHLER im ADX-Filter: {e}. Blockiere Trade zur Sicherheit.")
            return None, None # Bei Fehlern im Filter lieber keinen Trade eingehen

    # --- 5. Gib das gefilterte (oder ungefilterte) Signal zurück ---
    if signal_side:
        return signal_side, signal_price

    # Kein Signal gefunden
    return None, None
