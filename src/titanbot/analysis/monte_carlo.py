# src/titanbot/analysis/monte_carlo.py
"""Monte Carlo Simulation — titanbot.

Alle Trade-Outcomes (Win/Loss) aus allen Configs werden kombiniert.
Jede Simulation: Trade-Reihenfolge zufaellig, Compound-Sizing (X% des
aktuellen Kapitals risikiert). Reihenfolge bestimmt das Endkapital.
"""

import os
import sys
import argparse
import random

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
)


def _extract_trade_params(result, cfg):
    """Gibt Liste von (is_win, risk_pct, rr) pro Trade zurueck."""
    trades   = result.get('trades_list', [])
    risk_pct = cfg.get('risk', {}).get('risk_per_trade_pct', 1.0)
    rr       = cfg.get('risk', {}).get('risk_reward_ratio', 2.0)
    out = []
    for t in trades:
        pnl_pct = t.get('pnl_pct')
        if pnl_pct is None:
            # Berechnen aus entry/exit-Preisen
            side = entry_price = exit_price = None
            for k in ('entry_long', 'entry_short'):
                if k in t:
                    side = 'long' if k == 'entry_long' else 'short'
                    entry_price = t[k].get('price')
            for k in ('exit_long', 'exit_short'):
                if k in t:
                    exit_price = t[k].get('price')
            if side and entry_price and exit_price and entry_price > 0:
                raw = (exit_price / entry_price - 1) if side == 'long' else (1 - exit_price / entry_price)
                pnl_pct = raw * 100
        if pnl_pct is not None:
            out.append((pnl_pct > 0, float(risk_pct), float(rr)))
    return out


def _simulate_once(trade_params, start_capital, ruin_threshold):
    """Compound MC: jeder Trade risikiert risk_pct des aktuellen Kapitals."""
    capital = start_capital
    peak    = start_capital
    max_dd  = 0.0
    for is_win, risk_pct, rr in trade_params:
        if is_win:
            capital *= (1.0 + risk_pct * rr / 100.0)
        else:
            capital *= (1.0 - risk_pct / 100.0)
        if capital <= 0:
            return 0.0, 100.0
        if capital > peak:
            peak = capital
        dd = (peak - capital) / peak * 100.0
        if dd > max_dd:
            max_dd = dd
    return capital, max_dd


def run_monte_carlo(trade_params, start_capital, n_simulations, ruin_pct=50.0, seed=42):
    rng            = random.Random(seed)
    final_equities = []
    max_drawdowns  = []
    ruin_count     = 0
    ruin_threshold = start_capital * ruin_pct / 100.0

    for _ in range(n_simulations):
        shuffled = trade_params[:]
        rng.shuffle(shuffled)
        end_eq, mdd = _simulate_once(shuffled, start_capital, ruin_threshold)
        final_equities.append(end_eq)
        max_drawdowns.append(mdd)
        if end_eq < ruin_threshold:
            ruin_count += 1

    final_equities.sort()
    max_drawdowns.sort()
    n = len(final_equities)

    return {
        'p5_eq':   final_equities[int(n * 0.05)],
        'p50_eq':  final_equities[int(n * 0.50)],
        'p95_eq':  final_equities[int(n * 0.95)],
        'p50_dd':  max_drawdowns[int(n * 0.50)],
        'p95_dd':  max_drawdowns[int(n * 0.95)],
        'ruin_prob': ruin_count / n_simulations * 100.0,
        'equities':  final_equities,
        'drawdowns': max_drawdowns,
    }


