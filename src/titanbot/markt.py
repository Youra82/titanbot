"""Marktdaten (öffentlich), Papierkonto und Echtgeld-Hilfen für titanbot (Claude, 21.11.2026).
Übernommen aus kapbot (Stand 63f0328, dort aus dem evobot); titanbot importiert nichts aus anderen Bots."""
from __future__ import annotations
import json, os, time, urllib.request
from pathlib import Path


SPREAD = 0.0005                  # je Seite (Papier)
FEE = 0.0006                     # Bitget-Taker-Gebühr je Seite
LIQ_BUFFER = 0.01                # Liquidation, wenn das Tief ≤ −(1/Hebel − 1 %) unter dem Einstieg liegt
MIN_NOTIONAL = 5.0
FUNDING_MAX_VERSUCHE = 6         # Papier-Ausstieg wartet so viele Läufe auf die Funding-Sätze, danach ohne
BITGET_SYMBOL = {'1000PEPE': 'PEPE', '1000SHIB': 'SHIB'}


def _get(url, tries=4):
    for i in range(tries):
        try:
            return json.loads(urllib.request.urlopen(url, timeout=20).read())
        except Exception:
            if i == tries - 1:
                raise
            time.sleep(1 + 2 * i)


def binance_closes(sym, interval, limit):
    """Abgeschlossene Binance-USDT-M-Kerzen: [(open_ms, close)]."""
    rows = _get(f'https://fapi.binance.com/fapi/v1/klines?symbol={sym}USDT&interval={interval}&limit={limit}')
    now = int(time.time() * 1000)
    return [(int(r[0]), float(r[4])) for r in rows if int(r[6]) < now]


def bitget_open_at(coin, t_ms):
    """Eröffnungskurs der Bitget-1m-Kerze, die bei t_ms beginnt (oder der nächsten verfügbaren innerhalb 5 min)."""
    sym = BITGET_SYMBOL.get(coin, coin) + 'USDT'
    d = _get('https://api.bitget.com/api/v2/mix/market/history-candles?symbol=' + sym +
             f'&productType=USDT-FUTURES&granularity=1m&startTime={t_ms - 60_000}&endTime={t_ms + 5 * 60_000}&limit=10')
    rows = sorted((int(r[0]), float(r[1])) for r in (d.get('data') or []))
    for ts, op in rows:
        if t_ms <= ts <= t_ms + 5 * 60_000:
            return op
    return None


def bitget_min_low(coin, t_from, t_to):
    """Tiefster Kurs der Bitget-1h-Kerzen zwischen t_from und t_to (Liquidationsprüfung). None bei fehlenden Daten."""
    sym = BITGET_SYMBOL.get(coin, coin) + 'USDT'
    d = _get('https://api.bitget.com/api/v2/mix/market/history-candles?symbol=' + sym +
             f'&productType=USDT-FUTURES&granularity=1H&startTime={t_from}&endTime={t_to}&limit=48')
    lows = [float(r[3]) for r in (d.get('data') or []) if t_from <= int(r[0]) < t_to]
    return min(lows) if lows else None


def bitget_funding_anteil(symbol, von_ms, bis_ms, side=1):
    """Funding zwischen Einstieg und Ausstieg als Anteil am Positionswert: Summe der echten Bitget-Sätze in (von_ms, bis_ms].
    Satz > 0: Long zahlt. side=1 Long (Rückgabe −Summe), side=−1 Short (+Summe)."""
    saetze = {}
    for n in range(1, 11):
        d = _get('https://api.bitget.com/api/v2/mix/market/history-fund-rate?symbol=' + symbol +
                 f'&productType=USDT-FUTURES&pageSize=100&pageNo={n}')
        if str(d.get('code')) != '00000':
            raise RuntimeError(f"Bitget Funding {symbol}: {d.get('msg')}")
        rows = d.get('data') or []
        saetze.update((int(x['fundingTime']), float(x['fundingRate'])) for x in rows)
        if len(rows) < 100 or min(int(x['fundingTime']) for x in rows) <= von_ms:
            break
    return -side * sum(r for t, r in saetze.items() if von_ms < t <= bis_ms)


