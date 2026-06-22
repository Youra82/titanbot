# src/titanbot/analysis/fvg_analysis.py
"""Fair Value Gap (FVG) size threshold sweep analysis.

Tests min_fvg_size_pct sweep: [0.02, 0.05, 0.1, 0.15, 0.2, 0.3, 0.5]
For each threshold: runs all configs with overridden min_fvg_size_pct -> avg PnL, trades, win-rate.
Shows optimal threshold.
"""

import os
import sys
import argparse
try:
    from tqdm import tqdm
except ImportError:
    def tqdm(it, **kw): return it

import copy

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
sys.path.insert(0, os.path.join(PROJECT_ROOT, 'src'))

from titanbot.analysis.analysis_utils import (
    GREEN, YELLOW, RED, CYAN, NC,
    get_settings, get_date_range, load_all_configs,
    run_backtest_for_config, send_chart_telegram,
)

FVG_SIZE_VALUES = [0.02, 0.05, 0.1, 0.15, 0.2, 0.3, 0.5]


def main():
    parser = argparse.ArgumentParser(description='FVG-Size Threshold Sweep Analyse')
    parser.add_argument('--capital',     type=float, default=None, help='Start-Kapital in USDT')
    parser.add_argument('--no-telegram', action='store_true',      help='Kein Telegram-Report')
    args = parser.parse_args()

    settings = get_settings()
    start_capital = args.capital or settings.get('optimization_settings', {}).get('start_capital', 20)
    start_date, end_date, warmup_date = get_date_range()

    configs = load_all_configs()
    if not configs:
        print(f"{RED}Keine Configs gefunden.{NC}")
        sys.exit(1)

    print(f"\n{CYAN}=== FVG-Size Threshold Sweep ==={NC}")
    print(f"  Kapital: {start_capital} USDT | {len(configs)} Configs")
    print(f"  min_fvg_size_pct Werte: {FVG_SIZE_VALUES}\n")

    sweep_results = []

    for threshold in FVG_SIZE_VALUES:
        print(f"  Teste min_fvg_size_pct={threshold}...")
        pnls, wrs, trades = [], [], []

        for cfg in tqdm(configs, desc="  Configs", unit="cfg", leave=False,
                        bar_format="{desc}: {n_fmt}/{total_fmt} [{bar:25}] {elapsed}"):
            new_cfg = copy.deepcopy(cfg)
            new_cfg.setdefault('strategy', {})['min_fvg_size_pct'] = threshold

            ret = run_backtest_for_config(new_cfg, start_date, end_date, start_capital, warmup_date)
            if ret is None:
                continue
            result, _ = ret
            pnls.append(result.get('total_pnl_pct', 0))
            wrs.append(result.get('win_rate', 0))
            trades.append(result.get('trades_count', 0))

        if not pnls:
            sweep_results.append({'threshold': threshold, 'avg_pnl': None})
            continue

        avg_pnl    = sum(pnls)   / len(pnls)
        avg_wr     = sum(wrs)    / len(wrs)
        avg_trades = sum(trades) / len(trades)
        sweep_results.append({
            'threshold': threshold, 'avg_pnl': avg_pnl,
            'avg_wr': avg_wr, 'avg_trades': avg_trades, 'n': len(pnls),
        })
        col = GREEN if avg_pnl > 0 else RED
        print(f"    PnL: {col}{avg_pnl:+.2f}%{NC}  WR: {avg_wr:.1f}%  Avg Trades: {avg_trades:.1f}")

    # Find optimal
    valid = [r for r in sweep_results if r['avg_pnl'] is not None and r['avg_trades'] >= 2]
    best  = max(valid, key=lambda r: r['avg_pnl'], default=None)

    print(f"\n{CYAN}{'min_fvg_size_pct':>18}  {'Avg PnL%':>10}  {'Avg WR%':>9}  {'Avg Trades':>11}  {'Status'}{NC}")
    print('-' * 70)
    for r in sweep_results:
        if r['avg_pnl'] is None:
            print(f"{r['threshold']:>18.3f}  {'N/A':>10}")
            continue
        col  = GREEN if r['avg_pnl'] > 0 else RED
        mark = f"  {GREEN}<-- OPTIMAL{NC}" if best and r['threshold'] == best['threshold'] else ""
        print(f"{r['threshold']:>18.3f}  {col}{r['avg_pnl']:>+9.2f}%{NC}  {r['avg_wr']:>8.1f}%  {r['avg_trades']:>10.1f}{mark}")

    if best:
        print(f"\n{GREEN}Optimaler min_fvg_size_pct: {best['threshold']:.3f}{NC}")
        print(f"  Avg PnL: {best['avg_pnl']:+.2f}%  |  Avg WR: {best['avg_wr']:.1f}%  |  Avg Trades: {best['avg_trades']:.1f}")

    # Trade-off note
    if valid:
        hi_trades = max(valid, key=lambda r: r['avg_trades'])
        hi_pnl    = max(valid, key=lambda r: r['avg_pnl'])
        if hi_trades['threshold'] != hi_pnl['threshold']:
            print(f"\n  {YELLOW}Hinweis: Meiste Trades bei {hi_trades['threshold']:.3f} ({hi_trades['avg_trades']:.1f}) "
                  f"vs. Bester PnL bei {hi_pnl['threshold']:.3f} ({hi_pnl['avg_pnl']:+.2f}%){NC}")

    if not args.no_telegram and valid:
        try:
            import matplotlib
            matplotlib.use('Agg')
            import matplotlib.pyplot as plt

            thresholds = [r['threshold'] for r in sweep_results if r['avg_pnl'] is not None]
            avg_pnls   = [r['avg_pnl']    for r in sweep_results if r['avg_pnl'] is not None]
            avg_trades = [r['avg_trades'] for r in sweep_results if r['avg_pnl'] is not None]
            avg_wrs    = [r['avg_wr']     for r in sweep_results if r['avg_pnl'] is not None]

            fig, axes = plt.subplots(3, 1, figsize=(10, 10))
            labels = [f"{t:.3f}" for t in thresholds]

            colors = ['green' if p > 0 else 'red' for p in avg_pnls]
            axes[0].bar(labels, avg_pnls, color=colors, alpha=0.8)
            axes[0].axhline(0, color='black', linewidth=0.8)
            if best:
                best_lbl = f"{best['threshold']:.3f}"
                if best_lbl in labels:
                    axes[0].bar([best_lbl], [best['avg_pnl']], color='gold', alpha=1.0, label='Optimal')
                    axes[0].legend()
            axes[0].set_ylabel('Avg PnL%')
            axes[0].set_title('FVG-Size Threshold: Avg PnL%')

            axes[1].bar(labels, avg_wrs, color='steelblue', alpha=0.8)
            axes[1].axhline(50, color='orange', linewidth=1, linestyle='--')
            axes[1].set_ylabel('Avg Win-Rate%')
            axes[1].set_title('FVG-Size Threshold: Avg Win-Rate%')

            axes[2].bar(labels, avg_trades, color='purple', alpha=0.7)
            axes[2].set_ylabel('Avg Trades/Config')
            axes[2].set_title('FVG-Size Threshold: Avg Trades (höher = mehr Signale)')
            axes[2].set_xlabel('min_fvg_size_pct')

            plt.tight_layout()
            caption = (f"FVG-Size Sweep | Optimal: {best['threshold'] if best else 'N/A'} | "
                       + (f"PnL {best['avg_pnl']:+.1f}%" if best else ""))
            send_chart_telegram(fig, caption)
            plt.close(fig)
        except Exception as e:
            print(f"{YELLOW}Chart-Fehler: {e}{NC}")


if __name__ == '__main__':
    main()
