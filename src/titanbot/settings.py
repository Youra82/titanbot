"""titanbot-Schalter aus settings.json (Projektwurzel). Fehlt die Datei oder ein Eintrag, gelten sichere Standardwerte (Papier)."""
from __future__ import annotations
import json, os
from pathlib import Path


def _root() -> Path:
    """Projektordner mit settings.json/data/secret.json: TITANBOT_ROOT (Dienstdatei) → Arbeitsverzeichnis → Quellbaum."""
    env = os.environ.get('TITANBOT_ROOT')
    if env and (Path(env) / 'settings.json').exists():
        return Path(env)
    if (Path.cwd() / 'settings.json').exists():
        return Path.cwd()
    return Path(__file__).resolve().parents[2]


ROOT = _root()
DEFAULTS = dict(titanbot=False, einsatz_stark=0.0625, einsatz_normal=0.0375, hebel=1, max_offen=20,
                echtgeld=False, papier_start=100.0)


def _num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def load(path: Path | None = None) -> dict:
    out = dict(DEFAULTS)
    try:
        data = json.loads((path or ROOT / 'settings.json').read_text(encoding='utf-8-sig'))
    except (OSError, ValueError):
        return out
    tb = data.get('titanbot') if isinstance(data.get('titanbot'), dict) else {}
    out['titanbot'] = tb.get('aktiv') is True
    es, en, h, m = tb.get('einsatz_oi_stark'), tb.get('einsatz_normal'), tb.get('hebel'), tb.get('max_offen')
    if _num(es) and 0 < es <= 0.5: out['einsatz_stark'] = float(es)
    if _num(en) and 0 < en <= 0.5: out['einsatz_normal'] = float(en)
    if _num(h) and int(h) == h and 1 <= h <= 5: out['hebel'] = int(h)
    if _num(m) and int(m) == m and 1 <= m <= 60: out['max_offen'] = int(m)
    eg = data.get('echtgeld') if isinstance(data.get('echtgeld'), dict) else {}      # wie jaegerbot
    out['echtgeld'] = eg.get('aktiv') is True
    ps = eg.get('papier_startkapital')
    if _num(ps) and 5 <= ps <= 1e7: out['papier_start'] = float(ps)
    return out