class Konto:
    """Papier- bzw. Echtgeld-Buchführung (JSON). Format wie das evobot-Konto, damit das Cockpit es als 'evo' liest."""
    def __init__(self, path: Path):
        self.path = path
        self.s = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {
            'start': 100.0, 'cash': 100.0, 'positions': [], 'closed': [], 'busy': {}}

    def save(self):
        tmp = self.path.with_suffix('.tmp')
        tmp.write_text(json.dumps(self.s, indent=1), encoding='utf-8')
        os.replace(tmp, self.path)

    def units(self):
        return self.s.setdefault('units', self.s['start'])

    def einzahlung(self, amount, t_ms=None):
        """Ein- (+) oder Auszahlung (−) zum aktuellen Anteilswert (einzahlungsneutral)."""
        self.s['units'] = self.units() + amount / (self.equity() / self.units())
        self.s['cash'] += amount
        self.s.setdefault('einzahlungen', []).append({'t_ms': t_ms or int(time.time() * 1000), 'betrag': amount})

    def equity(self):
        return (self.s['cash'] + sum(p['stake'] for p in self.s['positions'])
                + sum(v['stake'] for v in self.s.get('pending_live', [])))  # reservierte Margin unklarer Orders


# ---------- Echtgeld (nur mit Bitget-Zugang aus secret.json['titanbot']) ----------
def live_sync(k, live, now_ms, log):
    """Start aus dem Bitget-Guthaben, danach jede Ein-/Auszahlung als Konto.einzahlung (jede Buchung genau einmal)."""
    if 'live_since' not in k.s:
        if k.s['positions'] or k.s.get('pending_live'):
            raise SystemExit('Echtgeld braucht ein eigenes, leeres Konto (data/echtgeld)')
        bal = float(live.account().get('available') or 0)
        if bal < MIN_NOTIONAL:
            raise SystemExit(f'Bitget-Futures-Guthaben {bal:.2f} USDT zu klein')
        k.s.update(start=bal, cash=bal, units=bal, live_since=now_ms, seen_bills=[])
        log.append(f'Echtgeld-Start mit {bal:.2f} USDT')
        return
    seen = set(k.s.setdefault('seen_bills', []))
    for bid, t, amt in live.transfers(k.s['live_since'], now_ms):
        if bid in seen:
            continue
        seen.add(bid)
        k.einzahlung(amt, t)
        k.s['seen_bills'].append(bid)
        log.append(f"{'Einzahlung' if amt > 0 else 'Auszahlung'} {amt:+.2f} USDT gebucht")


# ---------- Echtgeld-Einstieg: Absicht VOR dem Senden sichern ----------
PENDING_FREIGABE_MS = 15 * 60_000     # unauffindbare Order frühestens nach 15 min und 3 leeren Abfragen freigeben
LIQ_VERDACHT_MS = 10 * 60_000         # fehlende Menge ohne eigenen Close erst nach 10 min als Liquidation buchen


def _eigene_menge(k, sym):
    from . import bitget as BG
    return sum(q.get('size', 0.0) for q in k.s['positions'] if q.get('live') and BG.symbol(q['coin']) == sym)


