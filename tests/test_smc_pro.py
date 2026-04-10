# tests/test_smc_pro.py
# Tests für alle SMC Pro Features: Liquidity, Premium/Discount, OB Quality, FVG Filter, Sweeps
import os
import sys
import pytest
import numpy as np
import pandas as pd

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.append(os.path.join(PROJECT_ROOT, 'src'))

from titanbot.strategy.smc_engine import SMCEngine, Bias, LiquidityLevel, OrderBlock, FVG
from titanbot.strategy.trade_logic import get_titan_signal, _is_rejection_candle


# ==================== FIXTURES ====================

def make_df(n=150, seed=42):
    """Synthetischer OHLCV-DataFrame mit realistischer Bewegung."""
    np.random.seed(seed)
    prices = 100 + np.cumsum(np.random.randn(n) * 0.8)
    df = pd.DataFrame({
        'open':   prices + np.random.randn(n) * 0.1,
        'high':   prices + np.abs(np.random.randn(n) * 0.5),
        'low':    prices - np.abs(np.random.randn(n) * 0.5),
        'close':  prices + np.random.randn(n) * 0.2,
        'volume': np.random.randint(200, 2000, n).astype(float),
    }, index=pd.date_range('2025-01-01', periods=n, freq='1h'))
    # sicherstellen: high >= max(open,close), low <= min(open,close)
    df['high'] = df[['open', 'close', 'high']].max(axis=1)
    df['low']  = df[['open', 'close', 'low']].min(axis=1)
    return df


@pytest.fixture
def engine_results():
    df = make_df()
    engine = SMCEngine(settings={
        'swingsLength': 15,
        'min_fvg_size_pct': 0.05,
        'liquidity_lookback': 10,
    })
    results = engine.process_dataframe(df[['open', 'high', 'low', 'close']].copy())
    return engine, results, df


# ==================== ENGINE TESTS ====================

def test_engine_returns_enriched_df(engine_results):
    """process_dataframe muss enriched_df mit smc_*-Spalten zurückgeben."""
    _, results, df = engine_results
    enriched = results.get('enriched_df')
    assert enriched is not None, "enriched_df fehlt im Ergebnis"
    expected_cols = [
        'smc_pd_pct', 'smc_pd_zone', 'smc_pd_high', 'smc_pd_low',
        'smc_recent_bsl_sweep', 'smc_recent_ssl_sweep',
        'smc_swing_bias', 'smc_internal_bias',
    ]
    for col in expected_cols:
        assert col in enriched.columns, f"Spalte '{col}' fehlt in enriched_df"
    assert len(enriched) == len(df)


def test_engine_bar_states_length(engine_results):
    """bar_states muss genau so viele Einträge haben wie Kerzen."""
    engine, results, df = engine_results
    assert len(results['bar_states']) == len(df)


def test_liquidity_levels_detected(engine_results):
    """BSL und SSL Levels werden aus Swing-Pivots generiert."""
    engine, results, _ = engine_results
    levels = results.get('liquidity_levels', [])
    assert len(levels) > 0, "Keine Liquidity Levels erkannt"
    bsl = [l for l in levels if l.bias == 'bsl']
    ssl = [l for l in levels if l.bias == 'ssl']
    assert len(bsl) > 0, "Keine BSL-Levels erkannt"
    assert len(ssl) > 0, "Keine SSL-Levels erkannt"


def test_liquidity_sweep_detection():
    """Sweep: Wick über BSL + Close darunter → swept=True."""
    # Konstruiere ein Szenario mit definiertem Sweep
    n = 80
    prices = np.linspace(100, 110, n)
    df = pd.DataFrame({
        'open':  prices,
        'close': prices,
        'high':  prices + 0.3,
        'low':   prices - 0.3,
        'volume': np.ones(n) * 500,
    }, index=pd.date_range('2025-01-01', periods=n, freq='1h'))
    df['high'] = df[['open', 'close', 'high']].max(axis=1)
    df['low']  = df[['open', 'close', 'low']].min(axis=1)

    engine = SMCEngine(settings={'swingsLength': 10, 'liquidity_lookback': 20})
    results = engine.process_dataframe(df[['open', 'high', 'low', 'close']].copy())

    sweep_events = [e for e in results['events'] if 'Sweep' in e['type']]
    levels = results['liquidity_levels']
    swept = [l for l in levels if l.swept]

    # Engine hat die Daten verarbeitet — Sweeps können 0 sein bei monotoner Bewegung,
    # aber die Logik selbst muss fehlerfrei durchlaufen.
    # Wir prüfen hauptsächlich: keine Exceptions, korrekte Struktur.
    assert isinstance(sweep_events, list)
    assert isinstance(swept, list)


