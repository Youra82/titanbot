# src/titanbot/analysis/entry_timing.py
"""Entry timing analysis — detailed hourly breakdown.

More detailed than time_analysis.py:
- Hourly breakdown (0-23h UTC) of win-rate and trade count
- Weekday breakdown (Mon-Sun)
- Heatmap (hour x weekday)
"""

import os
import sys
import argparse
try:
    from tqdm import tqdm
except ImportError:
    def tqdm(it, **kw): return it


PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
sys.path.insert(0, os.path.join(PROJECT_ROOT, 'src'))

from titanbot.analysis.analysis_utils import (
    GREEN, YELLOW, RED, CYAN, NC,
    get_settings, get_date_range, load_all_configs,
    run_backtest_for_config, save_send,
)

WEEKDAY_NAMES = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']


def extract_entry_time(trade):
    import pandas as pd
    et = trade.get('entry_time')
    if et is None:
        return None
    if isinstance(et, str):
        try:
            return pd.Timestamp(et, tz='UTC')
        except Exception:
            return None
    try:
        return pd.Timestamp(et)
    except Exception:
        return None


def trade_is_win(trade):
    side_key = 'entry_long' if 'entry_long' in trade else 'entry_short'
    exit_key = 'exit_long'  if 'exit_long'  in trade else 'exit_short'
    try:
        ep = float(trade.get(side_key, {}).get('price', 0))
        xp = float(trade.get(exit_key, {}).get('price', 0))
        if ep <= 0 or xp <= 0:
            return None
        if 'long' in side_key:
            return xp > ep
        else:
            return xp < ep
    except Exception:
        return None


