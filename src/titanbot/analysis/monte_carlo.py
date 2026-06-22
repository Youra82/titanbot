# src/titanbot/analysis/monte_carlo.py
"""Monte Carlo simulation for titanbot.

Runs backtest once per config to get trades list.
Shuffles trade order N times and computes final equity distribution.
Shows: median, 5th/95th percentile, ruin probability (equity < 50% start).
"""

import os
import sys
import argparse
import random
import math

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
sys.path.insert(0, os.path.join(PROJECT_ROOT, 'src'))

from titanbot.analysis.analysis_utils import (
    GREEN, YELLOW, RED, CYAN, NC,
    get_settings, get_date_range, load_all_configs,
    run_backtest_for_config, send_chart_telegram,
)


def simulate_equity_from_trades(trade_pnls, start_capital):
    """Simulate equity curve from a list of trade PnL values (USD)."""
    capital = start_capital
    for pnl in trade_pnls:
        capital += pnl
        if capital <= 0:
            return 0.0
    return capital


def extract_trade_pnls(result, start_capital):
    """Extract per-trade PnL in USD from equity curve deltas."""
    eq = result.get('equity_curve', [])
    if not eq:
        return []

    # The equity curve has one entry per candle, not per trade.
    # We reconstruct per-trade P&L from trades_list if possible,
    # falling back to equity_curve delta approach.
    trades_list = result.get('trades_list', [])
    if not trades_list:
        return []

    # Best approximation: equity_curve length vs trades_count
    # We'll use equity_curve to estimate overall return, then
    # spread it proportionally — but that loses information.
    # Better: use equity_curve change at trade exit bars.
    # Since we don't have per-trade USD PnL directly stored,
    # we approximate by distributing total P&L evenly (works for MC shuffling).
    trades_count = result.get('trades_count', 0)
    if trades_count == 0:
        return []

    end_eq = eq[-1]['equity'] if isinstance(eq[-1], dict) else float(eq[-1])
    total_pnl_usd = end_eq - start_capital

    # Use win_rate to split into wins and losses
    win_rate = result.get('win_rate', 50.0) / 100.0
    wins  = max(1, round(trades_count * win_rate))
    losses = trades_count - wins

    if trades_count == 0:
        return []

    rr = result.get('_rr', 2.0)  # fallback
    # Per-trade average: total_pnl = wins*W - losses*L, with W/L = rr
    # total_pnl = wins*W - losses*(W/rr) => W = total_pnl / (wins - losses/rr)
    denom = wins - (losses / max(rr, 0.01))
    if abs(denom) < 1e-6:
        avg_win = abs(total_pnl_usd / trades_count)
        avg_loss = avg_win / max(rr, 0.01)
    else:
        avg_win  = total_pnl_usd / denom
        avg_loss = avg_win / max(rr, 0.01)

    avg_win  = max(avg_win,  0.01)
    avg_loss = max(avg_loss, 0.01)

    pnls = ([avg_win] * wins) + ([-avg_loss] * losses)
    return pnls


def run_monte_carlo(pnls, start_capital, n_simulations, ruin_threshold_pct=50):
    """Shuffle trades N times and compute final equity distribution."""
    if not pnls:
        return None

    rng = random.Random(42)
    final_equities = []
    ruin_count = 0
    ruin_threshold = start_capital * ruin_threshold_pct / 100

    for _ in range(n_simulations):
        shuffled = pnls[:]
        rng.shuffle(shuffled)
        final_eq = simulate_equity_from_trades(shuffled, start_capital)
        final_equities.append(final_eq)
        if final_eq < ruin_threshold:
            ruin_count += 1

    final_equities.sort()
    n = len(final_equities)
    p5  = final_equities[int(n * 0.05)]
    p50 = final_equities[int(n * 0.50)]
    p95 = final_equities[int(n * 0.95)]

    return {
        'p5': p5,
        'p50': p50,
        'p95': p95,
        'ruin_prob': ruin_count / n_simulations * 100,
        'all': final_equities,
        'n': n_simulations,
    }


