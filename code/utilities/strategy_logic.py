# code/utilities/strategy_logic.py
import pandas as pd
import ta

def get_daily_levels(htf_data):
    """
    Extrahiert die Niveaus der letzten abgeschlossenen Tageskerze.
    """
    if len(htf_data) < 2:
        return None
    
    last_complete_candle = htf_data.iloc[-2]
    
    body_top = max(last_complete_candle['open'], last_complete_candle['close'])
    body_bottom = min(last_complete_candle['open'], last_complete_candle['close'])
    
    return {
        "wick_high": last_complete_candle['high'],
        "wick_low": last_complete_candle['low'],
        "body_top": body_top,
        "body_bottom": body_bottom,
        "timestamp": last_complete_candle.name
    }

def add_sma_to_htf(htf_df, params):
    """Berechnet den SMA für den HTF DataFrame."""
    filter_params = params.get('sma_filter', {})
    period = filter_params.get('period', 20)
    
    htf_df['sma'] = ta.trend.sma_indicator(close=htf_df['close'], window=period)
    
    # Definiert den Trend: 1 für Long-Bias (Kurs > SMA), -1 für Short-Bias (Kurs < SMA)
    htf_df['sma_trend'] = 0
    htf_df.loc[htf_df['close'] > htf_df['sma'], 'sma_trend'] = 1
    htf_df.loc[htf_df['close'] < htf_df['sma'], 'sma_trend'] = -1
    return htf_df

def calculate_jaeger_signals(ltf_data, daily_levels, params):
    """
    Berechnet die Jäger-Signale und filtert sie optional mit dem SMA Trend.
    """
    retest_tolerance_pct = params.get("retest_tolerance_pct", 0.05) / 100
    
    if daily_levels is not None:
        body_top = daily_levels['body_top']
        body_bottom = daily_levels['body_bottom']
    else:
        body_top = ltf_data['htf_body_top']
        body_bottom = ltf_data['htf_body_bottom']

    tolerance_long = body_top * retest_tolerance_pct
    tolerance_short = body_bottom * retest_tolerance_pct

    was_above = (ltf_data['close'].shift(1) > body_top)
    is_testing_top = (ltf_data['low'] <= body_top + tolerance_long)
    rebounded_from_top = (ltf_data['close'] > body_top)
    ltf_data['buy_signal'] = was_above & is_testing_top & rebounded_from_top

    was_below = (ltf_data['close'].shift(1) < body_bottom)
    is_testing_bottom = (ltf_data['high'] >= body_bottom - tolerance_short)
    rebounded_from_bottom = (ltf_data['close'] < body_bottom)
    ltf_data['sell_signal'] = was_below & is_testing_bottom & rebounded_from_bottom
    
    filter_params = params.get('sma_filter', {})
    use_filter = filter_params.get('enabled', False)
    
    if use_filter and 'htf_sma_trend' in ltf_data.columns:
        ltf_data.loc[ltf_data['htf_sma_trend'] != 1, 'buy_signal'] = False
        ltf_data.loc[ltf_data['htf_sma_trend'] != -1, 'sell_signal'] = False
        
    return ltf_data