def main():
    parser = argparse.ArgumentParser(description='Entry-Timing Analyse (Stunden + Heatmap)')
    parser.add_argument('--no-telegram', action='store_true', help='Kein Telegram-Report')
    args = parser.parse_args()

    settings = get_settings()
    start_capital = settings.get('optimization_settings', {}).get('start_capital', 20)
    start_date, end_date, warmup_date = get_date_range()

    configs = load_all_configs()
    if not configs:
        print(f"{RED}Keine Configs gefunden.{NC}")
        sys.exit(1)

    print(f"\n{CYAN}=== Entry-Timing Analyse (Stunden + Heatmap) ==={NC}")
    print(f"  Kapital: {start_capital} USDT | {len(configs)} Configs\n")

    # hour -> {wins, total}
    hour_stats = {h: {'wins': 0, 'total': 0} for h in range(24)}
    # weekday x hour grid
    grid_stats = {wd: {h: {'wins': 0, 'total': 0} for h in range(24)} for wd in range(7)}
    weekday_stats = {d: {'wins': 0, 'total': 0} for d in range(7)}

    for cfg in configs:
        ret = run_backtest_for_config(cfg, start_date, end_date, start_capital, warmup_date)
        if ret is None:
            continue
        result, _ = ret

        for trade in result.get('trades_list', []):
            ts = extract_entry_time(trade)
            if ts is None:
                continue
            win = trade_is_win(trade)
            if win is None:
                continue

            hour    = ts.hour
            weekday = ts.dayofweek

            hour_stats[hour]['total'] += 1
            weekday_stats[weekday]['total'] += 1
            grid_stats[weekday][hour]['total'] += 1
            if win:
                hour_stats[hour]['wins'] += 1
                weekday_stats[weekday]['wins'] += 1
                grid_stats[weekday][hour]['wins'] += 1

    total_trades = sum(s['total'] for s in hour_stats.values())
    print(f"  Gesamt analysierte Trades: {total_trades}\n")

    if total_trades == 0:
        print(f"{YELLOW}Keine Trades gefunden.{NC}")
        sys.exit(0)

    # Hourly breakdown
    print(f"{CYAN}Stündliche Auswertung (UTC):{NC}")
    print(f"{'Stunde':>8} {'Trades':>8} {'Win-Rate%':>12} {'Bar'}")
    print('-' * 50)
    for h in range(24):
        st    = hour_stats[h]
        total = st['total']
        wins  = st['wins']
        if total == 0:
            print(f"{h:>6}h   {'0':>8}")
            continue
        wr  = wins / total * 100
        col = GREEN if wr >= 55 else (YELLOW if wr >= 40 else RED)
        bar = '#' * min(30, int(wr / 5))
        print(f"{h:>6}h   {total:>8} {col}{wr:>11.1f}%{NC}  {col}{bar}{NC}")

    # Best and worst hours
    active_hours = [(h, hour_stats[h]) for h in range(24) if hour_stats[h]['total'] >= 3]
    if active_hours:
        best_hour  = max(active_hours, key=lambda x: x[1]['wins'] / max(x[1]['total'], 1))
        worst_hour = min(active_hours, key=lambda x: x[1]['wins'] / max(x[1]['total'], 1))
        best_wr  = best_hour[1]['wins']  / best_hour[1]['total']  * 100
        worst_wr = worst_hour[1]['wins'] / worst_hour[1]['total'] * 100
        print(f"\n  {GREEN}Beste Stunde:  {best_hour[0]}:00 UTC  ({best_wr:.1f}% WR, {best_hour[1]['total']} Trades){NC}")
        print(f"  {RED}Schlechtste:   {worst_hour[0]}:00 UTC  ({worst_wr:.1f}% WR, {worst_hour[1]['total']} Trades){NC}")

    # Weekday breakdown
    print(f"\n{CYAN}Wochentagsauswertung:{NC}")
    print(f"{'Tag':>8} {'Trades':>8} {'Win-Rate%':>12}")
    print('-' * 35)
    for wd in range(7):
        st    = weekday_stats[wd]
        total = st['total']
        wins  = st['wins']
        name  = WEEKDAY_NAMES[wd]
        if total == 0:
            print(f"{name:>8}  {'0':>8}")
            continue
        wr  = wins / total * 100
        col = GREEN if wr >= 55 else (YELLOW if wr >= 40 else RED)
        print(f"{name:>8}  {total:>8} {col}{wr:>11.1f}%{NC}")

    if not args.no_telegram:
        try:
            import matplotlib
            import matplotlib.pyplot as plt
            import numpy as np

            # Heatmap: weekday (rows) x hour (cols)
            matrix = np.zeros((7, 24))
            matrix_counts = np.zeros((7, 24))

            for wd in range(7):
                for h in range(24):
                    st = grid_stats[wd][h]
                    if st['total'] > 0:
                        matrix[wd][h] = st['wins'] / st['total'] * 100
                        matrix_counts[wd][h] = st['total']
                    else:
                        matrix[wd][h] = float('nan')

            fig, axes = plt.subplots(1, 2, figsize=(18, 5))
        style_fig(fig)

            # Win-rate heatmap
            im1 = axes[0].imshow(matrix, cmap='RdYlGn', vmin=0, vmax=100,
                                  aspect='auto', interpolation='nearest')
            plt.colorbar(im1, ax=axes[0], label='Win-Rate%')
            axes[0].set_yticks(range(7))
            axes[0].set_yticklabels(WEEKDAY_NAMES)
            axes[0].set_xticks(range(0, 24, 2))
            axes[0].set_xticklabels([f"{h}h" for h in range(0, 24, 2)], fontsize=7)
            axes[0].set_xlabel('UTC Stunde')
            axes[0].set_title('Win-Rate% Heatmap (Wochentag × Stunde)')

            # Trade count heatmap
            # Replace NaN with 0 for count heatmap
            counts = np.nan_to_num(matrix_counts)
            im2 = axes[1].imshow(counts, cmap='Blues', aspect='auto', interpolation='nearest')
            plt.colorbar(im2, ax=axes[1], label='Anzahl Trades')
            axes[1].set_yticks(range(7))
            axes[1].set_yticklabels(WEEKDAY_NAMES)
            axes[1].set_xticks(range(0, 24, 2))
            axes[1].set_xticklabels([f"{h}h" for h in range(0, 24, 2)], fontsize=7)
            axes[1].set_xlabel('UTC Stunde')
            axes[1].set_title('Anzahl Trades Heatmap (Wochentag × Stunde)')

            fig.suptitle(f'Entry-Timing Analyse | {total_trades} Trades', fontweight='bold')
            plt.tight_layout()

            caption = (f"Entry-Timing Heatmap | {total_trades} Trades | "
                       + (f"Beste Stunde: {best_hour[0]}:00 UTC ({best_wr:.1f}%)" if active_hours else ""))
            save_send(fig, caption)
            plt.close(fig)
        except Exception as e:
            print(f"{YELLOW}Chart-Fehler: {e}{NC}")


if __name__ == '__main__':
    main()
