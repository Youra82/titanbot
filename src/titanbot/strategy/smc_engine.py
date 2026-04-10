import pandas as pd
import numpy as np
from dataclasses import dataclass, field
from enum import Enum


# ==================== ENUMS ====================

class Leg(Enum):
    BULLISH = 1
    BEARISH = 0

class Bias(Enum):
    BULLISH = 1
    BEARISH = -1
    NEUTRAL = 0


# ==================== DATACLASSES ====================

@dataclass
class Pivot:
    currentLevel: float = np.nan
    lastLevel: float = np.nan
    crossed: bool = False
    barTime: int = 0
    barIndex: int = 0

@dataclass
class OrderBlock:
    barHigh: float
    barLow: float
    barTime: int
    bias: Bias
    mitigated: bool = False
    touch_count: int = 0          # Times price entered zone without mitigating
    bos_move_pct: float = 0.0    # BOS strength: distance moved after BOS / entry price
    quality: float = 0.5          # Composite quality score 0.0-1.0

@dataclass
class FVG:
    top: float
    bottom: float
    bias: Bias
    startTime: int
    size_pct: float = 0.0        # Size as % of price (filters noise)
    mitigated: bool = False

@dataclass
class LiquidityLevel:
    """
    BSL (Buy-Side Liquidity): highs where stop-losses of shorts accumulate.
    SSL (Sell-Side Liquidity): lows where stop-losses of longs accumulate.
    A sweep = price wicks through the level and closes the other side.
    After SSL swept → expect long. After BSL swept → expect short.
    """
    price: float
    bias: str                    # 'bsl' (above price) or 'ssl' (below price)
    bar_index: int
    bar_time: int
    is_equal: bool = False       # Equal high/low pool = stronger liquidity
    swept: bool = False
    sweep_bar: int = -1


# ==================== ENGINE ====================

