# src/titanbot/analysis/analysis_utils.py
"""Shared utilities for titanbot analysis scripts."""

import os
import sys
import json
import glob as _glob
import contextlib
import io
from datetime import datetime, timedelta, timezone

# Agg-Backend MUSS vor jedem pyplot-Import gesetzt werden
import matplotlib
matplotlib.use('Agg')

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
sys.path.insert(0, os.path.join(PROJECT_ROOT, 'src'))

# ANSI colour constants
G  = GREEN  = '\033[92m'
Y  = YELLOW = '\033[93m'
R  = RED    = '\033[91m'
C  = CYAN   = '\033[96m'
NC = '\033[0m'

CONFIGS_DIR = os.path.join(PROJECT_ROOT, 'src', 'titanbot', 'strategy', 'configs')
SETTINGS_FILE = os.path.join(PROJECT_ROOT, 'settings.json')
DOCS_DIR = os.path.join(PROJECT_ROOT, 'docs')
TMP_DIR  = '/tmp'


def get_settings():
    with open(SETTINGS_FILE, 'r', encoding='utf-8') as f:
        return json.load(f)


def get_date_range(lookback_weeks=None):
    """Return (start_date, end_date, warmup_date) as ISO strings (UTC)."""
    settings = get_settings()
    opt = settings.get('optimization_settings', {})
    lw = lookback_weeks if lookback_weeks is not None else opt.get('backtest_lookback_weeks', 2)
    ww = opt.get('warmup_weeks', 4)
    now = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    end_dt    = now
    warmup_dt = end_dt   - timedelta(weeks=lw)
    start_dt  = warmup_dt - timedelta(weeks=ww)
    return (
        start_dt.strftime('%Y-%m-%d'),
        end_dt.strftime('%Y-%m-%d'),
        warmup_dt.strftime('%Y-%m-%d'),
    )


def load_all_configs():
    """Return list of config dicts from configs/ directory."""
    pattern = os.path.join(CONFIGS_DIR, 'config_*.json')
    configs = []
    for path in sorted(_glob.glob(pattern)):
        try:
            with open(path, 'r', encoding='utf-8') as f:
                cfg = json.load(f)
            cfg['_config_path'] = path
            configs.append(cfg)
        except Exception as e:
            print(f"{YELLOW}WARNUNG: {path}: {e}{NC}")
    return configs


@contextlib.contextmanager
def _quiet():
    """Suppress stdout/stderr during download/cache messages."""
    with contextlib.redirect_stdout(io.StringIO()), \
         contextlib.redirect_stderr(io.StringIO()):
        yield


def run_backtest_for_config(cfg, start_date, end_date, start_capital, warmup_date=None, silent=True):
    """Run load_data + run_smc_backtest for one config. Returns (result, label) or None."""
    try:
        from titanbot.analysis.backtester import load_data, run_smc_backtest
        market = cfg.get('market', {})
        symbol = market.get('symbol', '')
        tf     = market.get('timeframe', '')
        smc_p  = dict(cfg.get('strategy', {}))
        risk_p = dict(cfg.get('risk', {}))
        if not symbol or not tf:
            return None
        smc_p['_timeframe'] = tf
        label = f"{symbol} {tf}"
        ctx = _quiet() if silent else contextlib.nullcontext()
        with ctx:
            data = load_data(symbol, tf, start_date, end_date)
        if data is None or data.empty:
            return None
        with (_quiet() if silent else contextlib.nullcontext()):
            result = run_smc_backtest(
                data, smc_p, risk_p,
                start_capital=start_capital,
                verbose=False,
                backtest_start_date=warmup_date,
            )
        return result, label
    except Exception as e:
        sym = cfg.get('market', {}).get('symbol', '?')
        tf  = cfg.get('market', {}).get('timeframe', '?')
        print(f"{RED}  Fehler bei {sym} {tf}: {e}{NC}")
        return None


# ─── Telegram ─────────────────────────────────────────────────────────────────

