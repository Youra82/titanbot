# src/titanbot/analysis/param_optimizer.py
"""Parameter optimizer with walk-forward validation (70/30 split).

Supported parameters:
  rr         risk_reward_ratio: [1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0]
  atr_sl     atr_multiplier_sl: [0.5, 1.0, 1.5, 2.0, 2.5, 3.0]
  smc_window swingsLength:      [10, 20, 30, 40, 50]

For each value: trains on first 70% of date range -> picks best -> validates on last 30%.
"""

import os
import sys
import argparse
try:
    from tqdm import tqdm
except ImportError:
    def tqdm(it, **kw): return it

import copy
from datetime import datetime, timedelta, timezone

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
sys.path.insert(0, os.path.join(PROJECT_ROOT, 'src'))

from titanbot.analysis.analysis_utils import (
    GREEN, YELLOW, RED, CYAN, NC,
    get_settings, get_date_range, load_all_configs,
    run_backtest_for_config, send_chart_telegram,
)

PARAM_RANGES = {
    'rr':         ('risk_reward_ratio',   [1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0], 'risk'),
    'atr_sl':     ('atr_multiplier_sl',   [0.5, 1.0, 1.5, 2.0, 2.5, 3.0],       'risk'),
    'smc_window': ('swingsLength',        [10, 20, 30, 40, 50],                  'strategy'),
}


def split_date_range(start_date, end_date, warmup_date, train_ratio=0.7):
    """Split date range (warmup_date → end_date) into train and test periods."""
    start_dt  = datetime.fromisoformat(start_date.replace('Z', '+00:00')).replace(tzinfo=timezone.utc)
    end_dt    = datetime.fromisoformat(end_date.replace('Z', '+00:00')).replace(tzinfo=timezone.utc)
    warmup_dt = datetime.fromisoformat(warmup_date.replace('Z', '+00:00')).replace(tzinfo=timezone.utc)

    total_days = (end_dt - warmup_dt).days
    if total_days < 2:
        return None, None, None, None

    split_dt = warmup_dt + timedelta(days=int(total_days * train_ratio))

    train_warmup = start_dt.strftime('%Y-%m-%d')
    train_end    = split_dt.strftime('%Y-%m-%d')
    test_warmup  = split_dt.strftime('%Y-%m-%d')
    test_end     = end_dt.strftime('%Y-%m-%d')

    return (start_date, train_end, train_warmup), (start_date, test_end, test_warmup)


def override_param(cfg, param_name, section, value):
    """Return a deep copy of cfg with the given parameter overridden."""
    new_cfg = copy.deepcopy(cfg)
    new_cfg.setdefault(section, {})[param_name] = value
    return new_cfg


