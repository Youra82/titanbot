# src/titanbot/analysis/walk_forward_test.py
"""Walk-forward test: evaluates different backtest_lookback_weeks values.

Tests lookback values [1, 2, 3, 4, 6, 8] weeks.
For each value: runs all configs, computes avg PnL%, avg win-rate, avg drawdown.
Recommends optimal lookback.
"""

import os
import sys
import argparse

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
sys.path.insert(0, os.path.join(PROJECT_ROOT, 'src'))

from titanbot.analysis.analysis_utils import (
    GREEN, YELLOW, RED, CYAN, NC,
    get_settings, get_date_range, load_all_configs,
    run_backtest_for_config, send_chart_telegram,
)


LOOKBACK_VALUES = [1, 2, 3, 4, 6, 8]


def main():
    parser = argparse.ArgumentParser(description='Walk-Forward Test: verschiedene Lookback-Wochen testen')
    parser.add_argument('--capital', type=float, default=None, help='Start-Kapital in USDT')
    parser.add_argument('--min-trades', type=int, default=2, help='Min. Trades pro Config im Lookback-Fenster')
    parser.add_argument('--no-telegram', action='store_true', help='Kein Telegram-Report')
    args = parser.parse_args()

    settings = get_settings()
    start_capital = args.capital or settings.get('optimization_settings', {}).get('start_capital', 20)

    configs = load_all_configs()
    if not configs:
        print(f"{RED}Keine Configs gefunden.{NC}")
        sys.exit(1)

    print(f"\n{CYAN}=== Walk-Forward Test ==={NC}")
    print(f"  {len(configs)} Configs, Kapital: {start_capital} USDT")
    print(f"  Lookback-Werte: {LOOKBACK_VALUES} Wochen\n")

    rows = []
    for lw in LOOKBACK_VALUES:
        start_date, end_date, warmup_date = get_date_range(lookback_weeks=lw)
        print(f"{CYAN}--- Lookback {lw}w  ({start_date} → {end_date}, Warmup bis {warmup_date}) ---{NC}")

        pnls, wrs, dds, trade_counts = [], [], [], []
        for cfg in configs:
            ret = run_backtest_for_config(cfg, start_date, end_date, start_capital, warmup_date)
            if ret is None:
                continue
            result, label = ret
            if result.get('trades_count', 0) < args.min_trades:
                continue
            pnls.append(result.get('total_pnl_pct', 0))
            wrs.append(result.get('win_rate', 0))
            dds.append(result.get('max_drawdown_pct', 0) * 100)
            trade_counts.append(result.get('trades_count', 0))

        if not pnls:
            print(f"  {YELLOW}Keine Ergebnisse.{NC}")
            rows.append({'lw': lw, 'avg_pnl': None, 'avg_wr': None, 'avg_dd': None, 'configs': 0})
            continue

        avg_pnl = sum(pnls) / len(pnls)
        avg_wr  = sum(wrs) / len(wrs)
        avg_dd  = sum(dds) / len(dds)
        avg_tc  = sum(trade_counts) / len(trade_counts)
        rows.append({'lw': lw, 'avg_pnl': avg_pnl, 'avg_wr': avg_wr, 'avg_dd': avg_dd,
                     'avg_trades': avg_tc, 'configs': len(pnls)})
        colour = GREEN if avg_pnl > 0 else RED
        print(f"  PnL: {colour}{avg_pnl:+.2f}%{NC}  WR: {avg_wr:.1f}%  DD: {avg_dd:.1f}%  Trades/Config: {avg_tc:.1f}  ({len(pnls)} Configs)")

    # Summary table
    print(f"\n{CYAN}{'='*65}{NC}")
    print(f"{'Lookback':>10}{'Configs':>10}{'Avg PnL%':>12}{'Avg WR%':>10}{'Avg DD%':>10}{'Avg Trades':>12}")
    print(f"{'-'*65}")
    best_row = None
    best_score = float('-inf')
    for r in rows:
        if r['avg_pnl'] is None:
            print(f"{r['lw']:>8}w {'N/A':>10} {'N/A':>12} {'N/A':>10} {'N/A':>10} {'N/A':>12}")
            continue
        score = r['avg_pnl'] - r['avg_dd'] + r['avg_wr'] * 0.1
        if score > best_score:
            best_score = score
            best_row = r
        colour = GREEN if r['avg_pnl'] > 0 else RED
        print(f"{r['lw']:>8}w {r['configs']:>10} {colour}{r['avg_pnl']:>+11.2f}%{NC} {r['avg_wr']:>9.1f}% {r['avg_dd']:>9.1f}% {r['avg_trades']:>11.1f}")

    print(f"{CYAN}{'='*65}{NC}")
    if best_row:
        print(f"\n{GREEN}Empfehlung: backtest_lookback_weeks = {best_row['lw']}{NC}")
        print(f"  Score (PnL - DD + WR*0.1): {best_score:.2f}")
        print(f"  Avg PnL: {best_row['avg_pnl']:+.2f}%  |  Avg WR: {best_row['avg_wr']:.1f}%  |  Avg DD: {best_row['avg_dd']:.1f}%")

    # Optional chart
    if not args.no_telegram and best_row:
        try:
            import matplotlib
            matplotlib.use('Agg')
            import matplotlib.pyplot as plt
            import numpy as np

            valid = [r for r in rows if r['avg_pnl'] is not None]
            lws   = [r['lw'] for r in valid]
            pnls  = [r['avg_pnl'] for r in valid]
            dds   = [r['avg_dd'] for r in valid]
            wrs   = [r['avg_wr'] for r in valid]

            fig, axes = plt.subplots(3, 1, figsize=(10, 9))
            fig.suptitle('Walk-Forward Test: Lookback-Wochen Analyse', fontsize=13, fontweight='bold')

            colors = ['green' if p > 0 else 'red' for p in pnls]
            axes[0].bar(lws, pnls, color=colors)
            axes[0].axhline(0, color='black', linewidth=0.8)
            axes[0].set_ylabel('Avg PnL%')
            axes[0].set_title('Durchschnittlicher PnL pro Lookback')
            axes[0].set_xticks(lws)

            axes[1].bar(lws, wrs, color='steelblue')
            axes[1].axhline(50, color='orange', linewidth=1, linestyle='--', label='50%')
            axes[1].set_ylabel('Avg Win-Rate%')
            axes[1].set_title('Durchschnittliche Win-Rate')
            axes[1].set_xticks(lws)
            axes[1].legend()

            axes[2].bar(lws, dds, color='salmon')
            axes[2].set_ylabel('Avg Max DD%')
            axes[2].set_title('Durchschnittlicher Max Drawdown')
            axes[2].set_xticks(lws)

            plt.xlabel('Lookback (Wochen)')
            plt.tight_layout()

            caption = (f"Walk-Forward Test | Empfehlung: {best_row['lw']}w | "
                       f"PnL {best_row['avg_pnl']:+.1f}% | DD {best_row['avg_dd']:.1f}%")
            send_chart_telegram(fig, caption)
            plt.close(fig)
        except Exception as e:
            print(f"{YELLOW}Chart-Fehler: {e}{NC}")


if __name__ == '__main__':
    main()