def get_telegram():
    """Read (bot_token, chat_id) from secret.json. Returns (None, None) on failure."""
    try:
        secret_path = os.path.join(PROJECT_ROOT, 'secret.json')
        with open(secret_path, 'r', encoding='utf-8') as f:
            s = json.load(f)
        # titanbot key structure: {"titanbot": [{"telegram_bot_token": ..., "telegram_chat_id": ...}]}
        acc = s.get('titanbot', [{}])
        if isinstance(acc, list):
            acc = acc[0] if acc else {}
        token   = acc.get('telegram_bot_token', '')
        chat_id = acc.get('telegram_chat_id', '')
        # fallback: top-level "telegram" key (wie dnabot)
        if not token:
            tg = s.get('telegram', {})
            token   = tg.get('bot_token', '')
            chat_id = tg.get('chat_id', '')
        return (token, chat_id) if token and chat_id else (None, None)
    except Exception as e:
        print(f"{YELLOW}  Telegram-Key nicht lesbar: {e}{NC}")
        return None, None


def _send_photo(token, chat_id, path, caption=''):
    try:
        import requests
        with open(path, 'rb') as f:
            r = requests.post(
                f'https://api.telegram.org/bot{token}/sendPhoto',
                data={'chat_id': chat_id, 'caption': caption},
                files={'photo': f}, timeout=30
            )
        if r.status_code != 200:
            print(f"{YELLOW}  Telegram HTTP {r.status_code}: {r.text[:200]}{NC}")
    except Exception as e:
        print(f"{YELLOW}  Telegram Fehler: {e}{NC}")


# ─── Dark-Theme ───────────────────────────────────────────────────────────────

BG_DARK  = '#0f172a'
BG_PANEL = '#1e293b'
COL_TEXT = '#94a3b8'
COL_GRID = '#334155'

def style_fig(fig):
    fig.patch.set_facecolor(BG_DARK)

def style_axes(*axes):
    for ax in axes:
        ax.set_facecolor(BG_PANEL)
        ax.tick_params(colors=COL_TEXT)
        ax.spines[:].set_color(COL_GRID)
        ax.grid(True, alpha=0.15, color='#475569')
        ax.xaxis.label.set_color(COL_TEXT)
        ax.yaxis.label.set_color(COL_TEXT)
        ax.title.set_color('white')
        for label in ax.get_xticklabels() + ax.get_yticklabels():
            label.set_color(COL_TEXT)
        legend = ax.get_legend()
        if legend:
            legend.get_frame().set_facecolor(BG_PANEL)
            legend.get_frame().set_edgecolor(COL_GRID)
            for text in legend.get_texts():
                text.set_color(COL_TEXT)


# ─── save_send ────────────────────────────────────────────────────────────────

def save_send(fig, name, caption='', no_telegram=False):
    """Speichert Chart als PNG + sendet via Telegram (wie dnabot).
    Wendet Dark-Theme automatisch auf alle Axes an.
    """
    import matplotlib.pyplot as plt
    os.makedirs(DOCS_DIR, exist_ok=True)
    os.makedirs(TMP_DIR, exist_ok=True)

    # Auto Dark-Theme — gilt fuer alle Skripts die save_send nutzen
    style_fig(fig)
    for ax in fig.get_axes():
        style_axes(ax)

    tmp_path  = os.path.join(TMP_DIR,  f'titanbot_{name}.png')
    docs_path = os.path.join(DOCS_DIR, f'{name}_latest.png')

    fig.savefig(tmp_path,  dpi=150, bbox_inches='tight', facecolor=BG_DARK)
    fig.savefig(docs_path, dpi=150, bbox_inches='tight', facecolor=BG_DARK)
    plt.close(fig)
    print(f"  {G}✓ Chart gespeichert: {tmp_path}{NC}")

    if not no_telegram:
        token, chat_id = get_telegram()
        if token:
            _send_photo(token, chat_id, tmp_path, caption)
            print(f"  {G}✓ Via Telegram gesendet.{NC}")
        else:
            print(f"  {YELLOW}⚠ Kein Telegram-Token — nur lokal gespeichert.{NC}")


# Rückwärts-Kompatibilität: alter Name
def send_chart_telegram(fig, caption, no_telegram=False, **_):
    save_send(fig, 'chart', caption=caption, no_telegram=no_telegram)
