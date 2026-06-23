# src/titanbot/analysis/walk_forward_test.py
"""Walk-forward test: evaluates different backtest_lookback_weeks values.

Rolling approach: for each lookback N, divides the OOS period into non-overlapping
N-week windows and averages results across all windows and configs.
This reflects true historical average performance instead of just the last N weeks.

Tests lookback values [1, 2, 3, 4, 6, 8] weeks.
Recommends optimal lookback and writes it to settings.json.
"""

import os
import sys
import json
import argparse
from datetime import datetime, timezone, timedelta

try:
    from tqdm import tqdm
except ImportError:
    def tqdm(it, **kw): return it

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
sys.path.insert(0, os.path.join(PROJECT_ROOT, 'src'))

from titanbot.analysis.analysis_utils import (
    GREEN, YELLOW, RED, CYAN, NC,
    get_settings, get_date_range, load_all_configs,
    run_backtest_for_config, save_send, style_fig, style_axes,
    BG_DARK,
)


LOOKBACK_VALUES = [1, 2, 3, 4, 6, 8]

_TF_LOOKBACK_DAYS = {
    '5m': 60, '15m': 60, '30m': 365, '1h': 365,
    '2h': 730, '4h': 730, '6h': 730, '1d': 1095,
}


def _get_oos_bounds(cfg, oos_ref_str):
    """Return (oos_start, oos_end) datetime for a config based on its timeframe."""
    tf = cfg.get('market', {}).get('timeframe', '1h')
    lookback_days = _TF_LOOKBACK_DAYS.get(tf, 730)
    oos_days = lookback_days * 30 // 100
    oos_end   = datetime.strptime(oos_ref_str, '%Y-%m-%d').replace(tzinfo=timezone.utc)
    oos_start = oos_end - timedelta(days=oos_days)
    return oos_start, oos_end


def _rolling_windows(oos_start, oos_end, lookback_weeks, warmup_weeks, max_windows=6):
    """Return list of (start_date, end_date, warmup_date) strings.

    Non-overlapping windows of lookback_weeks within [oos_start, oos_end],
    each preceded by a warmup_weeks training period.
    Capped at max_windows to keep runtime manageable.
    """
    step   = timedelta(weeks=lookback_weeks)
    warmup = timedelta(weeks=warmup_weeks)
    windows = []
    w_end = oos_end
    while w_end - step >= oos_start:
        w_start = w_end - step
        windows.append((
            (w_start - warmup).strftime('%Y-%m-%d'),
            w_end.strftime('%Y-%m-%d'),
            w_start.strftime('%Y-%m-%d'),
        ))
        w_end -= step
    windows = list(reversed(windows))  # chronological order
    # Evenly sample if more than max_windows available
    if len(windows) > max_windows:
        step_f = (len(windows) - 1) / (max_windows - 1)
        windows = [windows[round(i * step_f)] for i in range(max_windows)]
    return windows