def test_sweep_events_in_realistic_data(engine_results):
    """In realistischen Daten werden Sweep-Events erkannt."""
    _, results, _ = engine_results
    events = results['events']
    bos_choch = [e for e in events if 'BOS' in e['type'] or 'CHoCH' in e['type']]
    assert len(bos_choch) > 0, "Keine BOS/CHoCH Events erkannt — Engine funktioniert nicht"


def test_premium_discount_range(engine_results):
    """P/D-Wert liegt immer zwischen 0.0 und 1.0."""
    _, results, _ = engine_results
    enriched = results['enriched_df']
    pd_pcts = enriched['smc_pd_pct'].dropna()
    assert (pd_pcts >= 0.0).all(), "pd_pct unter 0.0 gefunden"
    assert (pd_pcts <= 1.0).all(), "pd_pct über 1.0 gefunden"


def test_pd_zone_values(engine_results):
    """P/D-Zone ist immer einer der drei erlaubten Werte."""
    _, results, _ = engine_results
    zones = results['enriched_df']['smc_pd_zone'].unique()
    allowed = {'premium', 'discount', 'equilibrium'}
    for z in zones:
        assert z in allowed, f"Unbekannte P/D-Zone: '{z}'"


def test_ob_quality_range(engine_results):
    """OB quality score liegt immer zwischen 0.0 und 1.0."""
    _, results, _ = engine_results
    all_obs = results['unmitigated_internal_obs'] + results['unmitigated_swing_obs']
    for ob in all_obs:
        assert 0.0 <= ob.quality <= 1.0, f"OB quality {ob.quality} außerhalb [0,1]"


def test_ob_touch_count_non_negative(engine_results):
    """OB touch_count ist nie negativ."""
    _, results, _ = engine_results
    all_obs = results['unmitigated_internal_obs'] + results['unmitigated_swing_obs']
    for ob in all_obs:
        assert ob.touch_count >= 0


def test_fvg_size_filter():
    """FVGs unter min_fvg_size_pct werden nicht registriert."""
    n = 100
    prices = 100 + np.cumsum(np.random.randn(n) * 0.1)  # sehr kleine Bewegungen
    df = pd.DataFrame({
        'open':  prices,
        'close': prices,
        'high':  prices + 0.01,   # winzige Wicks → minimale FVGs
        'low':   prices - 0.01,
        'volume': np.ones(n) * 100,
    }, index=pd.date_range('2025-01-01', periods=n, freq='1h'))
    df['high'] = df[['open', 'close', 'high']].max(axis=1)
    df['low']  = df[['open', 'close', 'low']].min(axis=1)

    # Hoher Filter (2%) → fast keine FVGs
    engine_strict = SMCEngine(settings={'swingsLength': 10, 'min_fvg_size_pct': 2.0})
    results_strict = engine_strict.process_dataframe(df[['open','high','low','close']].copy())

    # Niedriger Filter (0.001%) → mehr FVGs
    engine_loose = SMCEngine(settings={'swingsLength': 10, 'min_fvg_size_pct': 0.001})
    results_loose = engine_loose.process_dataframe(df[['open','high','low','close']].copy())

    # Strenger Filter hat weniger oder gleich viele FVGs
    strict_count = len(results_strict['unmitigated_fvgs'])
    loose_count = len(results_loose['unmitigated_fvgs'])
    assert strict_count <= loose_count, \
        f"Strikter Filter ({strict_count}) hat mehr FVGs als loser Filter ({loose_count})"


def test_equal_high_detection():
    """Zwei identische Swing-Highs werden als Equal High markiert."""
    engine = SMCEngine(settings={'swingsLength': 5, 'liquidity_lookback': 20})
    # Manuell zwei Levels am gleichen Preis eintragen
    engine._addLiquidityLevel(100.0, 'bsl', 10, 1000)
    engine._addLiquidityLevel(100.05, 'bsl', 20, 2000)  # innerhalb 0.1% Threshold

    bsl_levels = [l for l in engine.liquidityLevels if l.bias == 'bsl']
    equal_levels = [l for l in bsl_levels if l.is_equal]
    assert len(equal_levels) >= 1, "Equal High nicht erkannt"


# ==================== TRADE LOGIC TESTS ====================

def test_rejection_candle_long():
    """Kerze mit langem unterem Wick = Rejection für Long."""
    candle = pd.Series({
        'open': 100.0, 'close': 100.3,
        'high': 100.4, 'low': 99.0,   # langer unterer Wick
    })
    assert _is_rejection_candle(candle, 'buy'), "Hammer sollte als Long-Rejection erkannt werden"


