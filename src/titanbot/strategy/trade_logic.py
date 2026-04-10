# /root/titanbot/src/titanbot/strategy/trade_logic.py
import pandas as pd
import numpy as np
from titanbot.strategy.smc_engine import Bias, FVG, OrderBlock


# ==================== HELPERS ====================

def _is_rejection_candle(candle: pd.Series, side: str) -> bool:
    """
    Rejection candle (pin bar / hammer / shooting star).
    Long: long lower wick + close near high = demand absorbed.
    Short: long upper wick + close near low = supply absorbed.
    """
    body = abs(candle['close'] - candle['open'])
    full_range = candle['high'] - candle['low']
    if full_range == 0:
        return False
    body_pct = body / full_range

    if side == 'buy':
        lower_wick = min(candle['open'], candle['close']) - candle['low']
        return (lower_wick / full_range) >= 0.35 and body_pct <= 0.65
    else:
        upper_wick = candle['high'] - max(candle['open'], candle['close'])
        return (upper_wick / full_range) >= 0.35 and body_pct <= 0.65


def _ob_quality_ok(ob: OrderBlock, min_quality: float, max_touches: int) -> bool:
    """OB passes quality gate: strength and freshness check."""
    if ob.touch_count > max_touches:
        return False
    if ob.quality < min_quality:
        return False
    return True


def _get_strategy_params(params: dict) -> dict:
    """
    Normalize params dict. Backtester wraps as {"strategy":..., "risk":...},
    live bot passes the flat config with 'strategy' as sub-key.
    """
    if 'strategy' in params:
        return params['strategy']
    return params  # fallback: treat top-level as strategy params


# ==================== SIGNAL GENERATION ====================