def main():
    parser = argparse.ArgumentParser(description='Parameter-Optimizer mit Walk-Forward-Validierung')
    parser.add_argument('--param',       type=str,   default='rr',
                        choices=list(PARAM_RANGES.keys()),
                        help='Parameter zum Testen: rr | atr_sl | smc_window')
    parser.add_argument('--capital',     type=float, default=None, help='Start-Kapital in USDT')
    parser.add_argument('--risk',        type=float, default=None, help='Risiko pro Trade % (override)')
    parser.add_argument('--no-telegram', action='store_true',      help='Kein Telegram-Report')
    args = parser.parse_args()

    settings = get_settings()
    start_capital = args.capital or settings.get('optimization_settings', {}).get('start_capital', 20)

    param_name, param_values, section = PARAM_RANGES[args.param]
    start_date, end_date, warmup_date = get_date_range()

    train_range, test_range = split_date_range(start_date, end_date, warmup_date)
    if train_range is None:
        print(f"{RED}Datumbereich zu klein für 70/30-Split.{NC}")
        sys.exit(1)

    train_start, train_end, train_warmup = train_range
    test_start,  test_end,  test_warmup  = test_range

    configs = load_all_configs()
    if not configs:
        print(f"{RED}Keine Configs gefunden.{NC}")
        sys.exit(1)

    print(f"\n{CYAN}=== Parameter-Optimizer ({args.param}) ==={NC}")
    print(f"  Parameter: {param_name}  |  Werte: {param_values}")
    print(f"  Train: {train_warmup} → {train_end}")
    print(f"  Test:  {test_warmup} → {test_end}")
    print(f"  Kapital: {start_capital} USDT | {len(configs)} Configs\n")

    # TRAIN PHASE
    train_scores = {}  # param_value -> avg_pnl
    for val in param_values:
        pnls = []
        for cfg in tqdm(configs, desc="  Configs", unit="cfg", leave=False,
                        bar_format="{desc}: {n_fmt}/{total_fmt} [{bar:25}] {elapsed}"):
            new_cfg = override_param(cfg, param_name, section, val)
            if args.risk:
                new_cfg['risk']['risk_per_trade_pct'] = args.risk
            ret = run_backtest_for_config(new_cfg, train_start, train_end, start_capital, train_warmup)
            if ret is None:
                continue
            result, _ = ret
            pnls.append(result.get('total_pnl_pct', 0))
        train_scores[val] = sum(pnls) / len(pnls) if pnls else None

    best_val = max((v for v in param_values if train_scores[v] is not None),
                   key=lambda v: train_scores[v],
                   default=None)

    print(f"\n{CYAN}Train-Ergebnisse:{NC}")
    print(f"{'Wert':>10}  {'Avg Train PnL%':>16}  {'Status'}")
    print('-' * 40)
    for val in param_values:
        score = train_scores[val]
        if score is None:
            print(f"{val:>10}  {'N/A':>16}")
            continue
        mark  = f"  {GREEN}<-- BEST{NC}" if val == best_val else ""
        col   = GREEN if score > 0 else RED
        print(f"{val:>10}  {col}{score:>+15.2f}%{NC}{mark}")

    if best_val is None:
        print(f"{RED}Kein bester Wert ermittelt.{NC}")
        sys.exit(1)

    # TEST PHASE — validate with best value
    print(f"\n{CYAN}Test-Validierung (bester Train-Wert: {param_name}={best_val}):{NC}")
    test_pnls, test_wrs, test_dds = [], [], []
    for cfg in configs:
        new_cfg = override_param(cfg, param_name, section, best_val)
        if args.risk:
            new_cfg['risk']['risk_per_trade_pct'] = args.risk
        ret = run_backtest_for_config(new_cfg, test_start, test_end, start_capital, test_warmup)
        if ret is None:
            continue
        result, label = ret
        test_pnls.append(result.get('total_pnl_pct', 0))
        test_wrs.append(result.get('win_rate', 0))
        test_dds.append(result.get('max_drawdown_pct', 0) * 100)

    if test_pnls:
        avg_test_pnl = sum(test_pnls) / len(test_pnls)
        avg_test_wr  = sum(test_wrs) / len(test_wrs)
        avg_test_dd  = sum(test_dds) / len(test_dds)
        col = GREEN if avg_test_pnl > 0 else RED
        print(f"  Avg Test PnL: {col}{avg_test_pnl:+.2f}%{NC}")
        print(f"  Avg Test WR:  {avg_test_wr:.1f}%")
        print(f"  Avg Test DD:  {avg_test_dd:.1f}%")
        print(f"  Configs:      {len(test_pnls)}")
    else:
        print(f"{YELLOW}  Keine Test-Ergebnisse.{NC}")

    # Summary table Train vs Test
    print(f"\n{CYAN}{'='*50}{NC}")
    print(f"{'Wert':>10}  {'Train PnL%':>12}  {'Status'}")
    print('-' * 40)
    for val in param_values:
        ts = train_scores[val]
        if ts is None:
            print(f"{val:>10}  {'N/A':>12}")
            continue
        mark = f"  {GREEN}(BEST → TEST){NC}" if val == best_val else ""
        col  = GREEN if ts > 0 else RED
        print(f"{val:>10}  {col}{ts:>+11.2f}%{NC}{mark}")
    if test_pnls:
        col = GREEN if avg_test_pnl > 0 else RED
        print(f"\n  Test-Score ({best_val}): {col}{avg_test_pnl:+.2f}%{NC} "
              f"WR={avg_test_wr:.1f}% DD={avg_test_dd:.1f}%")

    if not args.no_telegram:
        try:
            import matplotlib
            matplotlib.use('Agg')
            import matplotlib.pyplot as plt

            valid_vals  = [v for v in param_values if train_scores[v] is not None]
            train_pnls  = [train_scores[v] for v in valid_vals]

            fig, ax = plt.subplots(figsize=(10, 5))
            colors = ['green' if p > 0 else 'red' for p in train_pnls]
            bars = ax.bar([str(v) for v in valid_vals], train_pnls, color=colors, alpha=0.8)
            ax.axhline(0, color='black', linewidth=0.8)

            if best_val in valid_vals:
                idx = valid_vals.index(best_val)
                bars[idx].set_edgecolor('gold')
                bars[idx].set_linewidth(3)

            ax.set_xlabel(param_name)
            ax.set_ylabel('Avg PnL% (Train)')
            ax.set_title(f'Parameter-Optimizer: {param_name} (70% Train-Phase)')
            if test_pnls:
                ax.axhline(avg_test_pnl, color='gold', linestyle='--',
                           linewidth=1.5, label=f'Test-PnL ({best_val}): {avg_test_pnl:+.1f}%')
                ax.legend()
            plt.tight_layout()

            caption = (f"Param-Optimizer: {param_name} | Best: {best_val} → "
                       f"Train {train_scores[best_val]:+.1f}% | "
                       + (f"Test {avg_test_pnl:+.1f}%" if test_pnls else ""))
            send_chart_telegram(fig, caption)
            plt.close(fig)
        except Exception as e:
            print(f"{YELLOW}Chart-Fehler: {e}{NC}")


if __name__ == '__main__':
    main()