def main():
    parser = argparse.ArgumentParser(description='Walk-Forward Test: verschiedene Lookback-Wochen testen')
    parser.add_argument('--capital',      type=float, default=None, help='Start-Kapital in USDT')
    parser.add_argument('--min-trades',   type=int,   default=1,    help='Min. Trades pro Fenster')
    parser.add_argument('--max-windows',  type=int,   default=6,    help='Max. Fenster pro Lookback (Speed vs. Genauigkeit)')
    parser.add_argument('--no-telegram',  action='store_true',      help='Kein Telegram-Report')
    parser.add_argument('--no-save',      action='store_true',      help='Empfehlung NICHT in settings.json schreiben')
    args = parser.parse_args()

    settings       = get_settings()
    opt            = settings.get('optimization_settings', {})
    start_capital  = args.capital or opt.get('start_capital', 20)
    warmup_weeks   = opt.get('warmup_weeks', 4)
    oos_ref_str    = opt.get('oos_reference_date')

    configs = load_all_configs()
    if not configs:
        print(f"{RED}Keine Configs gefunden.{NC}")
        sys.exit(1)

    if oos_ref_str:
        print(f"\n{CYAN}=== Walk-Forward Test (Rolling OOS) ==={NC}")
        print(f"  {len(configs)} Configs | Kapital: {start_capital} USDT")
        print(f"  OOS-Referenz: {oos_ref_str} | Warmup: {warmup_weeks}W | Max Fenster: {args.max_windows}")
        print(f"  Methode: Rollende Fenster im OOS-Zeitraum (wie dnabot)\n")
    else:
        print(f"\n{YELLOW}=== Walk-Forward Test (Fallback: letzte N Wochen) ==={NC}")
        print(f"  Kein oos_reference_date in settings.json — verwende altes Verhalten.\n")

    rows = []
    for lw in LOOKBACK_VALUES:
        all_pnls, all_wrs, all_dds, all_trades, window_count = [], [], [], [], 0

        bar = tqdm(configs, desc=f"  {lw}w", unit="cfg", leave=False,
                   bar_format="{desc}: {n_fmt}/{total_fmt} [{bar:25}] {elapsed}")

        for cfg in bar:
            sym = cfg.get('market', {}).get('symbol', '?')
            tf  = cfg.get('market', {}).get('timeframe', '?')
            bar.set_postfix_str(f"{sym} {tf}", refresh=True)

            if oos_ref_str:
                oos_start, oos_end = _get_oos_bounds(cfg, oos_ref_str)
                windows = _rolling_windows(oos_start, oos_end, lw, warmup_weeks, args.max_windows)
            else:
                start_date, end_date, warmup_date = get_date_range(lookback_weeks=lw)
                windows = [(start_date, end_date, warmup_date)]

            for (start_date, end_date, warmup_date) in windows:
                ret = run_backtest_for_config(cfg, start_date, end_date,
                                              start_capital, warmup_date, silent=True)
                if ret is None:
                    continue
                result, _ = ret
                if result.get('trades_count', 0) < args.min_trades:
                    continue
                all_pnls.append(result.get('total_pnl_pct', 0))
                all_wrs.append(result.get('win_rate', 0))
                all_dds.append(result.get('max_drawdown_pct', 0) * 100)
                all_trades.append(result.get('trades_count', 0))
                window_count += 1

        if not all_pnls:
            print(f"  {YELLOW}Keine Ergebnisse für {lw}W.{NC}")
            rows.append({'lw': lw, 'avg_pnl': None, 'avg_wr': None,
                         'avg_dd': None, 'configs': 0, 'windows': 0})
            continue

        avg_pnl = sum(all_pnls) / len(all_pnls)
        avg_wr  = sum(all_wrs)  / len(all_wrs)
        avg_dd  = sum(all_dds)  / len(all_dds)
        avg_tc  = sum(all_trades) / len(all_trades)
        rows.append({'lw': lw, 'avg_pnl': avg_pnl, 'avg_wr': avg_wr, 'avg_dd': avg_dd,
                     'avg_trades': avg_tc, 'configs': len(configs), 'windows': window_count})
        colour = GREEN if avg_pnl > 0 else RED
        print(f"  {lw}W — PnL: {colour}{avg_pnl:+.2f}%{NC}  "
              f"WR: {avg_wr:.1f}%  DD: {avg_dd:.1f}%  "
              f"Trades/Fenster: {avg_tc:.1f}  ({window_count} Fenster)")

    # Summary
    print(f"\n{CYAN}{'='*70}{NC}")
    print(f"{'Lookback':>10}{'Fenster':>10}{'Avg PnL%':>12}{'Avg WR%':>10}{'Avg DD%':>10}{'Avg Trades':>12}")
    print(f"{'-'*70}")
    best_row   = None
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
        print(f"{r['lw']:>8}w {r['windows']:>10} {colour}{r['avg_pnl']:>+11.2f}%{NC} "
              f"{r['avg_wr']:>9.1f}% {r['avg_dd']:>9.1f}% {r['avg_trades']:>11.1f}")

    print(f"{CYAN}{'='*70}{NC}")

    if best_row:
        print(f"\n{GREEN}Empfehlung: backtest_lookback_weeks = {best_row['lw']}{NC}")
        print(f"  Score (PnL - DD + WR×0.1): {best_score:.2f}")
        print(f"  Avg PnL: {best_row['avg_pnl']:+.2f}%  |  Avg WR: {best_row['avg_wr']:.1f}%  |  Avg DD: {best_row['avg_dd']:.1f}%")

        if not args.no_save:
            try:
                s = get_settings()
                s.setdefault('optimization_settings', {})['backtest_lookback_weeks'] = best_row['lw']
                settings_path = os.path.join(PROJECT_ROOT, 'settings.json')
                with open(settings_path, 'w', encoding='utf-8') as f:
                    json.dump(s, f, indent=4)
                print(f"{GREEN}✔ backtest_lookback_weeks = {best_row['lw']} in settings.json gespeichert.{NC}")
            except Exception as e:
                print(f"{YELLOW}WARN: settings.json konnte nicht aktualisiert werden: {e}{NC}")

    # Chart
    if best_row:
        try:
            import matplotlib.pyplot as plt

            valid  = [r for r in rows if r['avg_pnl'] is not None]
            lws    = [r['lw']      for r in valid]
            pnls   = [r['avg_pnl'] for r in valid]
            scores = [r['avg_pnl'] - r['avg_dd'] + r['avg_wr'] * 0.1 for r in valid]
            best_lw = best_row['lw']

            fig, axes = plt.subplots(2, 1, figsize=(10, 8))
            style_fig(fig)
            fig.suptitle('titanbot Walk-Forward — Lookback-Vergleich',
                         fontsize=13, fontweight='bold', color='white')

            bar_colors = ['#22c55e' if p > 0 else '#ef4444' for p in pnls]
            axes[0].bar([f"{lw}W" for lw in lws], pnls, color=bar_colors)
            axes[0].axhline(0, color='#94a3b8', linewidth=0.8)
            axes[0].set_ylabel('Avg PnL %')
            axes[0].set_title('Durchschnittlicher PnL pro Lookback (Rolling OOS)')
            for i, (lw, p) in enumerate(zip(lws, pnls)):
                clr = '#22c55e' if p > 0 else '#ef4444'
                axes[0].text(i, p + (0.05 if p >= 0 else -0.15),
                             f"{p:+.1f}%", ha='center', va='bottom', color=clr, fontsize=9)
            if best_lw in lws:
                idx = lws.index(best_lw)
                axes[0].get_children()[idx].set_edgecolor('white')
                axes[0].get_children()[idx].set_linewidth(2)

            score_colors = ['#f59e0b' if lw == best_lw else '#6366f1' for lw in lws]
            axes[1].bar([f"{lw}W" for lw in lws], scores, color=score_colors)
            axes[1].set_ylabel('Score (PnL − DD + WR×0.1)')
            axes[1].set_title(f'Gesamtscore pro Lookback  (★ BEST = {best_lw}W)')
            for i, (lw, s) in enumerate(zip(lws, scores)):
                axes[1].text(i, s + 0.02, f"{s:.1f}", ha='center', va='bottom',
                             color='white', fontsize=9)

            style_axes(*axes)
            plt.tight_layout(rect=[0, 0, 1, 0.95])

            caption = (f"titanbot Walk-Forward Rolling | Empfehlung: {best_lw}W | "
                       f"Avg PnL {best_row['avg_pnl']:+.1f}% | "
                       f"WR {best_row['avg_wr']:.1f}% | DD {best_row['avg_dd']:.1f}%")
            save_send(fig, 'walk_forward', caption=caption, no_telegram=args.no_telegram)
        except Exception as e:
            print(f"{YELLOW}Chart-Fehler: {e}{NC}")


if __name__ == '__main__':
    main()
