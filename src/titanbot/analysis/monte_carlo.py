# src/titanbot/analysis/monte_carlo.py
"""Monte Carlo Simulation — titanbot.

Alle Trades aller Configs werden zu EINER Liste kombiniert.
10.000 zufaellige Permutationen → Equity-Verteilung + MaxDD-Verteilung.
Identisch mit dnabot-Ansatz.
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
    run_backtest_for_config, save_send, style_fig, style_axes, BG_DARK,
)


def _extract_pnl_pcts(result):
    """Gibt Liste von pnl_pct-Werten aus dem Backtest-Ergebnis zurueck.

    trade_record hat kein direktes pnl_pct — berechne aus entry/exit Preis.
    Format: {'entry_long': {'price': ...}, 'exit_long': {'price': ...}, ...}
    """
    trades = result.get('trades_list', [])
    out = []
    for t in trades:
        # 1. Direktes pnl_pct falls vorhanden
        pct = t.get('pnl_pct') or t.get('pnl') or t.get('return_pct') or t.get('profit_pct')
        if pct is not None:
            out.append(float(pct))
            continue
        # 2. Aus entry/exit Preisen berechnen
        side = None
        entry_price = None
        exit_price  = None
        for k in ('entry_long', 'entry_short'):
            if k in t:
                side        = 'long' if k == 'entry_long' else 'short'
                entry_price = t[k].get('price')
                break
        for k in ('exit_long', 'exit_short'):
            if k in t:
                exit_price = t[k].get('price')
                break
        if side and entry_price and exit_price and entry_price > 0:
            raw = (exit_price / entry_price - 1) if side == 'long' else (1 - exit_price / entry_price)
            out.append(raw * 100.0)
    return out


def _simulate_once(pnl_pcts, start_capital):
    """Simuliert eine Equity-Kurve aus pnl_pct-Liste. Gibt (end_equity, max_drawdown_pct)."""
    capital    = start_capital
    peak       = start_capital
    max_dd     = 0.0
    for pct in pnl_pcts:
        capital *= (1.0 + pct / 100.0)
        if capital <= 0:
            return 0.0, 100.0
        if capital > peak:
            peak = capital
        dd = (peak - capital) / peak * 100.0
        if dd > max_dd:
            max_dd = dd
    return capital, max_dd


def run_monte_carlo(pnl_pcts, start_capital, n_simulations, ruin_pct=50.0, seed=42):
    rng = random.Random(seed)
    final_equities = []
    max_drawdowns  = []
    ruin_count     = 0
    ruin_threshold = start_capital * ruin_pct / 100.0

    for _ in range(n_simulations):
        shuffled = pnl_pcts[:]
        rng.shuffle(shuffled)
        end_eq, mdd = _simulate_once(shuffled, start_capital)
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
        'equities': final_equities,
        'drawdowns': max_drawdowns,
    }


def main():
    parser = argparse.ArgumentParser(description='Monte Carlo Simulation — titanbot')
    parser.add_argument('--simulations', type=int,   default=10000)
    parser.add_argument('--capital',     type=float, default=None)
    parser.add_argument('--risk',        type=float, default=None)
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

    all_pnl_pcts = []

    for cfg in tqdm(configs, desc="  Backtests", unit="cfg"):
        if args.risk:
            cfg = dict(cfg)
            cfg['risk'] = dict(cfg.get('risk', {}))
            cfg['risk']['risk_per_trade_pct'] = args.risk
        ret = run_backtest_for_config(cfg, start_date, end_date, start_capital, warmup_date)
        if ret is None:
            continue
        result, _ = ret
        pcts = _extract_pnl_pcts(result)
        all_pnl_pcts.extend(pcts)

    if not all_pnl_pcts:
        print(f"{RED}Keine Trades gefunden — bitte zuerst ./run_pipeline.sh ausfuehren.{NC}")
        sys.exit(1)

    n_trades    = len(all_pnl_pcts)
    risk_pct    = args.risk or settings.get('optimization_settings', {}).get('risk_per_trade_pct', 1.0)

    print(f"\n{CYAN}--- Monte Carlo Portfolio ({n_trades} Trades aus allen Configs) ---{NC}")
    mc = run_monte_carlo(all_pnl_pcts, start_capital, args.simulations)

    p5_pct  = (mc['p5_eq']  - start_capital) / start_capital * 100
    p50_pct = (mc['p50_eq'] - start_capital) / start_capital * 100
    p95_pct = (mc['p95_eq'] - start_capital) / start_capital * 100

    ruin_col = RED if mc['ruin_prob'] > 20 else (YELLOW if mc['ruin_prob'] > 5 else GREEN)
    print(f"  Trades:   {n_trades}")
    print(f"  5th Pct:  {p5_pct:+.1f}%  (End-Kapital: {mc['p5_eq']:.2f} USDT)")
    print(f"  Median:   {p50_pct:+.1f}%  (End-Kapital: {mc['p50_eq']:.2f} USDT)")
    print(f"  95th Pct: {p95_pct:+.1f}%  (End-Kapital: {mc['p95_eq']:.2f} USDT)")
    print(f"  Median MaxDD: {mc['p50_dd']:.1f}%   95th MaxDD: {mc['p95_dd']:.1f}%")
    print(f"  Ruin-Wahrscheinlichkeit (<50%): {ruin_col}{mc['ruin_prob']:.1f}%{NC}")

    # ── Chart ────────────────────────────────────────────────────────────────
    try:
        import matplotlib.pyplot as plt
        import numpy as np

        fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 6))
        style_fig(fig)

        title = (f"titanbot Monte Carlo | {args.simulations:,} Simulationen | "
                 f"{n_trades} Trades | Risk/Trade: {risk_pct:.1f}% | "
                 f"Ruin-Wahrsch. (<50%): {mc['ruin_prob']:.1f}%")
        fig.suptitle(title, fontsize=11, color='white', fontweight='bold')

        # ── Links: Equity-Verteilung ──────────────────────────────────────
        ax1.hist(mc['equities'], bins=80, color='#3b82f6', alpha=0.85)
        ax1.axvline(mc['p5_eq'],  color='#ef4444', linestyle='--', linewidth=1.5,
                    label=f'5. Perzentil: {p5_pct:+.1f}%')
        ax1.axvline(mc['p50_eq'], color='#f59e0b', linestyle='-',  linewidth=1.5,
                    label=f'Median: {p50_pct:+.1f}%')
        ax1.axvline(mc['p95_eq'], color='#22c55e', linestyle='--', linewidth=1.5,
                    label=f'95. Perzentil: {p95_pct:+.1f}%')
        ax1.axvline(start_capital * 0.5, color='#dc2626', linestyle=':', linewidth=2)
        ax1.set_xlabel('End-Kapital (USDT)')
        ax1.set_ylabel('Häufigkeit')
        ax1.set_title('Verteilung der Endkapitale')
        ax1.legend(fontsize=9)

        # ── Rechts: MaxDD-Verteilung ──────────────────────────────────────
        ax2.hist(mc['drawdowns'], bins=80, color='#ef4444', alpha=0.85)
        ax2.axvline(mc['p50_dd'], color='#f59e0b', linestyle='-',  linewidth=1.5,
                    label=f'Median MaxDD: {mc["p50_dd"]:.1f}%')
        ax2.axvline(mc['p95_dd'], color='#ef4444', linestyle='--', linewidth=1.5,
                    label=f'95. Perzentil MaxDD: {mc["p95_dd"]:.1f}%')
        ax2.set_xlabel('Maximaler Drawdown (%)')
        ax2.set_ylabel('Häufigkeit')
        ax2.set_title('Verteilung der Max Drawdowns')
        ax2.legend(fontsize=9)

        style_axes(ax1, ax2)
        plt.tight_layout(rect=[0, 0, 1, 0.93])

        caption = (f"titanbot MC | {n_trades} Trades | "
                   f"Median {p50_pct:+.1f}% | 5th {p5_pct:+.1f}% | "
                   f"MDD {mc['p50_dd']:.1f}% | Ruin {mc['ruin_prob']:.1f}%")
        save_send(fig, 'monte_carlo', caption=caption, no_telegram=args.no_telegram)

    except Exception as e:
        print(f"{YELLOW}Chart-Fehler: {e}{NC}")


if __name__ == '__main__':
    main()
