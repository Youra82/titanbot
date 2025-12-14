import pandas as pd
import numpy as np
import yfinance as yf
from dataclasses import dataclass, field
from enum import Enum

# --- 1. Konstanten (ersetzt Pine-Konstanten) ---

class Leg(Enum):
    BULLISH = 1
    BEARISH = 0

class Bias(Enum):
    BULLISH = 1
    BEARISH = -1
    NEUTRAL = 0

# --- 2. Datenstrukturen (ersetzt Pine 'type' UDTs) ---

@dataclass
class Pivot:
    """ Speichert den Zustand eines Swing- oder Internal-Pivots """
    currentLevel: float = np.nan
    lastLevel: float = np.nan
    crossed: bool = False
    barTime: int = 0
    barIndex: int = 0

@dataclass
class OrderBlock:
    """ Speichert die Daten für einen Order Block """
    barHigh: float
    barLow: float
    barTime: int
    bias: Bias
    mitigated: bool = False

@dataclass
class FVG:
    """ Speichert die Daten für ein Fair Value Gap """
    top: float  # Immer der höhere Preis
    bottom: float # Immer der niedrigere Preis
    bias: Bias
    startTime: int
    mitigated: bool = False

# --- 3. Die Haupt-Engine-Klasse ---

class SMCEngine:
    """
    Diese Klasse bildet die zustandsbehaftete Logik des Pine-Skripts nach.
    Sie wird Kerze für Kerze mit Daten gefüttert.
    """
    def __init__(self, settings: dict):
        # --- Inputs ---
        self.swingsLength = settings.get('swingsLength', 50)
        self.internalLength = 5  # Fest im Pine-Skript für 'getCurrentStructure(5,...)'
        self.ob_mitigation = settings.get('ob_mitigation', 'High/Low') # 'Close' oder 'High/Low'

        # --- Interne Zustandsvariablen (ersetzt 'var') ---
        self.swingHigh = Pivot()
        self.swingLow = Pivot()
        self.internalHigh = Pivot()
        self.internalLow = Pivot()
        
        self.swingTrend = Bias.NEUTRAL
        self.internalTrend = Bias.NEUTRAL
        
        # Zustand der 'leg'-Funktion
        self.swing_leg_state = Leg.BULLISH
        self.internal_leg_state = Leg.BULLISH
        
        # --- Datenspeicherung ---
        # Wir müssen die gesamte Historie für Lookbacks speichern
        self.highs = []
        self.lows = []
        self.closes = []
        self.times = []
        
        # --- Ergebnislisten ---
        self.swingOrderBlocks: list[OrderBlock] = []
        self.internalOrderBlocks: list[OrderBlock] = []
        self.fairValueGaps: list[FVG] = []
        
        # Ein Protokoll aller erkannten Ereignisse
        self.event_log = []

    # --- 1. Logik zur Pivot-Erkennung (leg & getCurrentStructure) ---
    
    def _leg(self, size: int, index: int, current_leg_state: Leg) -> Leg:
        """ Portierung der 'leg'-Funktion """
        # `high[size]` in Pine ist `self.highs[index - size]`
        if index < size:
            return current_leg_state  # Nicht genügend Daten

        # `ta.highest(size)` in Pine ist `max(high[0]...high[size-1])`
        # bezogen auf den aktuellen `index` ist das `max(self.highs[index-size+1 : index+1])`
        try:
            window_highs = self.highs[index - size + 1 : index + 1]
            window_lows = self.lows[index - size + 1 : index + 1]
            
            if not window_highs or not window_lows:
                return current_leg_state

            pivot_high_candidate = self.highs[index - size]
            pivot_low_candidate = self.lows[index - size]

            newLegHigh = pivot_high_candidate > max(window_highs)
            newLegLow = pivot_low_candidate < min(window_lows)
        except Exception:
            return current_leg_state # Fallback bei Datenproblemen

        if newLegHigh:
            return Leg.BEARISH
        elif newLegLow:
            return Leg.BULLISH
        else:
            return current_leg_state  # Kein Wechsel

    def _getCurrentStructure(self, size: int, index: int, internal: bool):
        """ Portierung von 'getCurrentStructure' """
        
        prev_leg = self.internal_leg_state if internal else self.swing_leg_state
        new_leg = self._leg(size, index, prev_leg)
        
        # Zustand aktualisieren
        if internal:
            self.internal_leg_state = new_leg
        else:
            self.swing_leg_state = new_leg

        # Auf Änderung prüfen (startOfNewLeg)
        if new_leg == prev_leg:
            return

        # Ein neuer Pivot wurde `size` Kerzen zuvor bestätigt
        pivot_index = index - size
        if pivot_index < 0: return
        
        pivot_time = self.times[pivot_index]

        if new_leg == Leg.BULLISH:  # Ein Pivot-Tief (Low) wurde bestätigt
            p_ivot = self.internalLow if internal else self.swingLow
            p_ivot.lastLevel = p_ivot.currentLevel
            p_ivot.currentLevel = self.lows[pivot_index]
            p_ivot.crossed = False
            p_ivot.barTime = pivot_time
            p_ivot.barIndex = pivot_index
            
        elif new_leg == Leg.BEARISH:  # Ein Pivot-Hoch (High) wurde bestätigt
            p_ivot = self.internalHigh if internal else self.swingHigh
            p_ivot.lastLevel = p_ivot.currentLevel
            p_ivot.currentLevel = self.highs[pivot_index]
            p_ivot.crossed = False
            p_ivot.barTime = pivot_time
            p_ivot.barIndex = pivot_index

    # --- 2. Logik für BOS/CHoCH & OB-Speicherung ---

    def _storeOrdeBlock(self, p_ivot: Pivot, index: int, internal: bool, bias: Bias):
        """ Portierung von 'storeOrdeBlock' """
        if p_ivot.barIndex >= index or p_ivot.barIndex < 0:
            return  # Ungültiger Bereich

        # Finde die Kerze mit dem Extremum im Bereich zwischen Pivot und Break
        try:
            window_highs = self.highs[p_ivot.barIndex : index]
            window_lows = self.lows[p_ivot.barIndex : index]

            if bias == Bias.BULLISH:  # Sucht nach bärischem OB (letzte rote Kerze)
                ob_index_in_window = np.argmax(window_highs)
            else:  # Sucht nach bullischem OB (letzte grüne Kerze)
                ob_index_in_window = np.argmin(window_lows)
            
            ob_index = p_ivot.barIndex + ob_index_in_window

            new_ob = OrderBlock(
                barHigh=self.highs[ob_index],
                barLow=self.lows[ob_index],
                barTime=self.times[ob_index],
                bias=bias
            )
            
            ob_list = self.internalOrderBlocks if internal else self.swingOrderBlocks
            ob_list.append(new_ob)
        except Exception as e:
            # Dieser Fehler kann auftreten, wenn das Fenster leer ist (z.B. p_ivot.barIndex == index)
            # print(f"Fehler beim Speichern des OB: {e} | Pivot Index: {p_ivot.barIndex}, Current Index: {index}")
            pass

    def _displayStructure(self, index: int, internal: bool):
        """ Portierung von 'displayStructure' (BOS/CHoCH-Erkennung) """
        current_close = self.closes[index]
        current_time = self.times[index]

        p_ivot_high = self.internalHigh if internal else self.swingHigh
        p_ivot_low = self.internalLow if internal else self.swingLow
        trend = self.internalTrend if internal else self.swingTrend
        
        # --- Bullischer Bruch ---
        if (not p_ivot_high.crossed and  
            not pd.isna(p_ivot_high.currentLevel) and  
            current_close > p_ivot_high.currentLevel):
            
            tag = "CHoCH" if trend == Bias.BEARISH else "BOS"
            p_ivot_high.crossed = True
            new_trend = Bias.BULLISH
            
            self.event_log.append({
                "time": current_time, "index": index,
                "type": f"{'Internal' if internal else 'Swing'} Bullish {tag}",
                "level": p_ivot_high.currentLevel
            })
            
            self._storeOrdeBlock(p_ivot_high, index, internal, Bias.BULLISH)
            
            if internal: self.internalTrend = new_trend
            else: self.swingTrend = new_trend

        # --- Bärischer Bruch ---
        if (not p_ivot_low.crossed and  
            not pd.isna(p_ivot_low.currentLevel) and  
            current_close < p_ivot_low.currentLevel):

            tag = "CHoCH" if trend == Bias.BULLISH else "BOS"
            p_ivot_low.crossed = True
            new_trend = Bias.BEARISH
            
            self.event_log.append({
                "time": current_time, "index": index,
                "type": f"{'Internal' if internal else 'Swing'} Bearish {tag}",
                "level": p_ivot_low.currentLevel
            })

            self._storeOrdeBlock(p_ivot_low, index, internal, Bias.BEARISH)
            
            if internal: self.internalTrend = new_trend
            else: self.swingTrend = new_trend

    # --- 3. Logik zur Mitigation (Löschung) ---

    def _deleteOrderBlocks(self, index: int):
        """ Portierung von 'deleteOrderBlocks' (OB-Mitigation) """
        current_high = self.highs[index]
        current_low = self.lows[index]
        current_close = self.closes[index]
        
        bearish_mit_source = current_close if self.ob_mitigation == 'Close' else current_high
        bullish_mit_source = current_close if self.ob_mitigation == 'Close' else current_low
        
        for ob in self.internalOrderBlocks + self.swingOrderBlocks:
            if ob.mitigated:
                continue
            
            if ob.bias == Bias.BEARISH and bearish_mit_source > ob.barHigh:
                ob.mitigated = True
            elif ob.bias == Bias.BULLISH and bullish_mit_source < ob.barLow:
                ob.mitigated = True

    def _drawFairValueGaps(self, index: int):
        """ Portierung von 'drawFairValueGaps' (FVG-Erkennung) """
        if index < 2: return # Braucht 3 Kerzen

        last_close = self.closes[index - 1]
        current_high = self.highs[index]
        current_low = self.lows[index]
        last_2_high = self.highs[index - 2]
        last_2_low = self.lows[index - 2]
        current_time = self.times[index]

        # Pine-Logik (vereinfacht, ohne 'threshold')
        bullish_fvg = (current_low > last_2_high) and (last_close > last_2_high)
        bearish_fvg = (current_high < last_2_low) and (last_close < last_2_low)
        
        if bullish_fvg:
            new_fvg = FVG(
                top = current_low,
                bottom = last_2_high,
                bias = Bias.BULLISH,
                startTime = current_time
            )
            self.fairValueGaps.append(new_fvg)
            self.event_log.append({
                "time": current_time, "index": index, "type": "Bullish FVG",
                "level": (new_fvg.top, new_fvg.bottom)
            })

        if bearish_fvg:
            new_fvg = FVG(
                top = last_2_low,
                bottom = current_high,
                bias = Bias.BEARISH,
                startTime = current_time
            )
            self.fairValueGaps.append(new_fvg)
            self.event_log.append({
                "time": current_time, "index": index, "type": "Bearish FVG",
                "level": (new_fvg.top, new_fvg.bottom)
            })

    def _deleteFairValueGaps(self, index: int):
        """ Portierung von 'deleteFairValueGaps' (FVG-Mitigation) """
        current_low = self.lows[index]
        current_high = self.highs[index]

        for fvg in self.fairValueGaps:
            if fvg.mitigated:
                continue
            
            if fvg.bias == Bias.BULLISH and current_low < fvg.bottom:
                fvg.mitigated = True
            elif fvg.bias == Bias.BEARISH and current_high > fvg.top:
                fvg.mitigated = True

    # --- 4. Öffentliche Hauptmethode ---
    
    def process_dataframe(self, df: pd.DataFrame):
        """
        Verarbeitet einen gesamten Pandas DataFrame und gibt die Ergebnisse zurück.
        """
        # 1. Daten vorbereiten
        df = df.sort_index()
        self.highs = df['high'].tolist()
        self.lows = df['low'].tolist()
        self.closes = df['close'].tolist()
        
        if pd.api.types.is_datetime64_any_dtype(df.index):
            self.times = df.index.astype(np.int64).tolist() # Zeit als int
        else:
            self.times = df.index.astype(int).tolist()

        # 2. Schleife durch jede Kerze (jeden 'Bar')
        for i in range(len(df)):
            # Die Ausführungsreihenfolge ist wichtig!
            # Wir approximieren die Reihenfolge aus dem Pine-Skript.
            
            # 1. FVG-Mitigation
            self._deleteFairValueGaps(i)
            
            # 2. Struktur-Pivots finden
            self._getCurrentStructure(self.swingsLength, i, internal=False)
            self._getCurrentStructure(self.internalLength, i, internal=True)
            
            # 3. BOS/CHoCH prüfen
            self._displayStructure(i, internal=True)
            self._displayStructure(i, internal=False)
            
            # 4. OB-Mitigation
            self._deleteOrderBlocks(i)
            
            # 5. Neue FVGs finden
            self._drawFairValueGaps(i)

        # 3. Ergebnisse zurückgeben
        return {
            "events": self.event_log,
            "unmitigated_swing_obs": [ob for ob in self.swingOrderBlocks if not ob.mitigated],
            "unmitigated_internal_obs": [ob for ob in self.internalOrderBlocks if not ob.mitigated],
            "unmitigated_fvgs": [fvg for fvg in self.fairValueGaps if not fvg.mitigated]
        }