class SMCEngine:
    """
    Professional SMC Engine implementing:
    - Swing/Internal Pivots with BOS and CHoCH detection
    - Order Blocks (Swing + Internal) with quality scoring
    - Fair Value Gaps with minimum size filter
    - Liquidity Levels (BSL/SSL) with sweep detection
    - Equal High/Low detection (stronger liquidity pools)
    - Premium/Discount zone tracking
    - Per-candle state enrichment for downstream signal logic
    """

    def __init__(self, settings: dict):
        self.swingsLength = settings.get('swingsLength', 50)
        self.internalLength = 5
        self.ob_mitigation = settings.get('ob_mitigation', 'High/Low')
        self.min_fvg_size_pct = settings.get('min_fvg_size_pct', 0.05) / 100.0
        self.equal_level_threshold = 0.001  # 0.1% for equal high/low detection
        self.liquidity_lookback = settings.get('liquidity_lookback', 20)

        # Pivot state
        self.swingHigh = Pivot()
        self.swingLow = Pivot()
        self.internalHigh = Pivot()
        self.internalLow = Pivot()
        self.swingTrend = Bias.NEUTRAL
        self.internalTrend = Bias.NEUTRAL
        self.swing_leg_state = Leg.BULLISH
        self.internal_leg_state = Leg.BULLISH

        # OHLC storage
        self.highs: list = []
        self.lows: list = []
        self.closes: list = []
        self.opens: list = []
        self.times: list = []

        # SMC structures
        self.swingOrderBlocks: list[OrderBlock] = []
        self.internalOrderBlocks: list[OrderBlock] = []
        self.fairValueGaps: list[FVG] = []
        self.liquidityLevels: list[LiquidityLevel] = []
        self.event_log: list = []

        # Per-candle state (one dict per bar)
        self.bar_states: list = []

        # Premium/Discount range tracking
        self.pd_high: float = np.nan   # Last confirmed swing high
        self.pd_low: float = np.nan    # Last confirmed swing low

    # ==================== PIVOT DETECTION ====================

    def _leg(self, size: int, index: int, current_leg_state: Leg) -> Leg:
        """Pine Script 'leg' function port: confirms a pivot `size` bars ago."""
        if index < size:
            return current_leg_state
        try:
            window_highs = self.highs[index - size + 1: index + 1]
            window_lows = self.lows[index - size + 1: index + 1]
            if not window_highs or not window_lows:
                return current_leg_state
            pivot_high_candidate = self.highs[index - size]
            pivot_low_candidate = self.lows[index - size]
            newLegHigh = pivot_high_candidate > max(window_highs)
            newLegLow = pivot_low_candidate < min(window_lows)
        except Exception:
            return current_leg_state

        if newLegHigh:
            return Leg.BEARISH
        elif newLegLow:
            return Leg.BULLISH
        else:
            return current_leg_state

    def _getCurrentStructure(self, size: int, index: int, internal: bool):
        """Detect and confirm new swing/internal pivots."""
        prev_leg = self.internal_leg_state if internal else self.swing_leg_state
        new_leg = self._leg(size, index, prev_leg)

        if internal:
            self.internal_leg_state = new_leg
        else:
            self.swing_leg_state = new_leg

        if new_leg == prev_leg:
            return

        pivot_index = index - size
        if pivot_index < 0:
            return

        pivot_time = self.times[pivot_index]

        if new_leg == Leg.BULLISH:  # Confirmed pivot LOW
            p = self.internalLow if internal else self.swingLow
            p.lastLevel = p.currentLevel
            p.currentLevel = self.lows[pivot_index]
            p.crossed = False
            p.barTime = pivot_time
            p.barIndex = pivot_index
            if not internal:
                self._addLiquidityLevel(self.lows[pivot_index], 'ssl', pivot_index, pivot_time)
                self.pd_low = self.lows[pivot_index]

        elif new_leg == Leg.BEARISH:  # Confirmed pivot HIGH
            p = self.internalHigh if internal else self.swingHigh
            p.lastLevel = p.currentLevel
            p.currentLevel = self.highs[pivot_index]
            p.crossed = False
            p.barTime = pivot_time
            p.barIndex = pivot_index
            if not internal:
                self._addLiquidityLevel(self.highs[pivot_index], 'bsl', pivot_index, pivot_time)
                self.pd_high = self.highs[pivot_index]

    # ==================== BOS / CHOCH ====================

    def _storeOrderBlock(self, pivot: Pivot, index: int, internal: bool,
                         bias: Bias, bos_move: float):
        """Store the OB candle created by a BOS/CHoCH with quality scoring."""
        if pivot.barIndex >= index or pivot.barIndex < 0:
            return
        try:
            if bias == Bias.BULLISH:
                ob_idx_in_window = int(np.argmax(self.highs[pivot.barIndex: index]))
            else:
                ob_idx_in_window = int(np.argmin(self.lows[pivot.barIndex: index]))

            ob_index = pivot.barIndex + ob_idx_in_window
            ob_high = self.highs[ob_index]
            ob_low = self.lows[ob_index]
            ob_size = ob_high - ob_low
            entry_price = self.closes[ob_index] if self.closes[ob_index] > 0 else 1.0

            # Quality: BOS strength relative to OB size, capped at 1.0
            bos_strength = (bos_move / ob_size) if ob_size > 0 else 0.0
            quality = min(1.0, bos_strength / 5.0)

            new_ob = OrderBlock(
                barHigh=ob_high,
                barLow=ob_low,
                barTime=self.times[ob_index],
                bias=bias,
                bos_move_pct=bos_move / entry_price,
                quality=quality,
            )
            ob_list = self.internalOrderBlocks if internal else self.swingOrderBlocks
            ob_list.append(new_ob)
        except Exception:
            pass

    def _displayStructure(self, index: int, internal: bool):
        """Check for BOS/CHoCH and store resulting Order Block."""
        current_close = self.closes[index]
        current_time = self.times[index]
        pivot_high = self.internalHigh if internal else self.swingHigh
        pivot_low = self.internalLow if internal else self.swingLow
        trend = self.internalTrend if internal else self.swingTrend
        prefix = 'Internal' if internal else 'Swing'

        # Bullish break
        if (not pivot_high.crossed and
                not pd.isna(pivot_high.currentLevel) and
                current_close > pivot_high.currentLevel):
            tag = "CHoCH" if trend == Bias.BEARISH else "BOS"
            pivot_high.crossed = True
            bos_move = current_close - pivot_high.currentLevel
            self.event_log.append({
                "time": current_time, "index": index,
                "type": f"{prefix} Bullish {tag}",
                "level": pivot_high.currentLevel,
            })
            self._storeOrderBlock(pivot_high, index, internal, Bias.BULLISH, bos_move)
            if internal:
                self.internalTrend = Bias.BULLISH
            else:
                self.swingTrend = Bias.BULLISH

        # Bearish break
        if (not pivot_low.crossed and
                not pd.isna(pivot_low.currentLevel) and
                current_close < pivot_low.currentLevel):
            tag = "CHoCH" if trend == Bias.BULLISH else "BOS"
            pivot_low.crossed = True
            bos_move = pivot_low.currentLevel - current_close
            self.event_log.append({
                "time": current_time, "index": index,
                "type": f"{prefix} Bearish {tag}",
                "level": pivot_low.currentLevel,
            })
            self._storeOrderBlock(pivot_low, index, internal, Bias.BEARISH, bos_move)
            if internal:
                self.internalTrend = Bias.BEARISH
            else:
                self.swingTrend = Bias.BEARISH

    # ==================== OB MITIGATION ====================

    def _deleteOrderBlocks(self, index: int):
        """Mitigate OBs when price closes through them. Track touch count."""
        c_high = self.highs[index]
        c_low = self.lows[index]
        c_close = self.closes[index]

        bearish_source = c_close if self.ob_mitigation == 'Close' else c_high
        bullish_source = c_close if self.ob_mitigation == 'Close' else c_low

        for ob in self.internalOrderBlocks + self.swingOrderBlocks:
            if ob.mitigated:
                continue
            if ob.bias == Bias.BEARISH and bearish_source > ob.barHigh:
                ob.mitigated = True
            elif ob.bias == Bias.BULLISH and bullish_source < ob.barLow:
                ob.mitigated = True
            else:
                # Price entered zone without mitigation → increment touch count
                if ob.bias == Bias.BULLISH and c_low <= ob.barHigh and c_close >= ob.barLow:
                    ob.touch_count += 1
                elif ob.bias == Bias.BEARISH and c_high >= ob.barLow and c_close <= ob.barHigh:
                    ob.touch_count += 1

    # ==================== FVG ====================

    def _drawFairValueGaps(self, index: int):
        """Detect Fair Value Gaps (3-candle pattern) with minimum size filter."""
        if index < 2:
            return
        c_high = self.highs[index]
        c_low = self.lows[index]
        c_close = self.closes[index]
        c2_high = self.highs[index - 2]
        c2_low = self.lows[index - 2]
        c_time = self.times[index]

        bullish_fvg = (c_low > c2_high) and (c_close > c2_high)
        bearish_fvg = (c_high < c2_low) and (c_close < c2_low)

        if bullish_fvg:
            size = c_low - c2_high
            size_pct = size / c2_high if c2_high > 0 else 0.0
            if size_pct >= self.min_fvg_size_pct:
                self.fairValueGaps.append(FVG(
                    top=c_low, bottom=c2_high,
                    bias=Bias.BULLISH, startTime=c_time, size_pct=size_pct
                ))
                self.event_log.append({
                    "time": c_time, "index": index,
                    "type": "Bullish FVG", "level": (c_low, c2_high),
                })

        if bearish_fvg:
            size = c2_low - c_high
            size_pct = size / c2_low if c2_low > 0 else 0.0
            if size_pct >= self.min_fvg_size_pct:
                self.fairValueGaps.append(FVG(
                    top=c2_low, bottom=c_high,
                    bias=Bias.BEARISH, startTime=c_time, size_pct=size_pct
                ))
                self.event_log.append({
                    "time": c_time, "index": index,
                    "type": "Bearish FVG", "level": (c2_low, c_high),
                })

    def _deleteFairValueGaps(self, index: int):
        """Mitigate FVGs when price closes through them."""
        c_low = self.lows[index]
        c_high = self.highs[index]
        for fvg in self.fairValueGaps:
            if fvg.mitigated:
                continue
            if fvg.bias == Bias.BULLISH and c_low < fvg.bottom:
                fvg.mitigated = True
            elif fvg.bias == Bias.BEARISH and c_high > fvg.top:
                fvg.mitigated = True

    # ==================== LIQUIDITY ====================

    def _addLiquidityLevel(self, price: float, bias: str, bar_index: int, bar_time: int):
        """
        Register a new liquidity level (BSL from swing high, SSL from swing low).
        Mark as equal high/low if within threshold of an existing unswept level.
        """
        threshold = price * self.equal_level_threshold
        is_equal = False
        for lvl in self.liquidityLevels:
            if not lvl.swept and lvl.bias == bias and abs(lvl.price - price) <= threshold:
                is_equal = True
                lvl.is_equal = True
                break
        self.liquidityLevels.append(LiquidityLevel(
            price=price, bias=bias,
            bar_index=bar_index, bar_time=bar_time,
            is_equal=is_equal,
        ))

    def _checkLiquiditySweep(self, index: int):
        """
        Detect liquidity sweeps: price wicks through a level and closes the other side.
        BSL sweep: wick above high → close below → bearish reversal setup.
        SSL sweep: wick below low → close above → bullish reversal setup.
        """
        c_high = self.highs[index]
        c_low = self.lows[index]
        c_close = self.closes[index]
        c_time = self.times[index]

        for lvl in self.liquidityLevels:
            if lvl.swept or lvl.bar_index >= index:
                continue
            if lvl.bias == 'bsl' and c_high > lvl.price and c_close < lvl.price:
                lvl.swept = True
                lvl.sweep_bar = index
                self.event_log.append({
                    "time": c_time, "index": index,
                    "type": "BSL Sweep", "level": lvl.price,
                    "is_equal": lvl.is_equal,
                })
            elif lvl.bias == 'ssl' and c_low < lvl.price and c_close > lvl.price:
                lvl.swept = True
                lvl.sweep_bar = index
                self.event_log.append({
                    "time": c_time, "index": index,
                    "type": "SSL Sweep", "level": lvl.price,
                    "is_equal": lvl.is_equal,
                })

    # ==================== PREMIUM / DISCOUNT ====================

    def _get_pd_pct(self, price: float) -> float:
        """
        Position in the current P/D range.
        0.0 = at SSL (deepest discount), 1.0 = at BSL (deepest premium).
        0.5 = equilibrium.
        """
        if np.isnan(self.pd_high) or np.isnan(self.pd_low):
            return 0.5
        rng = self.pd_high - self.pd_low
        if rng <= 0:
            return 0.5
        return max(0.0, min(1.0, (price - self.pd_low) / rng))

    def _get_pd_zone(self, price: float) -> str:
        pct = self._get_pd_pct(price)
        if pct >= 0.618:
            return 'premium'
        elif pct <= 0.382:
            return 'discount'
        return 'equilibrium'

    # ==================== PER-CANDLE STATE ====================

    def _build_bar_state(self, index: int) -> dict:
        """Snapshot of all SMC state for this specific bar."""
        current_price = self.closes[index]
        lb = self.liquidity_lookback

        recent_bsl_sweep = False
        recent_ssl_sweep = False
        for lvl in self.liquidityLevels:
            if lvl.swept and 0 <= (index - lvl.sweep_bar) <= lb:
                if lvl.bias == 'bsl':
                    recent_bsl_sweep = True
                elif lvl.bias == 'ssl':
                    recent_ssl_sweep = True

        pd_pct = self._get_pd_pct(current_price)
        pd_zone = self._get_pd_zone(current_price)

        swing_bias = (
            'bullish' if self.swingTrend == Bias.BULLISH
            else 'bearish' if self.swingTrend == Bias.BEARISH
            else 'neutral'
        )
        internal_bias = (
            'bullish' if self.internalTrend == Bias.BULLISH
            else 'bearish' if self.internalTrend == Bias.BEARISH
            else 'neutral'
        )

        return {
            'pd_pct': pd_pct,
            'pd_zone': pd_zone,
            'pd_high': self.pd_high,
            'pd_low': self.pd_low,
            'recent_bsl_sweep': recent_bsl_sweep,
            'recent_ssl_sweep': recent_ssl_sweep,
            'swing_bias': swing_bias,
            'internal_bias': internal_bias,
        }

    # ==================== MAIN ENTRY POINT ====================

    def process_dataframe(self, df: pd.DataFrame) -> dict:
        """
        Process all candles and return SMC results + enriched DataFrame.

        Returns dict with:
          - events: list of all SMC events (BOS, CHoCH, FVG, Sweeps)
          - unmitigated_swing_obs: active swing order blocks
          - unmitigated_internal_obs: active internal order blocks
          - unmitigated_fvgs: active fair value gaps
          - liquidity_levels: all liquidity levels (swept and unswept)
          - bar_states: per-candle state list
          - enriched_df: original df with added smc_* columns
        """
        df = df.sort_index()
        self.highs = df['high'].tolist()
        self.lows = df['low'].tolist()
        self.closes = df['close'].tolist()
        self.opens = df['open'].tolist() if 'open' in df.columns else self.closes.copy()

        if pd.api.types.is_datetime64_any_dtype(df.index):
            self.times = df.index.astype(np.int64).tolist()
        else:
            self.times = list(range(len(df)))

        self.bar_states = []

        for i in range(len(df)):
            # Order matters — mirrors Pine Script execution order
            self._deleteFairValueGaps(i)
            self._getCurrentStructure(self.swingsLength, i, internal=False)
            self._getCurrentStructure(self.internalLength, i, internal=True)
            self._displayStructure(i, internal=True)
            self._displayStructure(i, internal=False)
            self._deleteOrderBlocks(i)
            self._checkLiquiditySweep(i)
            self._drawFairValueGaps(i)
            self.bar_states.append(self._build_bar_state(i))

        # Build enriched DataFrame with per-candle SMC columns
        enriched_df = df.copy()
        enriched_df['smc_pd_pct'] = [s['pd_pct'] for s in self.bar_states]
        enriched_df['smc_pd_zone'] = [s['pd_zone'] for s in self.bar_states]
        enriched_df['smc_pd_high'] = [s['pd_high'] for s in self.bar_states]
        enriched_df['smc_pd_low'] = [s['pd_low'] for s in self.bar_states]
        enriched_df['smc_recent_bsl_sweep'] = [s['recent_bsl_sweep'] for s in self.bar_states]
        enriched_df['smc_recent_ssl_sweep'] = [s['recent_ssl_sweep'] for s in self.bar_states]
        enriched_df['smc_swing_bias'] = [s['swing_bias'] for s in self.bar_states]
        enriched_df['smc_internal_bias'] = [s['internal_bias'] for s in self.bar_states]

        return {
            "events": self.event_log,
            "unmitigated_swing_obs": [ob for ob in self.swingOrderBlocks if not ob.mitigated],
            "unmitigated_internal_obs": [ob for ob in self.internalOrderBlocks if not ob.mitigated],
            "unmitigated_fvgs": [fvg for fvg in self.fairValueGaps if not fvg.mitigated],
            "liquidity_levels": self.liquidityLevels,
            "bar_states": self.bar_states,
            "enriched_df": enriched_df,
        }
