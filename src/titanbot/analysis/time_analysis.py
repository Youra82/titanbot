# src/titanbot/analysis/time_analysis.py
"""Time-of-day and day-of-week analysis for titanbot trades.

Classifies trade entry times by UTC session:
  ASIA:   0-8h
  EUROPE: 8-16h
  US:     16-24h

Also by weekday (0=Mon, 6=Sun).
Shows win-rate per session and per weekday.
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
    run_backtest_for_config, send_chart_telegram,
)

SESSIONS = {
    'ASIA':   (0,  8),
    'EUROPE': (8,  16),
    'US':     (16, 24),
}

WEEKDAY_NAMES = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']


def classify_session(hour):
    for name, (h_start, h_end) in SESSIONS.items():
        if h_start <= hour < h_end:
            return name
    return 'US'


def extract_entry_time(trade):
    """Extract entry_time as pandas Timestamp from trade record."""
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
    """Determine if trade was a win from entry/exit price."""
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
    parser = argparse.ArgumentParser(description='Zeit-Analyse (Session + Wochentag) für titanbot Trades')
    parser.add_argument('--no-telegram', action='store_true', help='Kein Telegram-Report')
    args = parser.parse_args()

    settings = get_settings()
    start_capital = settings.get('optimization_settings', {}).get('start_capital', 20)
    start_date, end_date, warmup_date = get_date_range()

    configs = load_all_configs()
    if not configs:
        print(f"{RED}Keine Configs gefunden.{NC}")
        sys.exit(1)

    print(f"\n{CYAN}=== Zeit-Analyse (Session + Wochentag) ==={NC}")
    print(f"  Kapital: {start_capital} USDT | {len(configs)} Configs\n")

    session_stats = {s: {'wins': 0, 'total': 0} for s in SESSIONS}
    weekday_stats = {d: {'wins': 0, 'total': 0} for d in range(7)}

    for cfg in configs:
        ret = run_backtest_for_config(cfg, start_date, end_date, start_capital, warmup_date)
        if ret is None:
            continue
        result, label = ret

        for trade in result.get('trades_list', []):
            ts = extract_entry_time(trade)
            if ts is None:
                continue
            win = trade_is_win(trade)
            if win is None:
                continue

            hour    = ts.hour
            weekday = ts.dayofweek
            session = classify_session(hour)

            session_stats[session]['total'] += 1
            weekday_stats[weekday]['total'] += 1
            if win:
                session_stats[session]['wins'] += 1
                weekday_stats[weekday]['wins'] += 1

    total_trades = sum(s['total'] for s in session_stats.values())
    print(f"  Gesamt Trades analysiert: {total_trades}\n")

    # Session table
    print(f"{CYAN}{'Session':12} {'UTC-Stunden':14} {'Trades':>8} {'Wins':>6} {'Win-Rate%':>12}{NC}")
    print('-' * 55)
    for session, (h_start, h_end) in SESSIONS.items():
        st    = session_stats[session]
        total = st['total']
        wins  = st['wins']
        if total == 0:
            print(f"{session:12} {f'{h_start}-{h_end}h':14} {'0':>8}")
            continue
        wr = wins / total * 100
        col = GREEN if wr >= 50 else (YELLOW if wr >= 40 else RED)
        print(f"{session:12} {f'{h_start}-{h_end}h':14} {total:>8} {wins:>6} {col}{wr:>11.1f}%{NC}")

    # Weekday table
    print(f"\n{CYAN}{'Wochentag':12} {'Trades':>8} {'Wins':>6} {'Win-Rate%':>12}{NC}")
    print('-' * 45)
    for wd in range(7):
        st    = weekday_stats[wd]
        total = st['total']
        wins  = st['wins']
        name  = WEEKDAY_NAMES[wd]
        if total == 0:
            print(f"{name:12} {'0':>8}")
            continue
        wr = wins / total * 100
        col = GREEN if wr >= 50 else (YELLOW if wr >= 40 else RED)
        print(f"{name:12} {total:>8} {wins:>6} {col}{wr:>11.1f}%{NC}")

    if not args.no_telegram:
        try:
            import matplotlib
            matplotlib.use('Agg')
            import matplotlib.pyplot as plt

            fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 5))

            # Session chart
            sessions = list(SESSIONS.keys())
            ses_wrs  = [session_stats[s]['wins'] / max(session_stats[s]['total'], 1) * 100
                        for s in sessions]
            ses_col  = ['green' if w >= 50 else 'red' for w in ses_wrs]
            ax1.bar(sessions, ses_wrs, color=ses_col)
            ax1.axhline(50, color='black', linestyle='--', linewidth=1)
            ax1.set_ylabel('Win-Rate%')
            ax1.set_title('Win-Rate per UTC-Session')
            ax1.set_ylim(0, 100)

            # Weekday chart
            wd_wrs = [weekday_stats[d]['wins'] / max(weekday_stats[d]['total'], 1) * 100
                      for d in range(7)]
            wd_col = ['green' if w >= 50 else 'red' for w in wd_wrs]
            ax2.bar(WEEKDAY_NAMES, wd_wrs, color=wd_col)
            ax2.axhline(50, color='black', linestyle='--', linewidth=1)
            ax2.set_ylabel('Win-Rate%')
            ax2.set_title('Win-Rate per Wochentag')
            ax2.set_ylim(0, 100)

            fig.suptitle(f'Zeit-Analyse | {total_trades} Trades', fontweight='bold')
            plt.tight_layout()
            caption = f"Zeit-Analyse | {total_trades} Trades | Sessions + Wochentage"
            send_chart_telegram(fig, caption)
            plt.close(fig)
        except Exception as e:
            print(f"{YELLOW}Chart-Fehler: {e}{NC}")


if __name__ == '__main__':
    main()
