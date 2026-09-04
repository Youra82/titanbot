#!/usr/bin/env python3
"""
screen_candidates.py (titanbot)

Adaptiert vom gleichnamigen ltbbot-Tool: schnelles Vorab-Screening ueber ein
breites, liquiditaets-sortiertes Coin-Universum, BEVOR man die teure volle
Pipeline (run_pipeline.sh, 150-350 Optuna-Trials) darauf loslaesst.

Loest ein konkretes Problem aus der Session vom 2026-09-04: ein manueller
Scan von 16 "aehnlich wirkenden" L1/L2-Alts auf VANILLA-Default-Parametern
(kein Optuna) fand keinen zweiten AVAX-artigen Kandidaten -- aber das war
kein fairer Test, weil AVAX/ARB ihre Staerke aus einer ECHTEN Optuna-Suche
zogen (Filter-Kombination + R:R + SL/TP passend zur Coin-Charakteristik),
nicht aus Default-Werten. Dieses Tool testet stattdessen mit einer echten
(nur kurzen/billigen) Optuna-Suche pro Kandidat.

Ablauf:
  1. Holt alle aktiven Bitget USDT-M-Perpetuals, sortiert nach 24h-Volumen,
     nimmt die liquidesten TOP_N.
  2. Fuer jedes Symbol: ruft optimizer.py (den ECHTEN Such-/Bewertungscode,
     inkl. aller Fixes vom 2026-09-04 -- Leak-freies Scoring, Trailing-Stop,
     touch_count etc.) mit stark reduzierten Trials und kurzem Zeitraum ueber
     alle SCREEN_TIMEFRAMES auf.
  3. Isoliert von der Produktion:
     --config_suffix "_screen"  -> landet nie im echten Config-Glob von
                                    run_portfolio_optimizer.py
     --results_file <eigene Datei> -> ueberschreibt NICHT die echte
                                    last_optimizer_run.json
  4. Sammelt pro Symbol/Timeframe das Ergebnis (bestaetigt? Test-PnL? Train-
     PnL? Trades?), schreibt es GLEICH nach jedem Symbol in eine CSV
     (Checkpoint -- ein Abbruch verliert nichts), loescht die generierten
     *_screen.json Configs wieder (nur Zwischenergebnis).
  5. Am Ende: sortierte Rangliste der vielversprechendsten Kandidaten fuer
     die volle Pipeline.

Aufruf:
  python screen_candidates.py                    # Standardlauf (Top 100, 40 Trials)
  python screen_candidates.py --top-n 50 --trials 25
  python screen_candidates.py --resume            # ueberspringt bereits gescreente Symbole
"""
import argparse
import json
import os
import subprocess
import sys
import time
from datetime import date, timedelta

# Ohne dies stuerzt jedes print() mit Unicode-Sonderzeichen auf Windows-
# Konsolen ab (cp1252 kennt viele Zeichen nicht) -- betrifft nicht die
# VPS-Produktion (UTF-8-Locale unter Linux).
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding='utf-8')
    except Exception:
        pass

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(PROJECT_ROOT, 'src'))

from titanbot.utils.exchange import Exchange  # noqa: E402

SCREEN_TIMEFRAMES = ['30m', '1h', '2h', '4h', '6h']
CONFIGS_DIR = os.path.join(PROJECT_ROOT, 'src', 'titanbot', 'strategy', 'configs')
SCREEN_RESULTS_DIR = os.path.join(PROJECT_ROOT, 'artifacts', 'results')
CSV_PATH = os.path.join(SCREEN_RESULTS_DIR, 'screen_candidates.csv')
LOG_DIR = os.path.join(PROJECT_ROOT, 'logs', 'screen_candidates')
SCREEN_RESULTS_FILE = os.path.join(SCREEN_RESULTS_DIR, 'screen_last_optimizer_run.json')

CSV_HEADER = "symbol,timeframe,confirmed,test_pnl,train_pnl,test_trades,status\n"


