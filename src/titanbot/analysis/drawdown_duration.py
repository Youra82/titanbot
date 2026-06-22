# src/titanbot/analysis/drawdown_duration.py
"""Drawdown duration analysis.

Gets equity curve from all backtests.
Calculates drawdown periods: start, end, depth, duration in candles.
Shows: max_duration, avg_duration, max_depth, how long recovery takes.
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


def compute_drawdown_periods(equity_values):
    """Compute drawdown periods from list of equity values.

    Returns list of dicts: {start_idx, end_idx, depth_pct, duration, recovery_duration}
    """
    if not equity_values or len(equity_values) < 2:
        return []

    periods = []
    peak = equity_values[0]
    in_dd = False
    dd_start = 0
    peak_idx = 0

    for i, eq in enumerate(equity_values):
        if eq >= peak:
            if in_dd:
                # Recovered — end drawdown period
                depth = (peak - min(equity_values[dd_start:i])) / max(peak, 1e-9) * 100
                recovery = i - (dd_start + equity_values[dd_start:i].index(
                    min(equity_values[dd_start:i])))
                periods.append({
                    'start_idx':         dd_start,
                    'end_idx':           i,
                    'depth_pct':         depth,
                    'duration':          i - dd_start,
                    'recovery_duration': recovery,
                })
                in_dd = False
            peak = eq
            peak_idx = i
        else:
            if not in_dd:
                in_dd = True
                dd_start = peak_idx

    # If still in drawdown at end
    if in_dd:
        depth = (peak - min(equity_values[dd_start:])) / max(peak, 1e-9) * 100
        periods.append({
            'start_idx':         dd_start,
            'end_idx':           len(equity_values) - 1,
            'depth_pct':         depth,
            'duration':          len(equity_values) - 1 - dd_start,
            'recovery_duration': None,  # Not yet recovered
        })

    return periods


def main():
    parser = argparse.ArgumentParser(description='Drawdown-Duration Analyse')
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

    if args.risk:
        for cfg in tqdm(configs, desc="  Configs", unit="cfg", leave=False,
                        bar_format="{desc}: {n_fmt}/{total_fmt} [{bar:25}] {elapsed}"):
            cfg.setdefault('risk', {})['risk_per_trade_pct'] = args.risk

    print(f"\n{CYAN}=== Drawdown-Duration Analyse ==={NC}")
    print(f"  Kapital: {start_capital} USDT | {len(configs)} Configs\n")

    all_periods = []
    all_max_depths = []
    all_max_durations = []
    config_rows = []

    for cfg in configs:
        ret = run_backtest_for_config(cfg, start_date, end_date, start_capital, warmup_date)
        if ret is None:
            continue
        result, label = ret

        eq_curve = result.get('equity_curve', [])
        if not eq_curve:
            continue

        equities = [e['equity'] if isinstance(e, dict) else float(e) for e in eq_curve]
        periods  = compute_drawdown_periods(equities)

        if not periods:
            config_rows.append({'label': label, 'max_dur': 0, 'avg_dur': 0,
                                 'max_depth': 0, 'avg_rec': None, 'n_periods': 0})
            continue

        depths    = [p['depth_pct']  for p in periods]
        durations = [p['duration']   for p in periods]
        recoveries = [p['recovery_duration'] for p in periods if p['recovery_duration'] is not None]

        max_depth = max(depths)
        max_dur   = max(durations)
        avg_dur   = sum(durations) / len(durations)
        avg_rec   = sum(recoveries) / len(recoveries) if recoveries else None

        all_periods.extend(periods)
        all_max_depths.append(max_depth)
        all_max_durations.append(max_dur)

        config_rows.append({
            'label':     label,
            'max_dur':   max_dur,
            'avg_dur':   avg_dur,
            'max_depth': max_depth,
            'avg_rec':   avg_rec,
            'n_periods': len(periods),
        })

    if not config_rows:
        print(f"{YELLOW}Keine Equity-Kurven verfügbar.{NC}")
        sys.exit(0)

    # Sort by worst drawdown duration
    config_rows.sort(key=lambda r: r['max_dur'], reverse=True)

    print(f"{'Config':40} {'DD-Periods':>11} {'Max Dur':>9} {'Avg Dur':>9} {'Max Depth%':>12} {'Avg Rec':>9}")
    print('-' * 96)
    for r in config_rows:
        if r['n_periods'] == 0:
            print(f"{r['label']:40}  {'0':>11}")
            continue
        depth_col = RED if r['max_depth'] > 20 else (YELLOW if r['max_depth'] > 10 else GREEN)
        dur_col   = RED if r['max_dur'] > 50   else (YELLOW if r['max_dur'] > 20   else GREEN)
        rec_str   = f"{r['avg_rec']:.1f}" if r['avg_rec'] else 'N/A'
        print(f"{r['label']:40} {r['n_periods']:>11} {dur_col}{r['max_dur']:>8}c{NC} "
              f"{r['avg_dur']:>8.1f}c {depth_col}{r['max_depth']:>10.1f}%{NC} {rec_str:>9}")

    print('-' * 96)
    if all_max_depths:
        print(f"\n{CYAN}Portfolio-Zusammenfassung:{NC}")
        print(f"  Avg Max DD-Depth:    {sum(all_max_depths) / len(all_max_depths):.1f}%")
        print(f"  Worst Max DD-Depth:  {max(all_max_depths):.1f}%")
        if all_max_durations:
            print(f"  Avg Max DD-Duration: {sum(all_max_durations) / len(all_max_durations):.1f} Candles")
            print(f"  Worst DD-Duration:   {max(all_max_durations)} Candles")

        long_dd = [r for r in config_rows if r['max_dur'] > 30]
        if long_dd:
            print(f"\n  {YELLOW}Configs mit langen Drawdown-Phasen (>30 Candles):{NC}")
            for r in long_dd[:5]:
                print(f"    {r['label']}: {r['max_dur']} Candles, Tiefe {r['max_depth']:.1f}%")

    if not args.no_telegram and all_max_depths:
        try:
            import matplotlib
            import matplotlib.pyplot as plt
            import numpy as np

            labels     = [r['label']     for r in config_rows[:20]]  # top 20
            max_durs   = [r['max_dur']   for r in config_rows[:20]]
            max_depths = [r['max_depth'] for r in config_rows[:20]]

            fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 9))

            dur_colors = ['red' if d > 50 else ('orange' if d > 20 else 'green') for d in max_durs]
            ax1.barh(range(len(labels)), max_durs, color=dur_colors, alpha=0.8)
            ax1.set_yticks(range(len(labels)))
            ax1.set_yticklabels(labels, fontsize=7)
            ax1.set_xlabel('Max Drawdown Duration (Candles)')
            ax1.set_title('Max Drawdown-Dauer (Candles) — Top 20 Configs')
            ax1.axvline(30, color='black', linestyle='--', linewidth=0.8, label='30 Candles')
            ax1.legend()
            ax1.invert_yaxis()

            dep_colors = ['red' if d > 20 else ('orange' if d > 10 else 'green') for d in max_depths]
            ax2.barh(range(len(labels)), max_depths, color=dep_colors, alpha=0.8)
            ax2.set_yticks(range(len(labels)))
            ax2.set_yticklabels(labels, fontsize=7)
            ax2.set_xlabel('Max Drawdown Tiefe (%)')
            ax2.set_title('Max Drawdown-Tiefe (%) — Top 20 Configs')
            ax2.axvline(20, color='black', linestyle='--', linewidth=0.8, label='20%')
            ax2.legend()
            ax2.invert_yaxis()

            plt.tight_layout()
            caption = (f"Drawdown-Duration | Avg Max Dur: {sum(all_max_durations)/len(all_max_durations):.1f}c | "
                       f"Avg Max Depth: {sum(all_max_depths)/len(all_max_depths):.1f}%")
            save_send(fig, caption)
            plt.close(fig)
        except Exception as e:
            print(f"{YELLOW}Chart-Fehler: {e}{NC}")


if __name__ == '__main__':
    main()
