# src/titanbot/analysis/bootstrap_test.py
"""Bootstrap significance test for win rates.

For each config: runs backtest, tests if win_rate > 50% is statistically significant.
Uses binomial test (scipy if available, else normal approximation).
"""

import os
import sys
import argparse
try:
    from tqdm import tqdm
except ImportError:
    def tqdm(it, **kw): return it

import math

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
sys.path.insert(0, os.path.join(PROJECT_ROOT, 'src'))

from titanbot.analysis.analysis_utils import (
    GREEN, YELLOW, RED, CYAN, NC,
    get_settings, get_date_range, load_all_configs,
    run_backtest_for_config, save_send,
)


def binomial_p_value(wins, n, p_null=0.5):
    """P-value for H0: win_rate <= p_null (one-sided, wins >= observed).

    Uses scipy.stats.binomtest if available, else normal approximation.
    Returns p_value (float, lower = more significant).
    """
    if n == 0:
        return 1.0
    try:
        from scipy.stats import binomtest
        result = binomtest(int(wins), int(n), p_null, alternative='greater')
        return result.pvalue
    except ImportError:
        pass

    # Normal approximation: z = (wins - n*p_null) / sqrt(n*p_null*(1-p_null))
    mean = n * p_null
    std  = math.sqrt(n * p_null * (1 - p_null))
    if std < 1e-9:
        return 1.0
    z = (wins - mean) / std
    # P(Z > z) using error function approximation
    p = 0.5 * (1 - math.erf(z / math.sqrt(2)))
    return max(0.0, min(1.0, p))


def main():
    parser = argparse.ArgumentParser(description='Bootstrap-Signifikanztest für Win-Rates')
    parser.add_argument('--min-samples', type=int,   default=10,    help='Mindest-Trades für Test (default: 10)')
    parser.add_argument('--alpha',       type=float, default=0.05,  help='Signifikanzniveau (default: 0.05)')
    parser.add_argument('--no-telegram', action='store_true',       help='Kein Telegram-Report')
    args = parser.parse_args()

    settings = get_settings()
    start_capital = settings.get('optimization_settings', {}).get('start_capital', 20)
    start_date, end_date, warmup_date = get_date_range()

    configs = load_all_configs()
    if not configs:
        print(f"{RED}Keine Configs gefunden.{NC}")
        sys.exit(1)

    print(f"\n{CYAN}=== Bootstrap-Signifikanztest ==={NC}")
    print(f"  Kapital: {start_capital} USDT | Min-Trades: {args.min_samples} | Alpha: {args.alpha}")
    print(f"  H0: Win-Rate <= 50%  |  H1: Win-Rate > 50% (one-sided)\n")

    significant_count = 0
    total_tested = 0
    rows = []

    for cfg in configs:
        ret = run_backtest_for_config(cfg, start_date, end_date, start_capital, warmup_date)
        if ret is None:
            continue
        result, label = ret

        n      = result.get('trades_count', 0)
        wr     = result.get('win_rate', 0.0)
        wins   = round(n * wr / 100)
        pnl    = result.get('total_pnl_pct', 0.0)

        if n < args.min_samples:
            rows.append({'label': label, 'n': n, 'wr': wr, 'p': None, 'sig': None, 'pnl': pnl})
            continue

        total_tested += 1
        p = binomial_p_value(wins, n)
        sig = p < args.alpha
        if sig:
            significant_count += 1
        rows.append({'label': label, 'n': n, 'wr': wr, 'p': p, 'sig': sig, 'pnl': pnl})

    # Sort: significant first, then by p-value
    rows.sort(key=lambda r: (not r['sig'] if r['sig'] is not None else True,
                              r['p'] if r['p'] is not None else 1.0))

    print(f"{'Config':40} {'Trades':>8} {'WR%':>8} {'p-Value':>10} {'Sig?':>6} {'PnL%':>8}")
    print('-' * 85)
    for r in rows:
        if r['p'] is None:
            status = f"{YELLOW}SKIP (n<{args.min_samples}){NC}"
            p_str  = 'N/A'
        elif r['sig']:
            status = f"{GREEN}YES{NC}"
            p_str  = f"{r['p']:.4f}"
        else:
            status = f"{RED}NO{NC}"
            p_str  = f"{r['p']:.4f}"

        pnl_col = GREEN if r['pnl'] > 0 else RED
        print(f"{r['label']:40} {r['n']:>8} {r['wr']:>7.1f}% {p_str:>10} {status:>6}  {pnl_col}{r['pnl']:>+7.1f}%{NC}")

    print('-' * 85)
    print(f"\n{CYAN}Ergebnis:{NC}")
    print(f"  Getestet:      {total_tested} Configs (>= {args.min_samples} Trades)")
    print(f"  Signifikant:   {significant_count} Configs (p < {args.alpha})")
    if total_tested > 0:
        pct = significant_count / total_tested * 100
        print(f"  Rate:          {pct:.0f}%")
        if pct < 20:
            print(f"  {RED}Warnung: Wenige signifikante Configs — Win-Raten könnten Zufall sein{NC}")
        elif pct > 60:
            print(f"  {GREEN}Gut: Mehrheit der Configs hat signifikante Win-Raten{NC}")

    if not args.no_telegram:
        try:
            import matplotlib
            import matplotlib.pyplot as plt

            tested = [r for r in rows if r['p'] is not None]
            if tested:
                labels = [r['label'] for r in tested]
                p_vals = [r['p'] for r in tested]
                colors = ['green' if r['sig'] else 'red' for r in tested]

                fig, ax = plt.subplots(figsize=(12, max(4, len(tested) * 0.35)))
        style_fig(fig)
                y_pos = range(len(tested))
                ax.barh(list(y_pos), p_vals, color=colors, alpha=0.7)
                ax.axvline(args.alpha, color='orange', linestyle='--', linewidth=1.5,
                           label=f'Alpha = {args.alpha}')
                ax.set_yticks(list(y_pos))
                ax.set_yticklabels(labels, fontsize=7)
                ax.set_xlabel('p-Value (kleiner = signifikanter)')
                ax.set_title(f'Bootstrap-Test: Win-Rate > 50% | {significant_count}/{total_tested} signifikant')
                ax.legend()
                ax.invert_yaxis()
                plt.tight_layout()

                caption = (f"Bootstrap-Test | Alpha {args.alpha} | "
                           f"{significant_count}/{total_tested} signifikant")
                save_send(fig, caption)
                plt.close(fig)
        except Exception as e:
            print(f"{YELLOW}Chart-Fehler: {e}{NC}")


if __name__ == '__main__':
    main()