def get_titan_signal(
    smc_results: dict,
    current_candle: pd.Series,
    params: dict,
    market_bias: Bias,
    prev_candle: pd.Series = None,
):
    """
    Professional SMC signal generation. Entry requires ALL of:

    1. Premium/Discount zone alignment
       - Long only in discount zone (pd_pct <= 0.5)
       - Short only in premium zone (pd_pct >= 0.5)

    2. Liquidity swept in the correct direction
       - SSL swept recently → long setup (stops taken below, reversal up)
       - BSL swept recently → short setup (stops taken above, reversal down)

    3. Price in a valid structural zone
       - Unmitigated Bullish OB / FVG → long
       - Unmitigated Bearish OB / FVG → short
       - FVG priority over OB (tighter, cleaner zone)

    4. OB quality gate (if OB entry)
       - quality score >= min_ob_quality
       - touch_count <= max_ob_touches

    5. Confirmation candle (optional)
       - Bullish engulfing / rejection pin bar for longs
       - Bearish engulfing / rejection pin bar for shorts

    6. MTF bias alignment (optional — use_mtf_filter)

    7. ADX filter (optional — use_adx_filter)

    8. Volume filter (optional — use_volume_filter)

    Returns: (side, entry_price, signal_context) or (None, None, None)
    """
    strategy_params = _get_strategy_params(params)

    # --- Settings ---
    use_adx_filter = strategy_params.get('use_adx_filter', False)
    adx_threshold = strategy_params.get('adx_threshold', 25)
    use_entry_confirmation = strategy_params.get('use_entry_confirmation', True)
    use_rejection_candle = strategy_params.get('use_rejection_candle', True)
    use_volume_filter = strategy_params.get('use_volume_filter', False)
    volume_threshold_multiplier = strategy_params.get('volume_threshold_multiplier', 1.5)
    use_pd_filter = strategy_params.get('use_pd_filter', True)
    use_liquidity_sweep_filter = strategy_params.get('use_liquidity_sweep_filter', True)
    min_ob_quality = strategy_params.get('min_ob_quality', 0.2)
    max_ob_touches = strategy_params.get('max_ob_touches', 1)
    use_swing_ob = strategy_params.get('use_swing_ob', True)

    use_mtf_filter = market_bias is not None and market_bias != Bias.NEUTRAL

    # --- Per-candle SMC state (from enriched df) ---
    pd_zone = current_candle.get('smc_pd_zone', 'equilibrium')
    pd_pct = current_candle.get('smc_pd_pct', 0.5)
    recent_bsl_sweep = current_candle.get('smc_recent_bsl_sweep', False)
    recent_ssl_sweep = current_candle.get('smc_recent_ssl_sweep', False)

    # --- Fast exits ---
    # Volume filter
    if use_volume_filter:
        try:
            volume_ma = current_candle.get('volume_ma', np.nan)
            current_volume = current_candle.get('volume', 0)
            if not (pd.isna(volume_ma) or volume_ma == 0):
                if current_volume < volume_ma * volume_threshold_multiplier:
                    return None, None, None
        except Exception:
            pass

    # ADX strength filter (directional filter applied later)
    if use_adx_filter:
        try:
            adx = current_candle.get('adx', np.nan)
            if pd.isna(adx) or adx < adx_threshold:
                return None, None, None
        except Exception:
            return None, None, None

    # --- Candle character ---
    is_bullish_candle = current_candle['close'] > current_candle['open']
    is_bearish_candle = current_candle['close'] < current_candle['open']

    # --- SMC structures ---
    unmitigated_fvgs = smc_results.get("unmitigated_fvgs", [])
    unmitigated_internal_obs = smc_results.get("unmitigated_internal_obs", [])
    unmitigated_swing_obs = smc_results.get("unmitigated_swing_obs", [])
    all_obs = unmitigated_internal_obs + (unmitigated_swing_obs if use_swing_ob else [])

    signal_side = None
    signal_price = None
    signal_context = {}

    # ==================== LONG SETUP ====================
    # Gate 1: P/D zone — must be in discount (price below midpoint of range)
    long_pd_ok = (not use_pd_filter) or (pd_pct <= 0.5)
    # Gate 2: SSL liquidity sweep — stops below were taken, reversal up expected
    long_sweep_ok = (not use_liquidity_sweep_filter) or recent_ssl_sweep

    if long_pd_ok and long_sweep_ok:
        # Priority 1: FVG long (tighter zone, cleaner entry)
        for fvg in unmitigated_fvgs:
            if fvg.bias != Bias.BULLISH:
                continue
            if current_candle['low'] <= fvg.top and current_candle['close'] >= fvg.bottom:
                if use_entry_confirmation:
                    candle_ok = is_bullish_candle or (
                        use_rejection_candle and _is_rejection_candle(current_candle, 'buy')
                    )
                    if not candle_ok:
                        continue
                signal_side = "buy"
                signal_price = current_candle['close']
                signal_context = {
                    'type': 'fvg',
                    'level_low': fvg.bottom,
                    'level_high': fvg.top,
                    'bias': 'bullish',
                    'fvg_size_pct': fvg.size_pct,
                    'pd_zone': pd_zone,
                    'pd_pct': pd_pct,
                    'ssl_swept': recent_ssl_sweep,
                }
                break

        # Priority 2: OB long
        if not signal_side:
            for ob in all_obs:
                if ob.bias != Bias.BULLISH:
                    continue
                if current_candle['low'] <= ob.barHigh and current_candle['close'] >= ob.barLow:
                    if not _ob_quality_ok(ob, min_ob_quality, max_ob_touches):
                        continue
                    if use_entry_confirmation:
                        candle_ok = is_bullish_candle or (
                            use_rejection_candle and _is_rejection_candle(current_candle, 'buy')
                        )
                        if not candle_ok:
                            continue
                    signal_side = "buy"
                    signal_price = current_candle['close']
                    signal_context = {
                        'type': 'order_block',
                        'level_low': ob.barLow,
                        'level_high': ob.barHigh,
                        'bias': 'bullish',
                        'ob_quality': ob.quality,
                        'ob_touches': ob.touch_count,
                        'pd_zone': pd_zone,
                        'pd_pct': pd_pct,
                        'ssl_swept': recent_ssl_sweep,
                    }
                    break

    # ==================== SHORT SETUP ====================
    if not signal_side:
        # Gate 1: P/D zone — must be in premium (price above midpoint)
        short_pd_ok = (not use_pd_filter) or (pd_pct >= 0.5)
        # Gate 2: BSL liquidity sweep — stops above were taken, reversal down expected
        short_sweep_ok = (not use_liquidity_sweep_filter) or recent_bsl_sweep

        if short_pd_ok and short_sweep_ok:
            # Priority 1: FVG short
            for fvg in unmitigated_fvgs:
                if fvg.bias != Bias.BEARISH:
                    continue
                if current_candle['high'] >= fvg.bottom and current_candle['close'] <= fvg.top:
                    if use_entry_confirmation:
                        candle_ok = is_bearish_candle or (
                            use_rejection_candle and _is_rejection_candle(current_candle, 'sell')
                        )
                        if not candle_ok:
                            continue
                    signal_side = "sell"
                    signal_price = current_candle['close']
                    signal_context = {
                        'type': 'fvg',
                        'level_low': fvg.bottom,
                        'level_high': fvg.top,
                        'bias': 'bearish',
                        'fvg_size_pct': fvg.size_pct,
                        'pd_zone': pd_zone,
                        'pd_pct': pd_pct,
                        'bsl_swept': recent_bsl_sweep,
                    }
                    break

            # Priority 2: OB short
            if not signal_side:
                for ob in all_obs:
                    if ob.bias != Bias.BEARISH:
                        continue
                    if current_candle['high'] >= ob.barLow and current_candle['close'] <= ob.barHigh:
                        if not _ob_quality_ok(ob, min_ob_quality, max_ob_touches):
                            continue
                        if use_entry_confirmation:
                            candle_ok = is_bearish_candle or (
                                use_rejection_candle and _is_rejection_candle(current_candle, 'sell')
                            )
                            if not candle_ok:
                                continue
                        signal_side = "sell"
                        signal_price = current_candle['close']
                        signal_context = {
                            'type': 'order_block',
                            'level_low': ob.barLow,
                            'level_high': ob.barHigh,
                            'bias': 'bearish',
                            'ob_quality': ob.quality,
                            'ob_touches': ob.touch_count,
                            'pd_zone': pd_zone,
                            'pd_pct': pd_pct,
                            'bsl_swept': recent_bsl_sweep,
                        }
                        break

    if not signal_side:
        return None, None, None

    # ==================== FINAL FILTERS ====================

    # MTF alignment
    if use_mtf_filter:
        if market_bias == Bias.BULLISH and signal_side == "sell":
            return None, None, None
        if market_bias == Bias.BEARISH and signal_side == "buy":
            return None, None, None

    # ADX directional filter
    if use_adx_filter:
        try:
            adx_pos = current_candle.get('adx_pos', np.nan)
            adx_neg = current_candle.get('adx_neg', np.nan)
            if not (pd.isna(adx_pos) or pd.isna(adx_neg)):
                if signal_side == "buy" and adx_pos < adx_neg:
                    return None, None, None
                if signal_side == "sell" and adx_neg < adx_pos:
                    return None, None, None
        except Exception:
            return None, None, None

    return signal_side, signal_price, signal_context
