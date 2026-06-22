# src/titanbot/analysis/filter_impact.py
"""Filter combination impact analysis.

Tests 8 combinations of 3 SMC filters:
  use_pd_filter (True/False)
  use_liquidity_sweep_filter (True/False)
  use_rejection_candle (True/False)

For each combo: runs all configs with filters overridden -> avg PnL, trades, win-rate.
"""

import os
import sys
import argparse
try:
    from tqdm import tqdm
except ImportError:
    def tqdm(it, **kw): return it

import copy
import itertools

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
sys.path.insert(0, os.path.join(PROJECT_ROOT, 'src'))

from titanbot.analysis.analysis_utils import (
    GREEN, YELLOW, RED, CYAN, NC,
    get_settings, get_date_range, load_all_configs,
    run_backtest_for_config, send_chart_telegram,
)

FILTER_KEYS = ['use_pd_filter', 'use_liquidity_sweep_filter', 'use_rejection_candle']


def combo_label(combo):
    parts = []
    for key, val in zip(FILTER_KEYS, combo):
        short = key.replace('use_', '').replace('_filter', '').replace('liquidity_sweep', 'liq_sweep')
        parts.append(f"{short}={'Y' if val else 'N'}")
    return ' | '.join(parts)


def main():
    parser = argparse.ArgumentParser(description='Filter-Kombinationen Impact-Analyse')
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

    print(f"\n{CYAN}=== Filter-Kombination Impact-Analyse ==={NC}")
    print(f"  Kapital: {start_capital} USDT | {len(configs)} Configs")
    print(f"  Filter: {FILTER_KEYS}")
    print(f"  8 Kombinationen (True/False je Filter)\n")

    combos = list(itertools.product([True, False], repeat=3))
    combo_results = []

    for combo in combos:
        label = combo_label(combo)
        print(f"  {CYAN}Teste: {label}{NC}")

        pnls, wrs, trades = [], [], []
        for cfg in tqdm(configs, desc="  Configs", unit="cfg", leave=False,
                        bar_format="{desc}: {n_fmt}/{total_fmt} [{bar:25}] {elapsed}"):
            new_cfg = copy.deepcopy(cfg)
            for key, val in zip(FILTER_KEYS, combo):
                new_cfg.setdefault('strategy', {})[key] = val

            ret = run_backtest_for_config(new_cfg, start_date, end_date, start_capital, warmup_date)
            if ret is None:
                continue
            result, _ = ret
            pnls.append(result.get('total_pnl_pct', 0))
            wrs.append(result.get('win_rate', 0))
            trades.append(result.get('trades_count', 0))

        if not pnls:
            combo_results.append({'combo': combo, 'label': label, 'avg_pnl': None})
            continue

        avg_pnl    = sum(pnls)   / len(pnls)
        avg_wr     = sum(wrs)    / len(wrs)
        avg_trades = sum(trades) / len(trades)
        combo_results.append({
            'combo': combo, 'label': label,
            'avg_pnl': avg_pnl, 'avg_wr': avg_wr,
            'avg_trades': avg_trades, 'n': len(pnls),
        })
        col = GREEN if avg_pnl > 0 else RED
        print(f"    PnL: {col}{avg_pnl:+.2f}%{NC}  WR: {avg_wr:.1f}%  Trades: {avg_trades:.1f}")

    # Sort by avg_pnl
    valid = [r for r in combo_results if r['avg_pnl'] is not None]
    valid.sort(key=lambda r: r['avg_pnl'], reverse=True)

    print(f"\n{CYAN}=== Ranking der Filter-Kombinationen ==={NC}")
    print(f"{'Rang':>5}  {'pd_f':>5}  {'liq_f':>6}  {'rej_c':>6}  {'Avg PnL%':>10}  {'Avg WR%':>9}  {'Avg Trades':>11}")
    print('-' * 65)
    for i, r in enumerate(valid, 1):
        pd_f, liq_f, rej_c = r['combo']
        col = GREEN if r['avg_pnl'] > 0 else RED
        is_best = i == 1
        star = f" {GREEN}* BEST{NC}" if is_best else ""
        print(f"{i:>5}  {'Y' if pd_f else 'N':>5}  {'Y' if liq_f else 'N':>6}  {'Y' if rej_c else 'N':>6}  "
              f"{col}{r['avg_pnl']:>+9.2f}%{NC}  {r['avg_wr']:>8.1f}%  {r['avg_trades']:>10.1f}{star}")

    if valid:
        best = valid[0]
        worst = valid[-1]
        pd_f, liq_f, rej_c = best['combo']
        print(f"\n{GREEN}Beste Kombination:{NC}")
        print(f"  use_pd_filter={pd_f}, use_liquidity_sweep_filter={liq_f}, use_rejection_candle={rej_c}")
        print(f"  Avg PnL: {best['avg_pnl']:+.2f}%  |  Avg WR: {best['avg_wr']:.1f}%")
        if best['avg_pnl'] - worst['avg_pnl'] > 1:
            print(f"  {YELLOW}Hinweis: Filter-Wahl hat signifikante Auswirkung ({best['avg_pnl']-worst['avg_pnl']:.1f}% Spread){NC}")

    if not args.no_telegram and valid:
        try:
            import matplotlib
            matplotlib.use('Agg')
            import matplotlib.pyplot as plt
            import numpy as np

            labels = [f"pd={'Y' if r['combo'][0] else 'N'}/liq={'Y' if r['combo'][1] else 'N'}/rej={'Y' if r['combo'][2] else 'N'}"
                      for r in valid]
            pnls   = [r['avg_pnl'] for r in valid]
            colors = ['green' if p > 0 else 'red' for p in pnls]

            fig, ax = plt.subplots(figsize=(12, 5))
            ax.bar(labels, pnls, color=colors, alpha=0.8)
            ax.axhline(0, color='black', linewidth=0.8)
            ax.set_xlabel('Filter-Kombination')
            ax.set_ylabel('Avg PnL%')
            ax.set_title('Filter-Kombination Impact: Avg PnL%')
            ax.tick_params(axis='x', rotation=45)
            plt.tight_layout()

            caption = (f"Filter-Impact | Best: {valid[0]['avg_pnl']:+.1f}% | "
                       f"pd={valid[0]['combo'][0]}/liq={valid[0]['combo'][1]}/rej={valid[0]['combo'][2]}")
            send_chart_telegram(fig, caption)
            plt.close(fig)
        except Exception as e:
            print(f"{YELLOW}Chart-Fehler: {e}{NC}")


if __name__ == '__main__':
    main()