def _log(msg: str):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def fetch_top_symbols(top_n: int) -> list:
    """Holt die liquidesten top_n aktiven USDT-M-Perpetuals nach 24h-Quote-Volumen."""
    with open(os.path.join(PROJECT_ROOT, 'secret.json')) as f:
        secrets = json.load(f)
    ex = Exchange(secrets['titanbot'][0])
    tickers = ex.exchange.fetch_tickers(params={'productType': 'USDT-FUTURES'})
    active_symbols = {
        m['symbol'] for m in ex.markets.values()
        if m.get('swap') and m.get('quote') == 'USDT' and m.get('settle') == 'USDT' and m.get('active', True)
    }
    rows = []
    for sym, t in tickers.items():
        if sym not in active_symbols:
            continue
        vol = t.get('quoteVolume') or 0.0
        rows.append((sym, vol))
    rows.sort(key=lambda r: r[1], reverse=True)
    top = [sym for sym, _ in rows[:top_n]]
    _log(f"Top {len(top)} von {len(rows)} aktiven USDT-Perpetuals nach 24h-Volumen ausgewaehlt.")
    return top


def load_already_screened(resume: bool) -> set:
    done = set()
    if resume and os.path.exists(CSV_PATH):
        with open(CSV_PATH, 'r', encoding='utf-8') as f:
            next(f, None)  # Header
            for line in f:
                parts = line.strip().split(',')
                if len(parts) >= 2:
                    done.add(parts[0])
    return done


def ensure_csv():
    os.makedirs(SCREEN_RESULTS_DIR, exist_ok=True)
    if not os.path.exists(CSV_PATH):
        with open(CSV_PATH, 'w', encoding='utf-8') as f:
            f.write(CSV_HEADER)


def append_rows(rows: list):
    with open(CSV_PATH, 'a', encoding='utf-8') as f:
        for r in rows:
            f.write(",".join(str(x) for x in r) + "\n")


def cleanup_screen_configs(symbol_coin: str):
    """Loescht alle *_screen.json Configs fuer dieses Symbol wieder (nur
    Zwischenergebnis, wird aus den geparsten Werten schon in die CSV
    uebernommen -- soll configs/ nicht mit hunderten Dateien zumuellen)."""
    if not os.path.isdir(CONFIGS_DIR):
        return
    prefix = f"config_{symbol_coin}USDTUSDT_"
    for fname in os.listdir(CONFIGS_DIR):
        if fname.startswith(prefix) and fname.endswith("_screen.json"):
            try:
                os.remove(os.path.join(CONFIGS_DIR, fname))
            except OSError:
                pass


def screen_symbol(symbol: str, trials: int, start_date: str, end_date: str, jobs: int,
                   timeframes: list = None) -> list:
    """Ruft optimizer.py fuer EIN Symbol ueber alle timeframes auf und liest
    danach die Task-Liste aus dem (isolierten) results_file aus -- enthaelt
    fuer JEDE Kombination test_pnl/train_pnl/status, auch fuer nicht
    bestaetigte (status='quality_gate_failed')."""
    timeframes = timeframes or SCREEN_TIMEFRAMES
    coin = symbol.split('/')[0]
    cmd = [
        sys.executable, os.path.join(PROJECT_ROOT, 'src', 'titanbot', 'analysis', 'optimizer.py'),
        '--symbols', coin,
        '--timeframes', ' '.join(timeframes),
        '--start_date', start_date,
        '--end_date', end_date,
        '--jobs', str(jobs),
        '--max_drawdown', '30',
        '--start_capital', '20',
        '--min_win_rate', '0',
        '--trials', str(trials),
        '--min_pnl', '-99999',
        '--mode', 'best_profit',
        '--config_suffix', '_screen',
        '--min_trades_per_year', '20',
        '--results_file', SCREEN_RESULTS_FILE,
    ]
    os.makedirs(LOG_DIR, exist_ok=True)
    log_path = os.path.join(LOG_DIR, f"{coin}.log")
    try:
        with open(log_path, 'w', encoding='utf-8') as logf:
            subprocess.run(cmd, stdout=logf, stderr=subprocess.STDOUT, timeout=1800,
                            env={**os.environ, 'PYTHONIOENCODING': 'utf-8'})
    except subprocess.TimeoutExpired:
        cleanup_screen_configs(coin)
        return [(symbol, tf, False, '', '', '', 'TIMEOUT') for tf in timeframes]
    except Exception as e:
        cleanup_screen_configs(coin)
        return [(symbol, tf, False, '', '', '', f'ERROR:{e}') for tf in timeframes]

    rows = []
    seen_tfs = set()
    try:
        with open(SCREEN_RESULTS_FILE, 'r', encoding='utf-8') as f:
            results = json.load(f)
        for entry in results.get('tasks', []):
            if entry.get('symbol') != symbol:
                continue
            tf = entry.get('timeframe')
            seen_tfs.add(tf)
            confirmed = bool(entry.get('saved'))
            rows.append((symbol, tf, confirmed,
                         entry.get('test_pnl', ''), entry.get('train_pnl', ''),
                         entry.get('test_trades', ''), entry.get('status', '')))
    except Exception:
        pass

    for tf in timeframes:
        if tf not in seen_tfs:
            rows.append((symbol, tf, False, '', '', '', 'kein Ergebnis (uebersprungen oder Absturz)'))

    cleanup_screen_configs(coin)
    return rows


