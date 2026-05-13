#!/usr/bin/env python3
"""Stellt alle Configs aus der vorhandenen Optuna-DB wieder her — KEIN neues Training."""
import os, sys, json, optuna
from datetime import datetime, timezone

optuna.logging.set_verbosity(optuna.logging.WARNING)

PROJECT_ROOT = os.path.abspath(os.path.dirname(__file__))
sys.path.insert(0, os.path.join(PROJECT_ROOT, 'src'))

DB_FILE    = os.path.join(PROJECT_ROOT, 'artifacts', 'db', 'optuna_studies_smc.db')
CONFIG_DIR = os.path.join(PROJECT_ROOT, 'src', 'titanbot', 'strategy', 'configs')

if not os.path.exists(DB_FILE):
    print(f"FEHLER: Optuna-DB nicht gefunden: {DB_FILE}")
    sys.exit(1)

storage = f"sqlite:///{DB_FILE}?timeout=60"
study_names = optuna.study.get_all_study_names(storage=storage)
print(f"Gefundene Studies in DB: {len(study_names)}")

os.makedirs(CONFIG_DIR, exist_ok=True)
recovered = 0
skipped   = 0

for study_name in sorted(study_names):
    try:
        study = optuna.load_study(study_name=study_name, storage=storage)
    except Exception as e:
        print(f"  SKIP {study_name}: {e}")
        skipped += 1
        continue

    valid_trials = [t for t in study.trials if t.state == optuna.trial.TrialState.COMPLETE]
    if not valid_trials:
        print(f"  SKIP {study_name}: keine abgeschlossenen Trials")
        skipped += 1
        continue

    best = max(valid_trials, key=lambda t: t.value)
    p    = best.params

    # Symbol/Timeframe aus Study-Name ableiten (Format: smc_BTCUSDTUSDT_4h_strict)
    parts = study_name.split('_')
    # parts[0]='smc', parts[1]=symbol, parts[2]=timeframe, parts[3..]=mode/suffix
    if len(parts) < 3:
        print(f"  SKIP {study_name}: unbekanntes Namensformat")
        skipped += 1
        continue
    raw_symbol = parts[1]   # z.B. BTCUSDTUSDT
    timeframe  = parts[2]   # z.B. 4h

    # Symbol in Bitget-Format umwandeln: BTCUSDTUSDT → BTC/USDT:USDT
    if raw_symbol.endswith('USDTUSDT'):
        base = raw_symbol[:-len('USDTUSDT')]
        symbol = f"{base}/USDT:USDT"
    else:
        symbol = raw_symbol

    def _create_safe_filename(sym, tf):
        return f"{sym.replace('/', '').replace(':', '')}_{tf}"

    config_path = os.path.join(CONFIG_DIR, f"config_{_create_safe_filename(symbol, timeframe)}.json")

    strategy_config = {
        'swingsLength':               p.get('swingsLength', 20),
        'ob_mitigation':              p.get('ob_mitigation', 'High/Low'),
        'use_adx_filter':             p.get('use_adx_filter', False),
        'adx_period':                 p.get('adx_period', 14),
        'adx_threshold':              p.get('adx_threshold', 25),
        'use_pd_filter':              p.get('use_pd_filter', True),
        'use_liquidity_sweep_filter': p.get('use_liquidity_sweep_filter', True),
        'liquidity_lookback':         p.get('liquidity_lookback', 20),
        'min_fvg_size_pct':           round(p.get('min_fvg_size_pct', 0.05), 4),
        'min_ob_quality':             round(p.get('min_ob_quality', 0.2), 3),
        'max_ob_touches':             p.get('max_ob_touches', 1),
        'use_rejection_candle':       p.get('use_rejection_candle', True),
        'use_mtf_filter':             p.get('use_mtf_filter', False),
        'volume_ma_period':           20,
    }
    risk_config = {
        'margin_mode':                    "isolated",
        'risk_per_trade_pct':             round(p.get('risk_per_trade_pct', 1.0), 2),
        'risk_reward_ratio':              round(p.get('risk_reward_ratio', 2.0), 2),
        'min_leverage':                   p.get('min_leverage', 3),
        'max_leverage':                   p.get('max_leverage', 15),
        'atr_multiplier_sl':              round(p.get('atr_multiplier_sl', 1.5), 3),
        'min_sl_pct':                     0.5,
        'structure_sl_buffer_pct':        0.2,
        'trailing_stop_activation_rr':    round(p.get('trailing_stop_activation_rr', 2.0), 2),
        'trailing_stop_callback_rate_pct':round(p.get('trailing_stop_callback_rate_pct', 1.0), 2),
    }

    config_output = {
        "market":   {"symbol": symbol, "timeframe": timeframe},
        "strategy": strategy_config,
        "risk":     risk_config,
        "behavior": {"use_longs": True, "use_shorts": True},
        "_meta": {
            "wfv":             "70/30",
            "test_pnl_pct":    best.user_attrs.get('test_pnl'),
            "train_pnl_pct":   best.user_attrs.get('train_pnl'),
            "test_wr":         best.user_attrs.get('test_wr'),
            "test_trades":     best.user_attrs.get('test_trades'),
            "test_dd_pct":     best.user_attrs.get('test_dd_pct'),
            "composite_score": round(best.value, 4) if best.value else None,
            "recovered_at":    datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
            "source_study":    study_name,
            "best_trial_no":   best.number,
            "total_trials":    len(valid_trials),
        }
    }

    with open(config_path, 'w', encoding='utf-8') as f:
        json.dump(config_output, f, indent=4)

    pnl_str = f"{best.user_attrs.get('test_pnl', '?')}%"
    print(f"  ✔ {os.path.basename(config_path)}  (Trials: {len(valid_trials)}, Test-PnL: {pnl_str})")
    recovered += 1

print(f"\nFertig: {recovered} Configs wiederhergestellt, {skipped} übersprungen.")
print(f"Configs gespeichert in: {CONFIG_DIR}")