def main():
    parser = argparse.ArgumentParser(description='Monte Carlo Simulation — titanbot')
    parser.add_argument('--simulations', type=int,   default=10000)
    parser.add_argument('--capital',     type=float, default=None)
    parser.add_argument('--risk',        type=float, default=None,
                        help='Risk %% pro Trade (ueberschreibt Config)')
    parser.add_argument('--no-telegram', action='store_true')
    args = parser.parse_args()

    settings      = get_settings()
    start_capital = args.capital or settings.get('optimization_settings', {}).get('start_capital', 20)
    start_date, end_date, warmup_date = get_date_range()

    configs = load_all_configs()
    if not configs:
        print(f"{RED}Keine Configs gefunden.{NC}")
        sys.exit(1)

    print(f"\n{CYAN}=== Monte Carlo Simulation ==={NC}")
    print(f"  Kapital: {start_capital} USDT | Simulationen: {args.simulations:,} | {len(configs)} Configs\n")

    all_trade_params = []

    for cfg in tqdm(configs, desc="  Backtests", unit="cfg"):
        if args.risk:
            cfg = dict(cfg)
            cfg['risk'] = dict(cfg.get('risk', {}))
            cfg['risk']['risk_per_trade_pct'] = args.risk
        ret = run_backtest_for_config(cfg, start_date, end_date, start_capital, warmup_date)
        if ret is None:
            continue
        result, _ = ret
        params = _extract_trade_params(result, cfg)
        all_trade_params.extend(params)

    if not all_trade_params:
        print(f"{RED}Keine Trades gefunden — bitte zuerst ./run_pipeline.sh ausfuehren.{NC}")
        sys.exit(1)

    n_trades = len(all_trade_params)
    n_wins   = sum(1 for is_win, *_ in all_trade_params if is_win)
    avg_risk = sum(r for _, r, _ in all_trade_params) / n_trades
    win_rate = n_wins / n_trades * 100

    print(f"\n{CYAN}--- Monte Carlo Portfolio ({n_trades} Trades) ---{NC}")
    print(f"  Win-Rate: {win_rate:.1f}%  |  Avg Risk/Trade: {avg_risk:.2f}%")

    mc = run_monte_carlo(all_trade_params, start_capital, args.simulations)

    p5_pct  = (mc['p5_eq']  - start_capital) / start_capital * 100
    p50_pct = (mc['p50_eq'] - start_capital) / start_capital * 100
    p95_pct = (mc['p95_eq'] - start_capital) / start_capital * 100

    ruin_col = RED if mc['ruin_prob'] > 20 else (YELLOW if mc['ruin_prob'] > 5 else GREEN)
    print(f"  5th Pct:  {p5_pct:+.1f}%  (End-Kapital: {mc['p5_eq']:.2f} USDT)")
    print(f"  Median:   {p50_pct:+.1f}%  (End-Kapital: {mc['p50_eq']:.2f} USDT)")
    print(f"  95th Pct: {p95_pct:+.1f}%  (End-Kapital: {mc['p95_eq']:.2f} USDT)")
    print(f"  Median MaxDD:   {mc['p50_dd']:.1f}%")
    print(f"  95th MaxDD:     {mc['p95_dd']:.1f}%")
    print(f"  Ruin (<50%): {ruin_col}{mc['ruin_prob']:.1f}%{NC}")

    # ── Chart ────────────────────────────────────────────────────────────────
    try:
        import matplotlib.pyplot as plt

        fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 6))
        style_fig(fig)

        title = (f"titanbot Monte Carlo | {args.simulations:,} Simulationen | "
                 f"{n_trades} Trades | WR: {win_rate:.1f}% | Risk: {avg_risk:.2f}% | "
                 f"Ruin (<50%): {mc['ruin_prob']:.1f}%")
        fig.suptitle(title, fontsize=10, color='white', fontweight='bold')

        # ── Equity-Verteilung ─────────────────────────────────────────────
        equities = mc['equities']
        bins_eq  = max(10, min(80, len(set(round(e, 2) for e in equities))))
        ax1.hist(equities, bins=bins_eq, color='#3b82f6', alpha=0.85)
        ax1.axvline(mc['p5_eq'],  color='#ef4444', linestyle='--', linewidth=1.5,
                    label=f'5. Pct: {p5_pct:+.1f}%')
        ax1.axvline(mc['p50_eq'], color='#f59e0b', linestyle='-',  linewidth=1.5,
                    label=f'Median: {p50_pct:+.1f}%')
        ax1.axvline(mc['p95_eq'], color='#22c55e', linestyle='--', linewidth=1.5,
                    label=f'95. Pct: {p95_pct:+.1f}%')
        ax1.axvline(start_capital * 0.5, color='#dc2626', linestyle=':', linewidth=2,
                    label=f'Ruin (<50%)')
        ax1.set_xlabel('End-Kapital (USDT)')
        ax1.set_ylabel('Häufigkeit')
        ax1.set_title('Verteilung der Endkapitale')
        ax1.legend(fontsize=9)

        # ── MaxDD-Verteilung ──────────────────────────────────────────────
        dds     = mc['drawdowns']
        bins_dd = max(10, min(80, len(set(round(d, 1) for d in dds))))
        ax2.hist(dds, bins=bins_dd, color='#ef4444', alpha=0.85)
        ax2.axvline(mc['p50_dd'], color='#f59e0b', linestyle='-',  linewidth=1.5,
                    label=f'Median MaxDD: {mc["p50_dd"]:.1f}%')
        ax2.axvline(mc['p95_dd'], color='#ef4444', linestyle='--', linewidth=1.5,
                    label=f'95. Pct MaxDD: {mc["p95_dd"]:.1f}%')
        ax2.set_xlabel('Maximaler Drawdown (%)')
        ax2.set_ylabel('Häufigkeit')
        ax2.set_title('Verteilung der Max Drawdowns')
        ax2.legend(fontsize=9)

        style_axes(ax1, ax2)
        plt.tight_layout(rect=[0, 0, 1, 0.92])

        caption = (f"titanbot MC | {n_trades} Trades | WR {win_rate:.1f}% | "
                   f"Median {p50_pct:+.1f}% | MDD {mc['p50_dd']:.1f}% | "
                   f"Ruin {mc['ruin_prob']:.1f}%")
        save_send(fig, 'monte_carlo', caption=caption, no_telegram=args.no_telegram)

    except Exception as e:
        print(f"{YELLOW}Chart-Fehler: {e}{NC}")


if __name__ == '__main__':
    main()