def live_kauf(k, live, art, coin, t_sig, t_in, stake, lev, meta, log):
    """Echtgeld-Einstieg Long per Markt. Ablauf: Prüfen → Vormerkung (clientOid aus dem SIGNAL, Margin reserviert) speichern →
    senden → Ergebnis buchen. Bricht der Prozess nach dem Senden ab, findet der nächste Lauf die Order über die Vormerkung.
    Rückgabe: gebuchte Position, 'vorgemerkt' oder None (kein Trade)."""
    from . import bitget as BG
    sym = BG.symbol(coin)
    oid = BG.client_oid(art, coin, t_sig, 'open')
    if oid in k.s.get('oids', []):
        log.append(f'{coin}: Order {oid} wurde schon gebucht – kein zweites Mal'); return None
    c = live.contract(sym)
    if not c or c.get('symbolStatus', 'normal') != 'normal':
        log.append(f'{sym} auf Bitget nicht handelbar'); return None
    ref = live.last_price(sym)
    size = BG.size_for(stake, lev, ref or 0, c)
    if size is None:
        log.append(f'{sym}: Margin {stake:.2f} zu klein für Mindestmenge'); return None
    prep = k.s.setdefault('prepared', {})
    if prep.get(sym) != lev:
        live.prepare(sym, lev)
        prep[sym] = lev
    v = {'coin': coin, 't': t_sig, 't_in': t_in, 'stake': stake, 'lev': lev, 'oid': oid, 'size': size, 'meta': meta, 'leer': 0}
    k.s.setdefault('pending_live', []).append(v)
    k.s['cash'] -= stake                                   # Margin reservieren
    k.save()                                               # VOR dem Senden
    try:
        o = live.market(sym, 'long', size, oid)
    except Exception as e:
        log.append(f'Einstieg {coin}: {type(e).__name__} – vorgemerkt, nächster Lauf prüft {oid}'); return 'vorgemerkt'
    return _pending_buchen(k, v, o, log)


def _pending_buchen(k, v, o, log):
    """Order-Zustand einer Vormerkung buchen: (Teil-)Füllung → Position, 0 gefüllt → Reservierung zurück, sonst 'vorgemerkt'."""
    from . import bitget as BG
    state = (o or {}).get('state')
    if o is None or state not in ('filled', 'canceled', 'cancelled'):
        if o is not None:
            log.append(f"Einstieg {v['coin']}: Order noch offen – vorgemerkt")
        return 'vorgemerkt'
    k.s['pending_live'].remove(v)
    px, q, fee = BG.partial(o)
    if q <= 0:
        k.s['cash'] += v['stake']; k.save()
        log.append(f"Einstieg {v['coin']} nicht gefüllt – Reservierung zurück"); return None
    real = px * q / v['lev']                               # genau die gefüllte Margin
    k.s['cash'] += v['stake'] - real
    pos = {**v['meta'], 'coin': v['coin'], 'signal_ms': v['t'], 'entry_ms': v['t_in'], 'entry_px': px,
           'entry_eff': px * (1 + SPREAD) * (1 + FEE), 'stake': real, 'lev': v['lev'], 'size': q, 'size0': q, 'fee_in': fee,
           'live': True, 'oid_open': v['oid']}
    if state != 'filled':
        log.append(f"Einstieg {v['coin']} nur teilweise gefüllt ({q}) – gebucht wird die gefüllte Menge")
    k.s['positions'].append(pos)
    k.s.setdefault('oids', []).append(v['oid']); del k.s['oids'][:-1000]
    k.save()
    return pos


def resolve_pending(k, live, now_ms, log):
    """Vormerkungen per clientOid nachschlagen. Unauffindbar: erst nach 15 min, 3 leeren Abfragen und ohne unerklärte
    Börsenmenge freigeben (Order hat Bitget nie erreicht); sonst vorgemerkt lassen und warnen. Rückgabe: neu gebuchte Positionen."""
    from . import bitget as BG
    neu = []
    for v in list(k.s.get('pending_live', [])):
        sym = BG.symbol(v['coin'])
        try:
            o = live.order(sym, v['oid'])
        except Exception as e:
            log.append(f"Vormerkung {v['coin']}: {type(e).__name__}"); continue
        if o is None:
            v['leer'] = v.get('leer', 0) + 1
            if v['leer'] >= 3 and now_ms - v['t_in'] >= PENDING_FREIGABE_MS:
                try:
                    have = live.positions().get((sym, 'long'), 0.0)
                except Exception:
                    continue
                if have <= _eigene_menge(k, sym) + 1e-9:
                    k.s['pending_live'].remove(v); k.s['cash'] += v['stake']
                    log.append(f"Einstieg {v['coin']}: Order nie bei Bitget angekommen – Reservierung zurück")
                elif not v.get('gewarnt'):
                    v['gewarnt'] = True
                    log.append(f"WARNUNG {sym}: Vormerkung ohne auffindbare Order, aber unerklärte Long-Menge {have} – bitte prüfen")
            k.save(); continue
        r = _pending_buchen(k, v, o, log)
        if isinstance(r, dict):
            neu.append(r)
    return neu


