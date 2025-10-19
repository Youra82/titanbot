# src/titanbot/strategy/trade_logic.py
import pandas as pd
from titanbot.strategy.smc_engine import Bias, FVG, OrderBlock

def get_titan_signal(smc_results: dict, current_candle: pd.Series, params: dict):
    """
    Hier kommt deine eigentliche Handelslogik hinein.
    Diese Funktion entscheidet, ob ein Trade eröffnet werden soll.

    Rückgabewerte:
    - (side, entry_price): z.B. ("buy", 123.45) oder ("sell", 123.45)
    - (None, None): Wenn kein Signal vorhanden ist.
    """
    
    # Hole die analysierten, unmitigierten Zonen
    unmitigated_fvgs = smc_results.get("unmitigated_fvgs", [])
    unmitigated_obs = smc_results.get("unmitigated_internal_obs", []) # z.B. interne OBs
    
    # --- HIER IST DEINE TRADING-LOGIK ---
    # Dies ist nur ein sehr einfaches Beispiel.
    # Passe dies an deine SMC-Strategie an!

    # BEISPIEL-LOGIK:
    # 1. Prüfe, ob die aktuelle Kerze in einen bullischen FVG eintaucht
    for fvg in unmitigated_fvgs:
        if fvg.bias == Bias.BULLISH and current_candle['low'] <= fvg.top:
            # Signal gefunden: Kaufe, sobald der FVG berührt wird
            # Wir verwenden den 'close' als Annäherung, trade_manager.py nimmt dann den Market-Preis
            # print(f"SMC-Signal: Long-Einstieg in Bullish FVG bei {fvg.top}") # DEBUG-Ausgabe entfernt
            return "buy", current_candle['close']

    # 2. Prüfe, ob die aktuelle Kerze in einen bärischen Order Block eintaucht
    for ob in unmitigated_obs:
        if ob.bias == Bias.BEARISH and current_candle['high'] >= ob.barLow:
             # print(f"SMC-Signal: Short-Einstieg in Bearish OB bei {ob.barLow}") # DEBUG-Ausgabe entfernt
             return "sell", current_candle['close']


    # Kein Signal gefunden
    return None, None
