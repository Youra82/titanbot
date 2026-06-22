# src/titanbot/analysis/correlation.py
"""Correlation analysis between configs based on equity curves.

Runs backtest for each config, computes correlation matrix of equity curves.
Finds pairs with lowest correlation for portfolio diversification.
Shows heatmap chart.
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


def interpolate_equity(equity_curve, n_points=100):
    """Resample equity curve to n_points for uniform comparison."""
    if not equity_curve:
        return None
    try:
        equities = [e['equity'] if isinstance(e, dict) else float(e)
                    for e in equity_curve]
        if len(equities) < 2:
            return None
        # Simple linear interpolation to n_points
        step = (len(equities) - 1) / (n_points - 1)
        resampled = []
        for i in range(n_points):
            idx = i * step
            lo  = int(idx)
            hi  = min(lo + 1, len(equities) - 1)
            frac = idx - lo
            resampled.append(equities[lo] * (1 - frac) + equities[hi] * frac)
        return resampled
    except Exception:
        return None


def pearson_correlation(a, b):
    """Compute Pearson correlation coefficient between two lists."""
    n = len(a)
    if n < 2:
        return 0.0
    mean_a = sum(a) / n
    mean_b = sum(b) / n
    num = sum((x - mean_a) * (y - mean_b) for x, y in zip(a, b))
    den_a = sum((x - mean_a) ** 2 for x in a) ** 0.5
    den_b = sum((y - mean_b) ** 2 for y in b) ** 0.5
    if den_a < 1e-9 or den_b < 1e-9:
        return 0.0
    return num / (den_a * den_b)


def main():
    parser = argparse.ArgumentParser(description='Korrelationsanalyse der Config-Equity-Kurven')
    parser.add_argument('--risk',        type=float, default=None, help='Risiko pro Trade % (override)')
    parser.add_argument('--no-telegram', action='store_true',      help='Kein Telegram-Report')
    parser.add_argument('--top-pairs',   type=int,   default=5,    help='Top N unkorrelierteste Paare (default: 5)')
    args = parser.parse_args()

    settings = get_settings()
    start_capital = settings.get('optimization_settings', {}).get('start_capital', 20)
    start_date, end_date, warmup_date = get_date_range()

    configs = load_all_configs()
    if not configs:
        print(f"{RED}Keine Configs gefunden.{NC}")
        sys.exit(1)

    if args.risk:
        for cfg in tqdm(configs, desc="  Configs", unit="cfg", leave=False,
                        bar_format="{desc}: {n_fmt}/{total_fmt} [{bar:25}] {elapsed}"):
            cfg.setdefault('risk', {})['risk_per_trade_pct'] = args.risk

    print(f"\n{CYAN}=== Korrelationsanalyse ==={NC}")
    print(f"  Kapital: {start_capital} USDT | {len(configs)} Configs\n")

    results_by_label = {}
    for cfg in configs:
        ret = run_backtest_for_config(cfg, start_date, end_date, start_capital, warmup_date)
        if ret is None:
            continue
        result, label = ret
        eq = interpolate_equity(result.get('equity_curve', []))
        if eq:
            results_by_label[label] = eq

    if len(results_by_label) < 2:
        print(f"{YELLOW}Zu wenige Ergebnisse für Korrelationsanalyse.{NC}")
        sys.exit(0)

    labels = sorted(results_by_label.keys())
    n = len(labels)
    print(f"  {n} Configs mit gültiger Equity-Kurve\n")

    # Build correlation matrix
    corr_matrix = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(n):
            corr_matrix[i][j] = pearson_correlation(
                results_by_label[labels[i]],
                results_by_label[labels[j]]
            )

    # Find lowest correlation pairs
    pairs = []
    for i in range(n):
        for j in range(i + 1, n):
            pairs.append((corr_matrix[i][j], labels[i], labels[j]))
    pairs.sort(key=lambda p: p[0])  # lowest correlation first

    print(f"{CYAN}Top {args.top_pairs} am wenigsten korrelierten Paare (Portfolio-Diversifikation):{NC}")
    print(f"{'Rang':>5}  {'Pair':55}  {'Korrelation':>12}")
    print('-' * 78)
    for i, (corr, la, lb) in enumerate(pairs[:args.top_pairs], 1):
        col = GREEN if corr < 0.3 else (YELLOW if corr < 0.6 else RED)
        print(f"{i:>5}  {la[:27]:27} / {lb[:27]:27}  {col}{corr:>+11.3f}{NC}")

    # Also show highest correlations (redundant strategies)
    print(f"\n{CYAN}Top 5 höchst korrelierten Paare (redundante Strategies):{NC}")
    print(f"{'Rang':>5}  {'Pair':55}  {'Korrelation':>12}")
    print('-' * 78)
    for i, (corr, la, lb) in enumerate(reversed(pairs[-5:]), 1):
        col = RED if corr > 0.8 else (YELLOW if corr > 0.5 else GREEN)
        print(f"{i:>5}  {la[:27]:27} / {lb[:27]:27}  {col}{corr:>+11.3f}{NC}")

    if not args.no_telegram and n <= 20:
        # Only plot heatmap if not too many configs
        try:
            import matplotlib
            matplotlib.use('Agg')
            import matplotlib.pyplot as plt
            import numpy as np

            mat = np.array(corr_matrix)
            fig, ax = plt.subplots(figsize=(max(8, n * 0.5), max(7, n * 0.45)))

            short_labels = [l.replace('/USDT:USDT ', '').replace('BTC/', 'BTC/')
                            for l in labels]

            im = ax.imshow(mat, cmap='RdYlGn', vmin=-1, vmax=1, aspect='auto')
            plt.colorbar(im, ax=ax, label='Korrelation')
            ax.set_xticks(range(n))
            ax.set_yticks(range(n))
            ax.set_xticklabels(short_labels, rotation=90, fontsize=6)
            ax.set_yticklabels(short_labels, fontsize=6)
            ax.set_title(f'Equity-Kurven Korrelationsmatrix ({n} Configs)', fontsize=10)

            plt.tight_layout()
            caption = (f"Korrelations-Heatmap | {n} Configs | "
                       f"Min-Korr: {pairs[0][0]:.3f}")
            send_chart_telegram(fig, caption)
            plt.close(fig)
        except Exception as e:
            print(f"{YELLOW}Chart-Fehler: {e}{NC}")
    elif n > 20:
        print(f"\n{YELLOW}Hinweis: Heatmap übersprungen ({n} Configs > 20 — zu groß für gute Darstellung){NC}")


if __name__ == '__main__':
    main()
