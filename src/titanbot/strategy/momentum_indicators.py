# /root/titanbot/src/titanbot/strategy/momentum_indicators.py
"""
Shared MACD/RSI momentum indicator prep — used identically by trade_manager.py
(live) and backtester.py (backtest/optimizer), so the optional momentum filter
in trade_logic.py sees the same columns in both paths.

Implements what README.md always described ("SMC-Momentum-Hybrid": MACD-Cross +
RSI-Reversal confirmation) but that never actually existed in trade_logic.py —
get_titan_signal() was pure SMC with no momentum component.
"""
import ta


def compute_momentum_columns(df, momentum_params: dict = None):
    """
    Adds macd/macd_signal/rsi plus two "recent event within lookback" boolean
    columns to df (mutated in place, also returned):

    - macd_recent_bull_cross: MACD line crossed above signal line within the
      last `momentum_lookback` bars (not necessarily this exact bar — a strict
      same-bar cross combined with the rest of the SMC filter stack would make
      an already-selective strategy trade even more rarely).
    - macd_recent_bear_cross: mirror, bearish cross.
    - rsi_recent_oversold_recovery: RSI dipped below `momentum_rsi_oversold`
      within the lookback window and has since recovered back above it.
    - rsi_recent_overbought_reversal: mirror, RSI rose above
      `momentum_rsi_overbought` within the lookback window and has since
      fallen back below it.

    momentum_params keys (all optional):
      momentum_macd_fast (12), momentum_macd_slow (26), momentum_macd_signal (9),
      momentum_rsi_period (14), momentum_rsi_oversold (30),
      momentum_rsi_overbought (70), momentum_lookback (3)
    """
    p = momentum_params or {}
    fast = p.get('momentum_macd_fast', 12)
    slow = p.get('momentum_macd_slow', 26)
    signal = p.get('momentum_macd_signal', 9)
    rsi_period = p.get('momentum_rsi_period', 14)
    oversold = p.get('momentum_rsi_oversold', 30)
    overbought = p.get('momentum_rsi_overbought', 70)
    lookback = p.get('momentum_lookback', 3)

    macd_ind = ta.trend.MACD(close=df['close'], window_fast=fast, window_slow=slow, window_sign=signal)
    df['macd'] = macd_ind.macd()
    df['macd_signal'] = macd_ind.macd_signal()

    rsi_ind = ta.momentum.RSIIndicator(close=df['close'], window=rsi_period)
    df['rsi'] = rsi_ind.rsi()

    macd_diff = df['macd'] - df['macd_signal']
    bull_cross = (macd_diff > 0) & (macd_diff.shift(1) <= 0)
    bear_cross = (macd_diff < 0) & (macd_diff.shift(1) >= 0)
    df['macd_recent_bull_cross'] = bull_cross.rolling(window=lookback, min_periods=1).max().fillna(0).astype(bool)
    df['macd_recent_bear_cross'] = bear_cross.rolling(window=lookback, min_periods=1).max().fillna(0).astype(bool)

    was_oversold = (df['rsi'] < oversold).rolling(window=lookback, min_periods=1).max().fillna(0).astype(bool)
    was_overbought = (df['rsi'] > overbought).rolling(window=lookback, min_periods=1).max().fillna(0).astype(bool)
    df['rsi_recent_oversold_recovery'] = was_oversold & (df['rsi'] >= oversold)
    df['rsi_recent_overbought_reversal'] = was_overbought & (df['rsi'] <= overbought)

    return df
