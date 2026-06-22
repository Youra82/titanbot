# src/titanbot/analysis/fee_impact.py
"""Fee impact analysis: tests how different fee rates affect backtest results.

The backtester hardcodes 0.05% fee. This script runs the backtest once,
extracts raw trade PnL (before fees), then applies different fee rates
to simulate the impact.

Strategy: run backtest once with fee=0 workaround is not possible since
fee is hardcoded. Instead we run once at 0.05%, extract per-trade notional,
and compute adjusted totals for different fee rates.

Since notional_value is not stored in trades_list, we approximate using
the raw pnl_pct of each trade and capital-at-risk from risk_per_trade_pct.
Simpler approach: run once, take equity curve delta per trade, and add back
the known fee cost (2 * 0.05% * notional), then subtract new fee cost.
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

FEE_RATES = [0.01, 0.02, 0.03, 0.04, 0.05, 0.06, 0.08, 0.10]
BASELINE_FEE = 0.05  # percent — hardcoded in backtester


def simulate_fee(equity_curve, trades_count, start_capital, base_fee_pct, new_fee_pct, risk_pct, rr):
    """Approximate PnL adjustment for a different fee rate.

    For each trade we estimate notional from equity curve + risk_per_trade_pct.
    Simplified: fee delta = (new_fee - base_fee) * 2 * leverage_est * risk_amount * trades
    We use: notional ≈ capital * risk_per_trade_pct/100 / sl_pct_est
    But sl_pct is unknown per trade, so use simpler model:
      For each trade: fee_cost = notional * fee_pct * 2
      We approximate notional = start_capital * leverage_est
      leverage_est from config: use avg of min/max leverage or just scale by rr.

    Realistic approximation: scale equity curve endpoint proportionally.
    """
    if not equity_curve or trades_count == 0:
        return None
    end_eq = equity_curve[-1]['equity'] if isinstance(equity_curve[-1], dict) else equity_curve[-1]
    raw_pnl_usd = end_eq - start_capital

    # Estimate total fee cost at baseline: we can't recover exact per-trade notional
    # from the equity curve alone without re-running. Use a capital-weighted average:
    avg_capital = (start_capital + end_eq) / 2
    avg_notional_est = avg_capital * max(1.0, rr * 2)  # rough leverage estimate

    base_total_fee = avg_notional_est * (base_fee_pct / 100) * 2 * trades_count
    new_total_fee  = avg_notional_est * (new_fee_pct / 100) * 2 * trades_count

    fee_delta = new_total_fee - base_total_fee
    adjusted_end = end_eq - fee_delta
    adjusted_pnl_pct = (adjusted_end - start_capital) / start_capital * 100
    return adjusted_pnl_pct


def main():
    parser = argparse.ArgumentParser(description='Fee-Impact Analyse für titanbot SMC')
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

    print(f"\n{CYAN}=== Fee-Impact Analyse ==={NC}")
    print(f"  Kapital: {start_capital} USDT | {len(configs)} Configs")
    print(f"  Baseline Fee: {BASELINE_FEE}% (hardcoded im Backtester)")
    print(f"  Getestete Fee-Raten: {FEE_RATES}%\n")

    # Run backtest once per config at baseline
    baseline_results = []
    for cfg in configs:
        ret = run_backtest_for_config(cfg, start_date, end_date, start_capital, warmup_date)
        if ret is None:
            continue
        result, label = ret
        risk_pct = cfg.get('risk', {}).get('risk_per_trade_pct', 1.0)
        rr       = cfg.get('risk', {}).get('risk_reward_ratio', 2.0)
        if args.risk:
            risk_pct = args.risk
        baseline_results.append({
            'label': label,
            'result': result,
            'risk_pct': risk_pct,
            'rr': rr,
        })

    if not baseline_results:
        print(f"{RED}Keine Backtest-Ergebnisse.{NC}")
        sys.exit(1)

    print(f"  Baseline ({BASELINE_FEE}%): {len(baseline_results)} Configs ausgewertet\n")

    # Per fee rate: compute adjusted PnL
    fee_table = {}  # fee_pct -> list of adjusted PnL%
    for fee in FEE_RATES:
        adj_pnls = []
        for r in baseline_results:
            result = r['result']
            eq     = result.get('equity_curve', [])
            tc     = result.get('trades_count', 0)
            rr     = r['rr']
            rp     = r['risk_pct']
            adj = simulate_fee(eq, tc, start_capital, BASELINE_FEE, fee, rp, rr)
            if adj is not None:
                adj_pnls.append(adj)
        fee_table[fee] = adj_pnls

    # Find break-even fee
    breakeven_fee = None
    for fee in FEE_RATES:
        avg = sum(fee_table[fee]) / len(fee_table[fee]) if fee_table[fee] else None
        if avg is not None and avg < 0 and breakeven_fee is None:
            breakeven_fee = fee

    # Print table
    print(f"\n{CYAN}{'Fee%':>8} {'Configs':>8} {'Avg PnL%':>12} {'Min PnL%':>12} {'Max PnL%':>12}{NC}")
    print('-' * 55)
    for fee in FEE_RATES:
        vals = fee_table[fee]
        if not vals:
            print(f"{fee:>7.2f}%  {'N/A':>8}")
            continue
        avg = sum(vals) / len(vals)
        mn  = min(vals)
        mx  = max(vals)
        colour = GREEN if avg > 0 else RED
        marker = ' <-- BASELINE' if abs(fee - BASELINE_FEE) < 1e-4 else ''
        print(f"{fee:>7.2f}%  {len(vals):>8} {colour}{avg:>+11.2f}%{NC} {mn:>+11.2f}% {mx:>+11.2f}%{marker}")

    print('-' * 55)
    if breakeven_fee:
        print(f"\n{YELLOW}Break-Even Fee: ~{breakeven_fee:.2f}% — ab hier wird Avg PnL negativ{NC}")
    else:
        print(f"\n{GREEN}Kein Break-Even im getesteten Bereich (Avg PnL bleibt positiv).{NC}")

    # Chart
    if not args.no_telegram:
        try:
            import matplotlib
            import matplotlib.pyplot as plt

            valid_fees = [f for f in FEE_RATES if fee_table[f]]
            avgs = [sum(fee_table[f]) / len(fee_table[f]) for f in valid_fees]

            fig, ax = plt.subplots(figsize=(10, 5))
        style_fig(fig)
            colors = ['green' if a > 0 else 'red' for a in avgs]
            ax.bar([str(f) for f in valid_fees], avgs, color=colors)
            ax.axhline(0, color='black', linewidth=0.8)
            ax.axvline(x=str(BASELINE_FEE), color='orange', linewidth=2, linestyle='--', label=f'Baseline {BASELINE_FEE}%')
            ax.set_xlabel('Fee-Rate (%)')
            ax.set_ylabel('Avg PnL% (alle Configs)')
            ax.set_title('Fee-Impact Analyse: Avg PnL% vs Fee-Rate')
            ax.legend()

            plt.tight_layout()
            caption = f"Fee-Impact | Baseline: {BASELINE_FEE}% | Break-Even: {breakeven_fee or 'N/A'}%"
            save_send(fig, caption)
            plt.close(fig)
        except Exception as e:
            print(f"{YELLOW}Chart-Fehler: {e}{NC}")


if __name__ == '__main__':
    main()
