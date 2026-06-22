# src/titanbot/analysis/regime_analysis.py
"""Market regime analysis for titanbot.

For each trade, classifies the market regime at entry using ADX + EMA50 + EMA200:
  TREND_UP:   EMA50 > EMA200 AND ADX > 25
  TREND_DOWN: EMA50 < EMA200 AND ADX > 25
  RANGE:      ADX < 20
  NEUTRAL:    else

Shows win-rate per regime, recommends disabling low-performing regimes.
"""

import os
import sys
import argparse
try:
    from tqdm import tqdm
except ImportError:
    def tqdm(it, **kw): return it

from datetime import datetime, timezone

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
sys.path.insert(0, os.path.join(PROJECT_ROOT, 'src'))

from titanbot.analysis.analysis_utils import (
    GREEN, YELLOW, RED, CYAN, NC,
    get_settings, get_date_range, load_all_configs,
    run_backtest_for_config, send_chart_telegram,
)

REGIMES = ['TREND_UP', 'TREND_DOWN', 'RANGE', 'NEUTRAL']


def classify_regime(adx_val, ema50_val, ema200_val):
    """Classify market regime based on ADX and EMA50/200."""
    try:
        adx   = float(adx_val)
        ema50 = float(ema50_val)
        ema200 = float(ema200_val)
    except (TypeError, ValueError):
        return 'NEUTRAL'

    if ema50 > ema200 and adx > 25:
        return 'TREND_UP'
    elif ema50 < ema200 and adx > 25:
        return 'TREND_DOWN'
    elif adx < 20:
        return 'RANGE'
    else:
        return 'NEUTRAL'


def load_regime_data(symbol, tf, start_date, end_date):
    """Load OHLCV data and compute EMA50, EMA200, ADX. Returns indexed DataFrame."""
    try:
        import pandas as pd
        import ta
        from titanbot.analysis.backtester import load_data

        data = load_data(symbol, tf, start_date, end_date)
        if data is None or data.empty:
            return None

        data['ema50']  = ta.trend.EMAIndicator(data['close'], window=50).ema_indicator()
        data['ema200'] = ta.trend.EMAIndicator(data['close'], window=200).ema_indicator()
        adx_ind = ta.trend.ADXIndicator(data['high'], data['low'], data['close'], window=14)
        data['adx_val'] = adx_ind.adx()
        data.dropna(inplace=True)
        return data
    except Exception as e:
        print(f"  {YELLOW}Regime-Daten Fehler: {e}{NC}")
        return None