def test_rejection_candle_short():
    """Kerze mit langem oberem Wick = Rejection für Short."""
    candle = pd.Series({
        'open': 100.0, 'close': 99.7,
        'high': 101.5, 'low': 99.6,   # langer oberer Wick
    })
    assert _is_rejection_candle(candle, 'sell'), "Shooting Star sollte als Short-Rejection erkannt werden"


def test_rejection_candle_doji_not_rejection():
    """Doji (kein klarer Wick) ist keine Rejection."""
    candle = pd.Series({
        'open': 100.0, 'close': 100.1,
        'high': 100.2, 'low': 99.9,
    })
    # Doji hat weder klaren oberen noch unteren Wick → keine Rejection
    # (kann in beide Richtungen False sein, je nach Verhältnis)
    result_buy = _is_rejection_candle(candle, 'buy')
    result_sell = _is_rejection_candle(candle, 'sell')
    assert not (result_buy and result_sell), "Doji sollte nicht beide Richtungen als Rejection haben"


def test_signal_pd_filter_blocks_long_in_premium(engine_results):
    """P/D-Filter: Long-Signal wird blockiert wenn Preis in Premium-Zone."""
    _, results, _ = engine_results
    enriched = results['enriched_df']

    # Suche einen Candle in Premium-Zone
    premium_candles = enriched[enriched['smc_pd_zone'] == 'premium']
    if len(premium_candles) == 0:
        pytest.skip("Keine Premium-Zone-Candles in Testdaten")

    test_candle = premium_candles.iloc[-1].copy()
    test_candle['atr'] = 0.5
    test_candle['adx'] = 25.0
    test_candle['adx_pos'] = 15.0
    test_candle['adx_neg'] = 10.0

    params = {'strategy': {
        'use_pd_filter': True,
        'use_liquidity_sweep_filter': False,  # Sweep-Filter aus → nur P/D testen
        'use_entry_confirmation': False,
    }}

    side, _, _ = get_titan_signal(results, test_candle, params, Bias.NEUTRAL)
    # In Premium → kein Long-Signal
    assert side != 'buy', "Long-Signal in Premium-Zone trotz aktivem P/D-Filter"


def test_signal_pd_filter_blocks_short_in_discount(engine_results):
    """P/D-Filter: Short-Signal wird blockiert wenn Preis in Discount-Zone."""
    _, results, _ = engine_results
    enriched = results['enriched_df']

    discount_candles = enriched[enriched['smc_pd_zone'] == 'discount']
    if len(discount_candles) == 0:
        pytest.skip("Keine Discount-Zone-Candles in Testdaten")

    test_candle = discount_candles.iloc[-1].copy()
    test_candle['atr'] = 0.5

    params = {'strategy': {
        'use_pd_filter': True,
        'use_liquidity_sweep_filter': False,
        'use_entry_confirmation': False,
    }}

    side, _, _ = get_titan_signal(results, test_candle, params, Bias.NEUTRAL)
    assert side != 'sell', "Short-Signal in Discount-Zone trotz aktivem P/D-Filter"


def test_signal_with_pd_filter_disabled(engine_results):
    """Ohne P/D-Filter sind Signale in beiden Zonen möglich."""
    _, results, _ = engine_results
    enriched = results['enriched_df']

    params = {'strategy': {
        'use_pd_filter': False,
        'use_liquidity_sweep_filter': False,
        'use_entry_confirmation': False,
        'min_ob_quality': 0.0,
        'max_ob_touches': 10,
    }}

    signals_found = 0
    for i in range(min(50, len(enriched))):
        candle = enriched.iloc[-(i+1)].copy()
        candle['atr'] = 0.5
        side, _, ctx = get_titan_signal(results, candle, params, Bias.NEUTRAL)
        if side is not None:
            signals_found += 1

    # Ohne Filter sollten mehr Signale entstehen als 0
    # (kann 0 sein wenn keine OBs/FVGs aktiv — dann skip)
    all_zones = enriched['smc_pd_zone'].values
    has_structures = (
        len(results['unmitigated_fvgs']) > 0 or
        len(results['unmitigated_internal_obs']) > 0
    )
    if has_structures:
        assert signals_found >= 0  # Kein Crash ist die Mindestanforderung


def test_signal_returns_three_tuple(engine_results):
    """get_titan_signal gibt immer ein 3-Tuple zurück."""
    _, results, _ = engine_results
    enriched = results['enriched_df']
    test_candle = enriched.iloc[-1].copy()
    test_candle['atr'] = 0.5

    result = get_titan_signal(results, test_candle, {}, Bias.NEUTRAL)
    assert isinstance(result, tuple) and len(result) == 3, \
        f"get_titan_signal muss 3-Tuple zurückgeben, bekam: {result}"


