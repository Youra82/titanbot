# src/titanbot/analysis/sensitivity.py
"""Sensitivity analysis (Tornado chart).

For each tunable parameter: runs base backtest, then varies it by
-50% / -20% / +20% / +50%. Computes PnL change per variation.
Generates Tornado chart sorted by sensitivity.

Parameters tested:
  risk_reward_ratio, atr_multiplier_sl, swingsLength,
  risk_per_trade_pct, min_fvg_size_pct, min_ob_quality
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

PARAM_DEFS = [
    ('risk',     'risk_reward_ratio'),
    ('risk',     'atr_multiplier_sl'),
    ('strategy', 'swingsLength'),
    ('risk',     'risk_per_trade_pct'),
    ('strategy', 'min_fvg_size_pct'),
    ('strategy', 'min_ob_quality'),
]

VARIATIONS = [-0.50, -0.20, +0.20, +0.50]
LABELS     = ['-50%', '-20%', '+20%', '+50%']


def avg_pnl_for_param(configs, section, param, factor, start_date, end_date,
                      warmup_date, start_capital):
    """Override param in all configs, run backtests, return avg PnL."""
    pnls = []
    for cfg in configs:
        base_val = cfg.get(section, {}).get(param)
        if base_val is None:
            continue
        new_val = base_val * (1 + factor)
        # Prevent nonsensical values
        if new_val <= 0:
            new_val = base_val * 0.01

        new_cfg = copy.deepcopy(cfg)
        new_cfg.setdefault(section, {})[param] = new_val

        ret = run_backtest_for_config(new_cfg, start_date, end_date, start_capital, warmup_date)
        if ret is None:
            continue
        result, _ = ret
        pnls.append(result.get('total_pnl_pct', 0))
    return sum(pnls) / len(pnls) if pnls else None


def main():
    parser = argparse.ArgumentParser(description='Sensitivitätsanalyse (Tornado-Chart)')
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

    print(f"\n{CYAN}=== Sensitivitätsanalyse (Tornado) ==={NC}")
    print(f"  Kapital: {start_capital} USDT | {len(configs)} Configs")
    print(f"  Variationen: {LABELS}\n")

    # Baseline
    print(f"  Berechne Baseline...")
    base_pnls = []
    for cfg in configs:
        ret = run_backtest_for_config(cfg, start_date, end_date, start_capital, warmup_date)
        if ret:
            base_pnls.append(ret[0].get('total_pnl_pct', 0))
    baseline = sum(base_pnls) / len(base_pnls) if base_pnls else 0
    print(f"  Baseline Avg PnL: {baseline:+.2f}%\n")

    sensitivity_results = []

    for section, param in PARAM_DEFS:
        print(f"  Teste {param}...")
        var_pnls = {}
        for factor, label in zip(VARIATIONS, LABELS):
            avg = avg_pnl_for_param(
                configs, section, param, factor,
                start_date, end_date, warmup_date, start_capital
            )
            var_pnls[label] = avg

        # Sensitivity = max change from baseline
        deltas = {lbl: (v - baseline) if v is not None else 0 for lbl, v in var_pnls.items()}
        max_delta = max(abs(d) for d in deltas.values())
        sensitivity_results.append({
            'param': param, 'section': section,
            'deltas': deltas, 'max_delta': max_delta,
            'var_pnls': var_pnls,
        })

        print(f"    {'Var':>6}  {'Avg PnL%':>10}  {'Delta':>8}")
        for lbl, v in var_pnls.items():
            if v is None:
                print(f"    {lbl:>6}  {'N/A':>10}")
                continue
            d = v - baseline
            col = GREEN if d > 0 else RED
            print(f"    {lbl:>6}  {v:>+9.2f}%  {col}{d:>+7.2f}%{NC}")

    # Sort by max sensitivity
    sensitivity_results.sort(key=lambda r: r['max_delta'], reverse=True)

    print(f"\n{CYAN}=== Tornado-Ranking (nach max. Auswirkung) ==={NC}")
    print(f"{'Rang':>5}  {'Parameter':30}  {'Max |ΔPnL%|':>12}")
    print('-' * 55)
    for i, r in enumerate(sensitivity_results, 1):
        col = GREEN if r['max_delta'] > 1 else (YELLOW if r['max_delta'] > 0.3 else NC)
        print(f"{i:>5}  {r['param']:30}  {col}{r['max_delta']:>+11.2f}%{NC}")

    if not args.no_telegram:
        try:
            import matplotlib
            import matplotlib.pyplot as plt
            import numpy as np

            params = [r['param'] for r in sensitivity_results]
            # For Tornado: show +50% delta and -50% delta per param
            pos_deltas = [r['deltas'].get('+50%', 0) for r in sensitivity_results]
            neg_deltas = [r['deltas'].get('-50%', 0) for r in sensitivity_results]

            fig, ax = plt.subplots(figsize=(11, max(4, len(params) * 0.6)))
        style_fig(fig)
            y_pos = range(len(params))

            ax.barh(list(y_pos), pos_deltas, color='green', alpha=0.7, label='+50% Variation')
            ax.barh(list(y_pos), neg_deltas, color='red',   alpha=0.7, label='-50% Variation')
            ax.axvline(0, color='black', linewidth=0.8)
            ax.set_yticks(list(y_pos))
            ax.set_yticklabels(params)
            ax.set_xlabel('ΔPnL% vs. Baseline')
            ax.set_title(f'Tornado-Chart: Parameter-Sensitivität (Baseline: {baseline:+.1f}%)')
            ax.legend()
            ax.invert_yaxis()
            plt.tight_layout()

            caption = f"Sensitivitäts-Analyse | Baseline {baseline:+.1f}% | Top: {params[0]}"
            save_send(fig, caption)
            plt.close(fig)
        except Exception as e:
            print(f"{YELLOW}Chart-Fehler: {e}{NC}")


if __name__ == '__main__':
    main()