# ---------- Echtgeld-Ausstieg ----------
def live_exit(k, live, p, now_ms, log):
    """Eigene Restmenge per Markt schließen. Reihenfolge: eigene Close-Order nachschlagen → bei fehlender Börsenmenge ohne
    eigenen Close erst nach LIQ_VERDACHT_MS als Liquidation werten → sonst Close senden.
    Rückgabe ((Ø-Kurs, Menge, Gebühr) über alle Teil-Closes, False) | (None, True) liquidiert | ('offen', False)."""
    from . import bitget as BG
    sym = BG.symbol(p['coin'])
    zweck = 'close' + (str(p['close_try']) if p.get('close_try') else '')
    oid = BG.client_oid(p.get('genom', p['art']), p['coin'], p['entry_ms'], zweck)
    o = live.order(sym, oid)
    if o is not None and o.get('state') in ('canceled', 'cancelled'):
        part = BG.partial(o)
        if part[1] > 0:
            p['size'] = round(p['size'] - part[1], 10)
            p.setdefault('teil_closes', []).append(list(part))
        p['close_try'] = p.get('close_try', 0) + 1
        k.save()
        if p['size'] <= 1e-12:
            return _gesamt(p, None), False
        log.append(f"Ausstieg {p['coin']}: Close storniert – neuer Versuch im nächsten Lauf"); return 'offen', False
    if o is not None and o.get('state') == 'filled':
        return _gesamt(p, BG.fill(o)), False
    if o is None:
        have = live.positions().get((sym, 'long'), 0.0)
        if have + 1e-9 < _eigene_menge(k, sym):
            seit = p.setdefault('liq_verdacht_ms', now_ms); k.save()
            if now_ms - seit < LIQ_VERDACHT_MS:
                log.append(f"Ausstieg {p['coin']}: Börsenmenge fehlt – prüfe im nächsten Lauf erneut"); return 'offen', False
            return None, True
        p.pop('liq_verdacht_ms', None)
        k.save()
        o = live.market(sym, 'long', p['size'], oid, close=True)
    f = BG.fill(o)
    if f is None:
        log.append(f"Ausstieg {p['coin']} nicht bestätigt – nächster Lauf prüft erneut"); return 'offen', False
    return _gesamt(p, f), False


def _gesamt(p, f):
    parts = [tuple(x) for x in p.get('teil_closes', [])] + ([tuple(f)] if f else [])
    q = sum(x[1] for x in parts)
    return (sum(x[0] * x[1] for x in parts) / q, q, sum(x[2] for x in parts))


def live_liquidation_r(p):
    """Rendite (bezogen auf stake × lev) bei liquidiertem Rest: bereits realisierte Teil-Closes zählen, der Rest verliert seine Margin."""
    q0 = p.get('size0') or (p.get('size', 0) + sum(x[1] for x in p.get('teil_closes', [])))
    teil = p.get('teil_closes', []); qt = sum(x[1] for x in teil)
    rest = max(0.0, 1 - qt / q0) if q0 else 1.0
    pnl = sum((x[0] - p['entry_px']) * x[1] - x[2] for x in teil) - p.get('fee_in', 0.0) * (qt / q0 if q0 else 0) - p['stake'] * rest
    return pnl / (p['stake'] * p.get('lev', 1))
