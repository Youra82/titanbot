# src/titanbot/analysis/multitf_analysis.py
"""Multi-timeframe filter analysis.

Tests use_mtf_filter=True vs use_mtf_filter=False.
Runs all configs with both settings.
Shows: trades count, win-rate, PnL, drawdown side by side.
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


def run_with_mtf(configs, use_mtf, start_date, end_date, start_capital, warmup_date):
    pnls, wrs, trades, dds = [], [], [], []
    for cfg in configs:
        new_cfg = copy.deepcopy(cfg)
        new_cfg.setdefault('strategy', {})['use_mtf_filter'] = use_mtf

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
        'all_pnls':   pnls,
        'all_wrs':    wrs,
    }


def main():
    parser = argparse.ArgumentParser(description='Multi-Timeframe Filter Analyse')
    parser.add_argument('--window-hours', type=int, default=None,
                        help='(Nicht genutzt, für API-Kompatibilität)')
    parser.add_argument('--no-telegram', action='store_true', help='Kein Telegram-Report')
    args = parser.parse_args()

    settings = get_settings()
    start_capital = settings.get('optimization_settings', {}).get('start_capital', 20)
    start_date, end_date, warmup_date = get_date_range()

    configs = load_all_configs()
    if not configs:
        print(f"{RED}Keine Configs gefunden.{NC}")
        sys.exit(1)

    print(f"\n{CYAN}=== Multi-Timeframe Filter Analyse ==={NC}")
    print(f"  Kapital: {start_capital} USDT | {len(configs)} Configs\n")

    # Run without MTF filter
    print(f"  {CYAN}use_mtf_filter=False (Baseline)...{NC}")
    no_mtf = run_with_mtf(configs, False, start_date, end_date, start_capital, warmup_date)

    # Run with MTF filter
    print(f"  {CYAN}use_mtf_filter=True...{NC}")
    with_mtf = run_with_mtf(configs, True, start_date, end_date, start_capital, warmup_date)

    if not no_mtf or not with_mtf:
        print(f"{RED}Unzureichende Daten.{NC}")
        sys.exit(1)

    # Comparison table
    print(f"\n{CYAN}{'Metrik':25} {'MTF=False':>15} {'MTF=True':>15} {'Diff':>12}{NC}")
    print('-' * 70)

    metrics = [
        ('Avg PnL%',     'avg_pnl',    '{:+.2f}%'),
        ('Avg Win-Rate%', 'avg_wr',    '{:.1f}%'),
        ('Avg Trades',   'avg_trades', '{:.1f}'),
        ('Avg Max DD%',  'avg_dd',     '{:.1f}%'),
        ('Configs',      'n',          '{:d}'),
    ]

    for name, key, fmt in metrics:
        v_no  = no_mtf[key]
        v_yes = with_mtf[key]
        diff  = v_yes - v_no

        col_no  = GREEN if (key == 'avg_pnl' and v_no  > 0) else (RED if (key == 'avg_pnl' and v_no  < 0) else NC)
        col_yes = GREEN if (key == 'avg_pnl' and v_yes > 0) else (RED if (key == 'avg_pnl' and v_yes < 0) else NC)
        col_diff = GREEN if diff > 0 else (RED if diff < 0 else NC)

        if key == 'avg_dd':
            col_diff = RED if diff > 0 else (GREEN if diff < 0 else NC)

        print(f"{name:25} {col_no}{fmt.format(v_no):>15}{NC} {col_yes}{fmt.format(v_yes):>15}{NC} "
              f"{col_diff}{diff:>+10.2f}{NC}")

    # Recommendation
    print(f"\n{CYAN}Empfehlung:{NC}")
    pnl_diff = with_mtf['avg_pnl'] - no_mtf['avg_pnl']
    dd_diff  = with_mtf['avg_dd']  - no_mtf['avg_dd']

    if pnl_diff > 0.5 and dd_diff < 5:
        print(f"  {GREEN}MTF-Filter aktivieren: +{pnl_diff:.2f}% PnL bei akzeptablem DD-Anstieg{NC}")
    elif pnl_diff < -0.5:
        print(f"  {RED}MTF-Filter deaktivieren: -{abs(pnl_diff):.2f}% PnL-Verlust durch MTF-Filter{NC}")
    else:
        print(f"  {YELLOW}Kein klarer Vorteil durch MTF-Filter (ΔPnL: {pnl_diff:+.2f}%){NC}")

    # Trade count ratio
    trade_ratio = with_mtf['avg_trades'] / max(no_mtf['avg_trades'], 0.01)
    print(f"  Trade-Reduktion durch MTF: {(1-trade_ratio)*100:.1f}%  "
          f"({no_mtf['avg_trades']:.1f} → {with_mtf['avg_trades']:.1f} Trades/Config)")

    if not args.no_telegram:
        try:
            import matplotlib
            import matplotlib.pyplot as plt
            import numpy as np

            categories = ['Avg PnL%', 'Avg WR%', 'Avg Trades', 'Avg DD%']
            vals_no  = [no_mtf['avg_pnl'],  no_mtf['avg_wr'],  no_mtf['avg_trades'],  no_mtf['avg_dd']]
            vals_yes = [with_mtf['avg_pnl'], with_mtf['avg_wr'], with_mtf['avg_trades'], with_mtf['avg_dd']]

            x = np.arange(len(categories))
            w = 0.35
            fig, ax = plt.subplots(figsize=(10, 5))
        style_fig(fig)
            ax.bar(x - w/2, vals_no,  w, label='MTF=False', color='steelblue', alpha=0.8)
            ax.bar(x + w/2, vals_yes, w, label='MTF=True',  color='orange',    alpha=0.8)
            ax.set_xticks(x)
            ax.set_xticklabels(categories)
            ax.axhline(0, color='black', linewidth=0.5)
            ax.set_title(f'Multi-Timeframe Filter Vergleich | {len(configs)} Configs')
            ax.legend()
            plt.tight_layout()

            caption = (f"MTF-Filter Analyse | False: {no_mtf['avg_pnl']:+.1f}% | "
                       f"True: {with_mtf['avg_pnl']:+.1f}% | ΔPnL: {pnl_diff:+.1f}%")
            save_send(fig, caption)
            plt.close(fig)
        except Exception as e:
            print(f"{YELLOW}Chart-Fehler: {e}{NC}")


if __name__ == '__main__':
    main()
