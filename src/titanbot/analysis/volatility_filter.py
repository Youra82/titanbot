# src/titanbot/analysis/volatility_filter.py
"""ADX volatility filter analysis.

Tests use_adx_filter=True with adx_threshold values: [15, 20, 25, 30, 35]
Also compares to use_adx_filter=False (baseline).
Shows trades, win-rate, PnL, drawdown per threshold.
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
    run_backtest_for_config, save_send,
)

ADX_THRESHOLDS = [15, 20, 25, 30, 35]


def run_with_adx(configs, use_adx, adx_threshold, start_date, end_date,
                 start_capital, warmup_date, risk_override=None):
    pnls, wrs, trades, dds = [], [], [], []
    for cfg in configs:
        new_cfg = copy.deepcopy(cfg)
        new_cfg.setdefault('strategy', {})['use_adx_filter']  = use_adx
        new_cfg['strategy']['adx_threshold'] = adx_threshold
        if risk_override is not None:
            new_cfg.setdefault('risk', {})['risk_per_trade_pct'] = risk_override

        ret = run_backtest_for_config(new_cfg, start_date, end_date, start_capital, warmup_date)
        if ret is None:
            continue
        result, _ = ret
        pnls.append(result.get('total_pnl_pct', 0))
        wrs.append(result.get('win_rate', 0))
        trades.append(result.get('trades_count', 0))
        dds.append(result.get('max_drawdown_pct', 0) * 100)

    if not pnls:
        return None
    return {
        'avg_pnl':    sum(pnls)   / len(pnls),
        'avg_wr':     sum(wrs)    / len(wrs),
        'avg_trades': sum(trades) / len(trades),
        'avg_dd':     sum(dds)    / len(dds),
        'n':          len(pnls),
    }


def main():
    parser = argparse.ArgumentParser(description='ADX-Volatilitätsfilter Analyse')
    parser.add_argument('--capital',     type=float, default=None, help='Start-Kapital in USDT')
    parser.add_argument('--risk',        type=float, default=None, help='Risiko pro Trade % (override)')
    parser.add_argument('--no-telegram', action='store_true',      help='Kein Telegram-Report')
    args = parser.parse_args()

    settings = get_settings()
    start_capital = args.capital or settings.get('optimization_settings', {}).get('start_capital', 20)
    start_date, end_date, warmup_date = get_date_range()

    configs = load_all_configs()
    if not configs:
        print(f"{RED}Keine Configs gefunden.{NC}")
        sys.exit(1)

    print(f"\n{CYAN}=== ADX-Volatilitätsfilter Analyse ==={NC}")
    print(f"  Kapital: {start_capital} USDT | {len(configs)} Configs")
    print(f"  ADX-Thresholds: {ADX_THRESHOLDS}\n")

    rows = []

    # Baseline: no ADX filter
    print(f"  {CYAN}Baseline: use_adx_filter=False...{NC}")
    baseline = run_with_adx(configs, False, 0, start_date, end_date,
                            start_capital, warmup_date, args.risk)
    if baseline:
        baseline['label'] = 'BASELINE (no ADX)'
        baseline['threshold'] = None
        rows.append(baseline)
        col = GREEN if baseline['avg_pnl'] > 0 else RED
        print(f"    PnL: {col}{baseline['avg_pnl']:+.2f}%{NC}  WR: {baseline['avg_wr']:.1f}%  "
              f"Trades: {baseline['avg_trades']:.1f}  DD: {baseline['avg_dd']:.1f}%")

    # ADX filter variations
    for threshold in ADX_THRESHOLDS:
        print(f"  {CYAN}ADX filter ON, threshold={threshold}...{NC}")
        res = run_with_adx(configs, True, threshold, start_date, end_date,
                           start_capital, warmup_date, args.risk)
        if res:
            res['label']     = f"ADX>{threshold}"
            res['threshold'] = threshold
            rows.append(res)
            col = GREEN if res['avg_pnl'] > 0 else RED
            print(f"    PnL: {col}{res['avg_pnl']:+.2f}%{NC}  WR: {res['avg_wr']:.1f}%  "
                  f"Trades: {res['avg_trades']:.1f}  DD: {res['avg_dd']:.1f}%")

    # Summary table
    best = max(rows, key=lambda r: r['avg_pnl'], default=None)

    print(f"\n{CYAN}{'Setting':22}  {'Avg PnL%':>10}  {'Avg WR%':>9}  {'Avg Trades':>11}  {'Avg DD%':>9}  {'Status'}{NC}")
    print('-' * 80)
    for r in rows:
        col  = GREEN if r['avg_pnl'] > 0 else RED
        mark = f"  {GREEN}<-- BEST{NC}" if best and r['label'] == best['label'] else ""
        print(f"{r['label']:22}  {col}{r['avg_pnl']:>+9.2f}%{NC}  {r['avg_wr']:>8.1f}%  "
              f"{r['avg_trades']:>10.1f}  {r['avg_dd']:>8.1f}%{mark}")

    if best:
        print(f"\n{GREEN}Empfehlung: {best['label']}{NC}")
        print(f"  Avg PnL: {best['avg_pnl']:+.2f}%  |  Avg WR: {best['avg_wr']:.1f}%  |  Avg DD: {best['avg_dd']:.1f}%")
        if baseline and best['label'] != baseline['label']:
            diff = best['avg_pnl'] - baseline['avg_pnl']
            col  = GREEN if diff > 0 else RED
            print(f"  Verbesserung vs. Baseline: {col}{diff:+.2f}%{NC}")

    if not args.no_telegram and rows:
        try:
            import matplotlib
            import matplotlib.pyplot as plt
            import numpy as np

            labels     = [r['label']      for r in rows]
            avg_pnls   = [r['avg_pnl']    for r in rows]
            avg_wrs    = [r['avg_wr']     for r in rows]
            avg_trades = [r['avg_trades'] for r in rows]

            fig, axes = plt.subplots(3, 1, figsize=(10, 9))
        style_fig(fig)
            colors = ['gold' if r['label'] == 'BASELINE (no ADX)' else
                      ('green' if r['avg_pnl'] > 0 else 'red') for r in rows]

            axes[0].bar(labels, avg_pnls, color=colors, alpha=0.8)
            axes[0].axhline(0, color='black', linewidth=0.8)
            axes[0].set_ylabel('Avg PnL%')
            axes[0].set_title('ADX-Filter: Avg PnL%')

            axes[1].bar(labels, avg_wrs, color='steelblue', alpha=0.8)
            axes[1].axhline(50, color='orange', linewidth=1, linestyle='--')
            axes[1].set_ylabel('Avg Win-Rate%')
            axes[1].set_title('ADX-Filter: Avg Win-Rate%')

            axes[2].bar(labels, avg_trades, color='purple', alpha=0.7)
            axes[2].set_ylabel('Avg Trades/Config')
            axes[2].set_title('ADX-Filter: Anzahl Trades (Filterstrenge)')

            plt.tight_layout()
            caption = (f"ADX-Filter Analyse | Best: {best['label'] if best else 'N/A'} | "
                       + (f"PnL {best['avg_pnl']:+.1f}%" if best else ""))
            save_send(fig, caption)
            plt.close(fig)
        except Exception as e:
            print(f"{YELLOW}Chart-Fehler: {e}{NC}")


if __name__ == '__main__':
    main()
