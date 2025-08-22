import pandas as pd
import ta
import numpy as np

# =============================================================================
# STRATEGIE 1: MOMENTUM-BESCHLEUNIGER
# =============================================================================
def calculate_momentum_signals(data, params):
    """
    Berechnet Signale basierend auf VWAP und Volumen-Ausbrüchen.
    """
    vol_ma_period = params.get("volume_ma_period", 20)
    vol_multiplier = params.get("volume_ma_multiplier", 1.5)
    crv = params.get("crv", 2.0)

    # Indikatoren berechnen
    data['vwap'] = ta.volume.volume_weighted_average_price(data['high'], data['low'], data['close'], data['volume'])
    data['volume_ma'] = data['volume'].rolling(window=vol_ma_period).mean()

    # Einstiegssignale
    long_condition = (data['close'] > data['vwap']) & (data['volume'] > data['volume_ma'] * vol_multiplier)
    short_condition = (data['close'] < data['vwap']) & (data['volume'] > data['volume_ma'] * vol_multiplier)

    data['buy_signal'] = long_condition & ~long_condition.shift(1).fillna(False)
    data['sell_signal'] = short_condition & ~short_condition.shift(1).fillna(False)

    # Stop-Loss und Take-Profit Level für den Backtester
    data['sl_price'] = data['vwap']
    risk = abs(data['close'] - data['sl_price'])
    data['tp_price'] = np.where(data['buy_signal'], data['close'] + (risk * crv),
                              np.where(data['sell_signal'], data['close'] - (risk * crv), np.nan))
    return data

# =============================================================================
# STRATEGIE 2: VOLATILITÄTS-FÄNGER
# =============================================================================
def calculate_volatility_signals(data, params):
    """
    Berechnet Signale basierend auf Bollinger Band Ausbrüchen.
    """
    bb_period = params.get("bb_period", 20)
    bb_std_dev = params.get("bb_std_dev", 2)

    # Indikatoren berechnen
    indicator_bb = ta.volatility.BollingerBands(close=data['close'], window=bb_period, window_dev=bb_std_dev)
    data['bb_high'] = indicator_bb.bollinger_hband()
    data['bb_mid'] = indicator_bb.bollinger_mavg()
    data['bb_low'] = indicator_bb.bollinger_lband()

    # Einstiegssignale
    long_condition = data['close'] > data['bb_high']
    short_condition = data['close'] < data['bb_low']

    data['buy_signal'] = long_condition & ~long_condition.shift(1).fillna(False)
    data['sell_signal'] = short_condition & ~short_condition.shift(1).fillna(False)

    # Stop-Loss und Take-Profit Level
    data['sl_price'] = data['bb_mid']
    data['tp_price'] = np.where(data['buy_signal'], data['bb_low'],
                              np.where(data['sell_signal'], data['bb_high'], np.nan))
    return data

# =============================================================================
# STRATEGIE 3: GEZEITEN-WELLEN-REITER
# =============================================================================
def calculate_tidal_wave_signals(data, params):
    """
    Berechnet Signale basierend auf EMA-Crossover und Pullbacks.
    """
    ema_fast_period = params.get("ema_fast_period", 9)
    ema_slow_period = params.get("ema_slow_period", 21)

    # Indikatoren berechnen
    data['ema_fast'] = ta.trend.ema_indicator(data['close'], window=ema_fast_period)
    data['ema_slow'] = ta.trend.ema_indicator(data['close'], window=ema_slow_period)

    # Trend- und Pullback-Bedingungen
    uptrend = data['ema_fast'] > data['ema_slow']
    downtrend = data['ema_fast'] < data['ema_slow']
    pullback_long = (data['low'] <= data['ema_fast']) & (data['close'] > data['ema_fast'])
    pullback_short = (data['high'] >= data['ema_fast']) & (data['close'] < data['ema_fast'])

    data['buy_signal'] = uptrend & pullback_long
    data['sell_signal'] = downtrend & pullback_short

    # Stop-Loss und Take-Profit Level
    data['sl_price'] = data['ema_slow']
    # TP beim letzten relevanten Hoch/Tief (hier vereinfacht als fester CRV für Backtesting)
    risk = abs(data['close'] - data['sl_price'])
    data['tp_price'] = np.where(data['buy_signal'], data['close'] + (risk * 2.0),
                              np.where(data['sell_signal'], data['close'] - (risk * 2.0), np.nan))
    return data
