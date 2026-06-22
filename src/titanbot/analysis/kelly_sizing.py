# src/titanbot/analysis/kelly_sizing.py
"""Kelly criterion position sizing analysis.

For each config: extracts win_rate and avg_win/avg_loss from trades.
Full Kelly  = W - (1-W)/R  where W=win_rate, R=avg_win/avg_loss
Half Kelly  = Full Kelly / 2

Compares to current risk_per_trade_pct in config.
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


def compute_kelly(win_rate_frac, avg_win, avg_loss):
    """Compute full Kelly fraction.

    Kelly = W - (1-W)/R where R = avg_win / avg_loss
    Returns full kelly as a fraction (e.g., 0.25 = 25%).
    """
    if avg_loss <= 0 or avg_win <= 0:
        return 0.0
    R = avg_win / avg_loss
    kelly = win_rate_frac - (1 - win_rate_frac) / R
    return kelly


def extract_avg_win_loss(result, start_capital):
    """Extract approximate avg win and avg loss in USD from backtest result."""
    trades_count = result.get('trades_count', 0)
    win_rate     = result.get('win_rate', 0.0)
    eq_curve     = result.get('equity_curve', [])
    rr           = result.get('_rr', 2.0)

    if trades_count == 0 or not eq_curve:
        return None, None

    wins   = max(1, round(trades_count * win_rate / 100))
    losses = trades_count - wins

    end_eq = eq_curve[-1]['equity'] if isinstance(eq_curve[-1], dict) else float(eq_curve[-1])
    total_pnl_usd = end_eq - start_capital

    if losses == 0 or wins == 0:
        avg_unit = abs(total_pnl_usd) / max(wins, 1)
        return avg_unit * rr, avg_unit

    # total_pnl = wins*W - losses*L,  R = W/L => W = R*L
    # total_pnl = wins*R*L - losses*L => L = total_pnl / (wins*R - losses)
    denom = wins * rr - losses
    if abs(denom) < 1e-6:
        return None, None
    avg_loss = total_pnl_usd / denom
    avg_win  = avg_loss * rr

    if avg_loss < 0:
        avg_loss = -avg_loss
        avg_win  = -avg_win

    if avg_win <= 0 or avg_loss <= 0:
        return None, None

    return avg_win, avg_loss


def main():
    parser = argparse.ArgumentParser(description='Kelly-Criterion Positionsgrößen-Analyse')
    parser.add_argument('--capital',    type=float, default=None,  help='Start-Kapital in USDT')
    parser.add_argument('--risk',       type=float, default=None,  help='Risiko pro Trade % (override)')
    parser.add_argument('--half-kelly', action='store_true',       help='Half-Kelly als Empfehlung verwenden')
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
        for cfg in configs:
            cfg.setdefault('risk', {})['risk_per_trade_pct'] = args.risk

    kelly_type = "Half-Kelly" if args.half_kelly else "Full-Kelly"
    print(f"\n{CYAN}=== Kelly-Criterion Analyse ({kelly_type}) ==={NC}")
    print(f"  Kapital: {start_capital} USDT | {len(configs)} Configs\n")

    rows = []
    for cfg in configs:
        ret = run_backtest_for_config(cfg, start_date, end_date, start_capital, warmup_date)
        if ret is None:
            continue
        result, label = ret

        current_risk = cfg.get('risk', {}).get('risk_per_trade_pct', 1.0)
        rr           = cfg.get('risk', {}).get('risk_reward_ratio', 2.0)
        result['_rr'] = rr

        win_rate  = result.get('win_rate', 0) / 100
        avg_win, avg_loss = extract_avg_win_loss(result, start_capital)

        if avg_win is None or avg_loss is None:
            continue

        kelly_full = compute_kelly(win_rate, avg_win, avg_loss) * 100  # as pct
        kelly_half = kelly_full / 2

        rec_kelly = kelly_half if args.half_kelly else kelly_full
        rows.append({
            'label': label,
            'current': current_risk,
            'kelly_full': kelly_full,
            'kelly_half': kelly_half,
            'rec': rec_kelly,
            'wr': win_rate * 100,
            'rr': rr,
            'pnl': result.get('total_pnl_pct', 0),
        })

    if not rows:
        print(f"{YELLOW}Keine Ergebnisse.{NC}")
        sys.exit(0)

    rows.sort(key=lambda r: r['rec'], reverse=True)

    print(f"{'Config':40} {'Curr%':>7} {'Full-K%':>9} {'Half-K%':>9} {'Empf%':>8} {'WR%':>6} {'RR':>5} {'PnL%':>8}")
    print('-' * 100)
    for r in rows:
        diff = r['rec'] - r['current']
        if diff > 0.5:
            rec_col = GREEN
            rec_txt = f"↑ {r['rec']:+.2f}%"
        elif diff < -0.5:
            rec_col = YELLOW
            rec_txt = f"↓ {r['rec']:+.2f}%"
        else:
            rec_col = NC
            rec_txt = f"  {r['rec']:+.2f}%"

        pnl_col = GREEN if r['pnl'] > 0 else RED
        print(f"{r['label']:40} {r['current']:>6.2f}% {r['kelly_full']:>8.2f}% {r['kelly_half']:>8.2f}% "
              f"{rec_col}{rec_txt:>8}{NC} {r['wr']:>5.1f}% {r['rr']:>4.1f}  "
              f"{pnl_col}{r['pnl']:>+7.1f}%{NC}")

    print('-' * 100)
    avg_current = sum(r['current'] for r in rows) / len(rows)
    avg_kelly   = sum(r['rec'] for r in rows) / len(rows)
    print(f"\n  Avg aktuelles Risiko: {avg_current:.2f}%")
    print(f"  Avg {kelly_type}:      {avg_kelly:.2f}%")
    if avg_kelly > avg_current * 1.5:
        print(f"  {GREEN}Configs könnten mit mehr Risiko handeln laut Kelly{NC}")
    elif avg_kelly < avg_current * 0.5:
        print(f"  {RED}Warnung: Kelly empfiehlt deutlich weniger Risiko!{NC}")

    if not args.no_telegram:
        try:
            import matplotlib
            matplotlib.use('Agg')
            import matplotlib.pyplot as plt
            import numpy as np

            labels   = [r['label'] for r in rows]
            currents = [r['current']    for r in rows]
            kellys   = [r['rec']        for r in rows]

            fig, ax = plt.subplots(figsize=(max(8, len(rows) * 0.5), 6))
            x = np.arange(len(rows))
            w = 0.35
            ax.bar(x - w/2, currents, w, label='Aktuell%', color='steelblue', alpha=0.8)
            ax.bar(x + w/2, kellys,   w, label=f'{kelly_type}%', color='orange', alpha=0.8)
            ax.axhline(0, color='black', linewidth=0.5)
            ax.set_xticks(x)
            ax.set_xticklabels(labels, rotation=90, fontsize=5)
            ax.set_ylabel('Risiko%')
            ax.set_title(f'Kelly-Criterion Analyse | {kelly_type}')
            ax.legend()
            plt.tight_layout()

            caption = (f"Kelly-Analyse | Avg aktuell: {avg_current:.2f}% | "
                       f"Avg {kelly_type}: {avg_kelly:.2f}%")
            send_chart_telegram(fig, caption)
            plt.close(fig)
        except Exception as e:
            print(f"{YELLOW}Chart-Fehler: {e}{NC}")


if __name__ == '__main__':
    main()