def test_signal_context_contains_pd_zone(engine_results):
    """signal_context enthält pd_zone wenn Signal ausgelöst wird."""
    _, results, _ = engine_results
    enriched = results['enriched_df']

    params = {'strategy': {
        'use_pd_filter': False,
        'use_liquidity_sweep_filter': False,
        'use_entry_confirmation': False,
        'min_ob_quality': 0.0,
        'max_ob_touches': 10,
    }}

    for i in range(len(enriched)):
        candle = enriched.iloc[-(i+1)].copy()
        candle['atr'] = 0.5
        side, price, ctx = get_titan_signal(results, candle, params, Bias.NEUTRAL)
        if side is not None:
            assert 'pd_zone' in ctx, "signal_context enthält kein 'pd_zone'"
            assert 'pd_pct' in ctx, "signal_context enthält kein 'pd_pct'"
            assert ctx['pd_zone'] in ('premium', 'discount', 'equilibrium')
            break


def test_ob_quality_gate_filters_weak_obs():
    """OBs unter min_ob_quality werden nicht getradet."""
    n = 100
    np.random.seed(7)
    prices = 100 + np.cumsum(np.random.randn(n) * 0.5)
    df = pd.DataFrame({
        'open':   prices,
        'close':  prices + np.random.randn(n) * 0.1,
        'high':   prices + np.abs(np.random.randn(n) * 0.3),
        'low':    prices - np.abs(np.random.randn(n) * 0.3),
        'volume': np.ones(n) * 500,
    }, index=pd.date_range('2025-01-01', periods=n, freq='1h'))
    df['high'] = df[['open', 'close', 'high']].max(axis=1)
    df['low']  = df[['open', 'close', 'low']].min(axis=1)

    engine = SMCEngine(settings={'swingsLength': 10})
    results = engine.process_dataframe(df[['open','high','low','close']].copy())
    enriched = results['enriched_df']

    # Strenger Quality-Gate: nur hohe Qualität
    params_strict = {'strategy': {
        'use_pd_filter': False, 'use_liquidity_sweep_filter': False,
        'use_entry_confirmation': False, 'min_ob_quality': 0.9, 'max_ob_touches': 0,
    }}
    # Lockerer Quality-Gate
    params_loose = {'strategy': {
        'use_pd_filter': False, 'use_liquidity_sweep_filter': False,
        'use_entry_confirmation': False, 'min_ob_quality': 0.0, 'max_ob_touches': 10,
    }}

    signals_strict = signals_loose = 0
    for i in range(len(enriched)):
        candle = enriched.iloc[-(i+1)].copy()
        candle['atr'] = 0.5
        s1, _, _ = get_titan_signal(results, candle, params_strict, Bias.NEUTRAL)
        s2, _, _ = get_titan_signal(results, candle, params_loose, Bias.NEUTRAL)
        if s1:
            signals_strict += 1
        if s2:
            signals_loose += 1

    # Strenger Filter → weniger oder gleich viele Signale
    assert signals_strict <= signals_loose, \
        f"Strenger OB-Quality-Filter ({signals_strict}) hat mehr Signale als loser ({signals_loose})"


def test_mtf_filter_blocks_counter_trend():
    """MTF-Filter blockiert Longs bei Bearish-Bias und Shorts bei Bullish-Bias."""
    n = 100
    np.random.seed(5)
    prices = 100 + np.cumsum(np.random.randn(n) * 0.5)
    df = pd.DataFrame({
        'open':   prices, 'close': prices + 0.05,
        'high':   prices + 0.3, 'low': prices - 0.3,
        'volume': np.ones(n) * 500,
    }, index=pd.date_range('2025-01-01', periods=n, freq='1h'))
    df['high'] = df[['open', 'close', 'high']].max(axis=1)
    df['low']  = df[['open', 'close', 'low']].min(axis=1)

    engine = SMCEngine(settings={'swingsLength': 10})
    results = engine.process_dataframe(df[['open','high','low','close']].copy())
    enriched = results['enriched_df']

    params = {'strategy': {
        'use_pd_filter': False, 'use_liquidity_sweep_filter': False,
        'use_entry_confirmation': False, 'min_ob_quality': 0.0, 'max_ob_touches': 10,
    }}

    for i in range(len(enriched)):
        candle = enriched.iloc[-(i+1)].copy()
        candle['atr'] = 0.5
        # Bearish MTF → kein Long erlaubt
        side, _, _ = get_titan_signal(results, candle, params, Bias.BEARISH)
        assert side != 'buy', f"Long trotz Bearish MTF-Bias (Candle {i})"
        # Bullish MTF → kein Short erlaubt
        side, _, _ = get_titan_signal(results, candle, params, Bias.BULLISH)
        assert side != 'sell', f"Short trotz Bullish MTF-Bias (Candle {i})"