# --- 4. Anwendungsbeispiel ---

if __name__ == "__main__":
    # Dieser Teil wird nur ausgeführt, wenn das Skript direkt gestartet wird.
    
    # --- 1. Beispieldaten laden ---
    # Stellen Sie sicher, dass der DataFrame 'high', 'low', 'close' enthält
    # und einen Zeit-Index hat.
    print("Lade Daten von yfinance...")
    data = yf.download("EURUSD=X", period="3mo", interval="1h")
    data = data.rename(columns={
        "Open": "open", "High": "high", "Low": "low",  
        "Close": "close", "Volume": "volume"
    })

    print(f"Daten geladen: {len(data)} Kerzen")

    # --- 2. Engine initialisieren ---
    # Hier kannst du die Einstellungen übergeben
    smc_settings = {
        'swingsLength': 50,          # Entspricht 'swingsLengthInput'
        'ob_mitigation': 'High/Low' # Entspricht 'orderBlockMitigationInput'
    }
    engine = SMCEngine(settings=smc_settings)

    # --- 3. Analyse durchführen ---
    print("Starte SMC-Analyse...")
    results = engine.process_dataframe(data)

    # --- 4. Ergebnisse anzeigen ---
    print("\n--- Analyse-Ergebnisse ---")

    print(f"\nGefundene Events (BOS/CHoCH/FVG): {len(results['events'])}")
    # Zeige die letzten 10 Events
    for event in results['events'][-10:]:
        # Zeitstempel lesbar machen
        event_time = pd.to_datetime(event['time'])
        # Runde die Preislevels für eine saubere Anzeige
        level_str = ""
        if isinstance(event['level'], (int, float)):
            level_str = f"{event['level']:.5f}"
        elif isinstance(event['level'], tuple):
            level_str = f"({event['level'][0]:.5f}, {event['level'][1]:.5f})"
            
        print(f"  {event_time} - {event['type']:<20} @ {level_str}")

    print(f"\nAktive (unmitigierte) Swing Order Blocks: {len(results['unmitigated_swing_obs'])}")
    for ob in results['unmitigated_swing_obs'][-5:]:
        ob_time = pd.to_datetime(ob.barTime)
        print(f"  {ob_time} - {ob.bias.name:<7} OB: High={ob.barHigh:.5f}, Low={ob.barLow:.5f}")

    print(f"\nAktive (unmitigierte) Fair Value Gaps: {len(results['unmitigated_fvgs'])}")
    for fvg in results['unmitigated_fvgs'][-5:]:
        fvg_time = pd.to_datetime(fvg.startTime)
        print(f"  {fvg_time} - {fvg.bias.name:<7} FVG: Top={fvg.top:.5f}, Bottom={fvg.bottom:.5f}")