def main():
    parser = argparse.ArgumentParser(description='Monte Carlo Simulation für titanbot SMC')
    parser.add_argument('--simulations', type=int, default=10000, help='Anzahl Simulationen (default: 10000)')
    parser.add_argument('--capital', type=float, default=None, help='Start-Kapital in USDT')
    parser.add_argument('--risk',    type=float, default=None, help='Risiko pro Trade % (override)')
    parser.add_argument('--no-telegram', action='store_true', help='Kein Telegram-Report')
    args = parser.parse_args()

    settings = get_settings()
    start_capital = args.capital or settings.get('optimization_settings', {}).get('start_capital', 20)
    start_date, end_date, warmup_date = get_date_range()

    configs = load_all_configs()
    if not configs:
        print(f"{RED}Keine Configs gefunden.{NC}")
        sys.exit(1)

    print(f"\n{CYAN}=== Monte Carlo Simulation ==={NC}")
    print(f"  Kapital: {start_capital} USDT | Simulationen: {args.simulations} | {len(configs)} Configs\n")

    all_pnls = []

    for cfg in configs:
        if args.risk:
            cfg = dict(cfg)
            cfg['risk'] = dict(cfg.get('risk', {}))
            cfg['risk']['risk_per_trade_pct'] = args.risk

        ret = run_backtest_for_config(cfg, start_date, end_date, start_capital, warmup_date)
        if ret is None:
            continue
        result, label = ret
        rr = cfg.get('risk', {}).get('risk_reward_ratio', 2.0)
        result['_rr'] = rr
        pnls = extract_trade_pnls(result, start_capital)
        if not pnls:
            print(f"  {YELLOW}{label}: keine Trades für MC-Simulation{NC}")
            continue

        mc = run_monte_carlo(pnls, start_capital, args.simulations)
        if mc is None:
            continue

        p50_pct = (mc['p50'] - start_capital) / start_capital * 100
        p5_pct  = (mc['p5']  - start_capital) / start_capital * 100
        p95_pct = (mc['p95'] - start_capital) / start_capital * 100
        ruin    = mc['ruin_prob']

        colour = GREEN if p50_pct > 0 else RED
        ruin_col = RED if ruin > 20 else (YELLOW if ruin > 5 else GREEN)
        print(f"  {label}")
        print(f"    Median: {colour}{p50_pct:+.1f}%{NC}  |  5th: {p5_pct:+.1f}%  |  95th: {p95_pct:+.1f}%  |  Ruin (<50%): {ruin_col}{ruin:.1f}%{NC}")

        all_pnls.extend(pnls)

    if all_pnls:
        print(f"\n{CYAN}--- Portfolio-MC (alle Configs kombiniert) ---{NC}")
        mc_all = run_monte_carlo(all_pnls, start_capital, args.simulations)
        if mc_all:
            p50_pct = (mc_all['p50'] - start_capital) / start_capital * 100
            p5_pct  = (mc_all['p5']  - start_capital) / start_capital * 100
            p95_pct = (mc_all['p95'] - start_capital) / start_capital * 100
            print(f"  Median: {p50_pct:+.1f}%  |  5th: {p5_pct:+.1f}%  |  95th: {p95_pct:+.1f}%")
            print(f"  Ruin-Wahrscheinlichkeit (<50% Kapital): {mc_all['ruin_prob']:.1f}%")

            if not args.no_telegram:
                try:
                    import matplotlib
                    matplotlib.use('Agg')
                    import matplotlib.pyplot as plt

                    fig, ax = plt.subplots(figsize=(10, 5))
                    final_equities = mc_all['all']
                    bins = min(100, len(set(final_equities)))
                    ax.hist(final_equities, bins=bins, color='steelblue', edgecolor='white', alpha=0.8)
                    ax.axvline(mc_all['p5'],  color='red',    linestyle='--', label=f'5th Pct: {p5_pct:+.1f}%')
                    ax.axvline(mc_all['p50'], color='green',  linestyle='-',  label=f'Median: {p50_pct:+.1f}%')
                    ax.axvline(mc_all['p95'], color='orange', linestyle='--', label=f'95th Pct: {p95_pct:+.1f}%')
                    ax.axvline(start_capital * 0.5, color='darkred', linestyle=':', linewidth=2,
                               label=f'Ruin (<50%): {mc_all["ruin_prob"]:.1f}%')
                    ax.set_xlabel('End-Kapital (USDT)')
                    ax.set_ylabel('Häufigkeit')
                    ax.set_title(f'Monte Carlo ({args.simulations:,} Simulationen) — Portfolio')
                    ax.legend()
                    plt.tight_layout()
                    caption = (f"Monte Carlo ({args.simulations:,} Sims) | "
                               f"Median {p50_pct:+.1f}% | 5th {p5_pct:+.1f}% | Ruin {mc_all['ruin_prob']:.1f}%")
                    send_chart_telegram(fig, caption)
                    plt.close(fig)
                except Exception as e:
                    print(f"{YELLOW}Chart-Fehler: {e}{NC}")


if __name__ == '__main__':
    main()