def main():
    parser = argparse.ArgumentParser(description='Markt-Regime-Analyse für titanbot SMC')
    parser.add_argument('--min-samples', type=int, default=3,    help='Mindest-Trades pro Regime (default: 3)')
    parser.add_argument('--no-telegram', action='store_true',    help='Kein Telegram-Report')
    args = parser.parse_args()

    settings = get_settings()
    start_capital = settings.get('optimization_settings', {}).get('start_capital', 20)
    start_date, end_date, warmup_date = get_date_range()

    configs = load_all_configs()
    if not configs:
        print(f"{RED}Keine Configs gefunden.{NC}")
        sys.exit(1)

    print(f"\n{CYAN}=== Markt-Regime-Analyse ==={NC}")
    print(f"  Kapital: {start_capital} USDT | {len(configs)} Configs")
    print(f"  Regime: {REGIMES}\n")

    # Aggregate: regime -> {wins, total}
    regime_stats = {r: {'wins': 0, 'total': 0} for r in REGIMES}

    for cfg in configs:
        symbol = cfg.get('market', {}).get('symbol', '')
        tf     = cfg.get('market', {}).get('timeframe', '')

        ret = run_backtest_for_config(cfg, start_date, end_date, start_capital, warmup_date)
        if ret is None:
            continue
        result, label = ret

        trades_list = result.get('trades_list', [])
        if not trades_list:
            continue

        # Load regime data for this symbol/tf
        regime_data = load_regime_data(symbol, tf, start_date, end_date)
        if regime_data is None:
            continue

        import pandas as pd

        for trade in trades_list:
            entry_time = trade.get('entry_time')
            if entry_time is None:
                continue
            if isinstance(entry_time, str):
                try:
                    entry_time = pd.Timestamp(entry_time, tz='UTC')
                except Exception:
                    continue

            # Find nearest bar in regime_data at or before entry_time
            try:
                idx = regime_data.index.get_indexer([entry_time], method='pad')[0]
                if idx < 0:
                    continue
                bar = regime_data.iloc[idx]
                regime = classify_regime(bar['adx_val'], bar['ema50'], bar['ema200'])
            except Exception:
                regime = 'NEUTRAL'

            # Determine win/loss from trade exit vs entry price
            side_key = 'entry_long' if 'entry_long' in trade else 'entry_short'
            exit_key = 'exit_long'  if 'exit_long'  in trade else 'exit_short'
            try:
                ep = float(trade.get(side_key, {}).get('price', 0))
                xp = float(trade.get(exit_key, {}).get('price', 0))
                if ep <= 0 or xp <= 0:
                    continue
                if 'long' in side_key:
                    win = xp > ep
                else:
                    win = xp < ep
            except Exception:
                continue

            regime_stats[regime]['total'] += 1
            if win:
                regime_stats[regime]['wins'] += 1

    # Print results
    print(f"\n{CYAN}{'Regime':15} {'Trades':>8} {'Wins':>6} {'Win-Rate%':>12} {'Empfehlung'}{NC}")
    print('-' * 60)
    recommendations = []
    for regime in REGIMES:
        st    = regime_stats[regime]
        total = st['total']
        wins  = st['wins']
        if total == 0:
            print(f"{regime:15} {'N/A':>8}")
            continue
        wr = wins / total * 100
        colour = GREEN if wr >= 50 else (YELLOW if wr >= 35 else RED)
        rec = ''
        if total >= args.min_samples:
            if wr < 35:
                rec = f"{RED}Deaktivieren empfohlen{NC}"
                recommendations.append((regime, wr))
            elif wr >= 55:
                rec = f"{GREEN}Sehr gut{NC}"
        print(f"{regime:15} {total:>8} {wins:>6} {colour}{wr:>11.1f}%{NC}  {rec}")

    print('-' * 60)
    total_trades = sum(s['total'] for s in regime_stats.values())
    total_wins   = sum(s['wins']  for s in regime_stats.values())
    overall_wr   = total_wins / total_trades * 100 if total_trades > 0 else 0
    print(f"{'GESAMT':15} {total_trades:>8} {total_wins:>6} {overall_wr:>11.1f}%")

    if recommendations:
        print(f"\n{RED}Empfehlungen:{NC}")
        for regime, wr in recommendations:
            print(f"  - {regime} deaktivieren (Win-Rate: {wr:.1f}%)")
    else:
        print(f"\n{GREEN}Alle Regimes liegen im akzeptablen Bereich.{NC}")

    if not args.no_telegram:
        try:
            import matplotlib
            matplotlib.use('Agg')
            import matplotlib.pyplot as plt

            valid_regimes = [r for r in REGIMES if regime_stats[r]['total'] >= 1]
            if valid_regimes:
                wrs    = [regime_stats[r]['wins'] / regime_stats[r]['total'] * 100
                          if regime_stats[r]['total'] > 0 else 0 for r in valid_regimes]
                colors = ['green' if w >= 50 else 'red' for w in wrs]
                counts = [regime_stats[r]['total'] for r in valid_regimes]

                fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 5))
                ax1.bar(valid_regimes, wrs, color=colors)
                ax1.axhline(50, color='black', linestyle='--', linewidth=1)
                ax1.set_ylabel('Win-Rate%')
                ax1.set_title('Win-Rate per Regime')
                ax1.set_ylim(0, 100)

                ax2.bar(valid_regimes, counts, color='steelblue')
                ax2.set_ylabel('Anzahl Trades')
                ax2.set_title('Trades per Regime')

                fig.suptitle('Markt-Regime-Analyse', fontweight='bold')
                plt.tight_layout()
                caption = (f"Regime-Analyse | Gesamt: {total_trades} Trades | "
                           f"WR: {overall_wr:.1f}%")
                send_chart_telegram(fig, caption)
                plt.close(fig)
        except Exception as e:
            print(f"{YELLOW}Chart-Fehler: {e}{NC}")


if __name__ == '__main__':
    main()