def print_ranking(top=25):
    if not os.path.exists(CSV_PATH):
        _log("Keine Ergebnisse vorhanden.")
        return
    import csv
    with open(CSV_PATH, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    def _f(v):
        try:
            return float(v)
        except (TypeError, ValueError):
            return float('-inf')

    confirmed_rows = [r for r in rows if r.get('confirmed') == 'True']
    confirmed_rows.sort(key=lambda r: _f(r.get('test_pnl')), reverse=True)

    print(f"\n{'='*80}")
    print(f"  Screening-Ergebnis: {len(rows)} Kombinationen getestet, {len(confirmed_rows)} bestaetigt")
    print(f"{'='*80}")
    print(f"  {'Symbol':<14}{'TF':<6}{'Test-PnL%':<12}{'Train-PnL%':<12}{'Trades':<8}")
    for r in confirmed_rows[:top]:
        print(f"  {r['symbol']:<14}{r['timeframe']:<6}{r.get('test_pnl',''):<12}{r.get('train_pnl',''):<12}"
              f"{r.get('test_trades',''):<8}")
    print(f"{'='*80}")
    print(f"  Volle Ergebnisliste: {CSV_PATH}")


def main():
    parser = argparse.ArgumentParser(description="Schnelles Coin/Timeframe-Screening fuer titanbot")
    parser.add_argument('--top-n', type=int, default=100, help='Anzahl liquidester Symbole (Vorfilter)')
    parser.add_argument('--trials', type=int, default=40, help='Optuna-Trials pro Symbol/Timeframe (Screen = wenig)')
    parser.add_argument('--lookback-weeks', type=int, default=12, help='Backtest-Zeitraum in Wochen (70/30-Split intern)')
    parser.add_argument('--jobs', type=int, default=2, help='Optuna-interne Parallelitaet pro Symbol-Lauf')
    parser.add_argument('--resume', action='store_true', help='Bereits gescreente Symbole (laut CSV) ueberspringen')
    parser.add_argument('--timeframes', type=str, default=' '.join(SCREEN_TIMEFRAMES),
                        help='Getestete Timeframes, space-getrennt (Default: alle 5 -- weniger = mehr Coins im Zeitbudget)')
    args = parser.parse_args()
    timeframes = args.timeframes.split()

    end_date = date.today().strftime('%Y-%m-%d')
    start_date = (date.today() - timedelta(weeks=args.lookback_weeks)).strftime('%Y-%m-%d')

    symbols = fetch_top_symbols(args.top_n)
    already = load_already_screened(args.resume)
    todo = [s for s in symbols if s not in already]
    _log(f"{len(todo)} von {len(symbols)} Symbolen zu screenen "
         f"({len(already)} bereits vorhanden, --resume={args.resume}).")
    _log(f"Zeitraum: {start_date} -> {end_date} | Trials/Kombo: {args.trials} | Timeframes: {timeframes}")

    ensure_csv()
    t_start = time.time()
    for i, symbol in enumerate(todo, 1):
        t0 = time.time()
        rows = screen_symbol(symbol, args.trials, start_date, end_date, args.jobs, timeframes)
        append_rows(rows)
        n_conf = sum(1 for r in rows if r[2])
        elapsed = time.time() - t0
        total_elapsed = time.time() - t_start
        avg = total_elapsed / i
        remaining = avg * (len(todo) - i)
        _log(f"[{i}/{len(todo)}] {symbol}: {n_conf}/{len(timeframes)} TF bestaetigt "
             f"({elapsed:.0f}s) -- Rest ca. {remaining/60:.0f} Min")

    print_ranking()


if __name__ == '__main__':
    main()
