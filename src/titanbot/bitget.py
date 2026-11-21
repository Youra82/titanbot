"""Bitget-Ausführung für den Arten-Dienst (Crash-Long/Pump-Long) – STANDARD AUS (Claude, 12.11.2026).

Schreibende Aufrufe nur mit enabled=True (CLI `--live`, Nutzerfreigabe). Ohne Freigabe wirft jeder POST LiveDisabled.
- Hedge-Modus, isolierte Margin, Hebel je Art; Markt-Einstieg und Markt-Ausstieg mit exakt der eigenen Stückzahl
  (Crash und Pump auf demselben Coin teilen sich die Long-Seite, jede Position schließt nur ihre eigene Menge).
- Kein blinder Wiederholungsversuch: Jede Order hat eine feste clientOid (aus Art, Coin, Zeit, Zweck). Vor dem Senden
  und nach einem Netzfehler wird per clientOid nachgesehen, ob die Order schon existiert.
- Ein-/Auszahlungen: Kontobuchungen mit Transfer-Typen → Konto.einzahlung (Kontokurven-Regel bleibt einzahlungsneutral).
- Keine Auszahlungs-, Transfer- oder „alles schließen“-Pfade.
"""
from __future__ import annotations
import base64, hashlib, hmac, json, math, time, urllib.error, urllib.parse, urllib.request

HOST = 'https://api.bitget.com'
PRODUCT = 'USDT-FUTURES'
WRITE_PATHS = {'/api/v2/mix/account/set-position-mode', '/api/v2/mix/account/set-margin-mode',
               '/api/v2/mix/account/set-leverage', '/api/v2/mix/order/place-order', '/api/v2/mix/order/cancel-order'}
# Futures-Kontobuchungen, die Ein-/Auszahlungen bzw. Umbuchungen sind (Vorzeichen steckt in 'amount')
TRANSFER_TYPES = {'trans_from_exchange', 'trans_to_exchange', 'trans_from_cross', 'trans_to_cross', 'trans_from_isolated',
                  'trans_to_isolated', 'trans_from_contract', 'trans_to_contract', 'trans_from_otc', 'trans_to_otc',
                  'transfer_in', 'transfer_out', 'deposit', 'withdraw'}


class LiveDisabled(RuntimeError):
    pass


class ExchangeError(RuntimeError):
    pass


class NetworkUnknown(RuntimeError):
    """POST ohne verlässliche Antwort – Ergebnis unbekannt, per clientOid nachsehen, NICHT neu senden."""


def client_oid(art, coin, t_ms, zweck):
    return 'tit' + hashlib.sha256(f'{art}|{coin}|{t_ms}|{zweck}'.encode()).hexdigest()[:29]


def symbol(coin):
    return {'1000PEPE': 'PEPE', '1000SHIB': 'SHIB'}.get(coin, coin) + 'USDT'


def size_for(margin, lev, price, c):
    """Stückzahl (abgerundet auf Schrittweite) für Margin × Hebel bei Kurs; None, wenn unter Mindestmenge/-wert."""
    if margin <= 0 or price <= 0:
        return None
    step = float(c['sizeMultiplier']); places = int(c['volumePlace'])
    q = math.floor(margin * lev / price / step + 1e-9) * step
    q = round(q, places)
    if q < float(c['minTradeNum']) or q * price < float(c.get('minTradeUSDT') or 5):
        return None
    return q


class Bitget:
    def __init__(self, cred: dict | None, *, enabled=False, opener=None, clock_ms=None):
        self.cred, self.enabled = cred, bool(enabled)
        self.opener = opener or urllib.request.build_opener()
        self.clock = clock_ms or (lambda: int(time.time() * 1000))
        self._contracts = None

    # --- Transport -------------------------------------------------------------------------------------------
    def _headers(self, method, path_q, body=''):
        h = {'Content-Type': 'application/json', 'locale': 'en-US'}
        if self.cred:
            ts = str(self.clock())
            sig = base64.b64encode(hmac.new(self.cred['secret'].encode(), (ts + method + path_q + body).encode(),
                                            hashlib.sha256).digest()).decode()
            h.update({'ACCESS-KEY': self.cred['apiKey'], 'ACCESS-SIGN': sig, 'ACCESS-TIMESTAMP': ts,
                      'ACCESS-PASSPHRASE': self.cred['password']})
        return h

    def _send(self, req):
        try:
            with self.opener.open(req, timeout=20) as r:
                d = json.loads(r.read())
        except urllib.error.HTTPError as e:                 # Bitget meldet Ablehnungen als HTTP 4xx mit JSON-Code
            try:
                d = json.loads(e.read())
            except Exception:
                raise urllib.error.URLError(f'HTTP {e.code} ohne Bitget-Code') from None   # Ergebnis unklar
            if not isinstance(d, dict) or 'code' not in d:
                raise urllib.error.URLError(f'HTTP {e.code} ohne Bitget-Code') from None
            raise ExchangeError(f"Bitget lehnt ab: code {d.get('code')} {str(d.get('msg', ''))[:80]}") from None
        if not isinstance(d, dict) or d.get('code') != '00000':
            raise ExchangeError(f"Bitget lehnt ab: code {d.get('code') if isinstance(d, dict) else '?'} {str(d.get('msg', ''))[:80]}")
        return d.get('data')

    def get(self, path, **q):
        path_q = path + ('?' + urllib.parse.urlencode(sorted(q.items())) if q else '')
        req = urllib.request.Request(HOST + path_q, headers=self._headers('GET', path_q), method='GET')
        for i in range(3):                                  # Lesen darf wiederholt werden
            try:
                return self._send(req)
            except (urllib.error.URLError, TimeoutError, OSError):
                if i == 2: raise
                time.sleep(1 + i)

    def post(self, path, body: dict):
        if path not in WRITE_PATHS:
            raise ExchangeError('Pfad nicht freigegeben')
        if not self.enabled:
            raise LiveDisabled('echte Orders nicht freigegeben (--live fehlt)')
        b = json.dumps(body, separators=(',', ':'))
        req = urllib.request.Request(HOST + path, data=b.encode(), headers=self._headers('POST', path, b), method='POST')
        try:
            return self._send(req)
        except ExchangeError:
            raise
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise NetworkUnknown(type(e).__name__) from None

    # --- Lesen -----------------------------------------------------------------------------------------------
    def contract(self, sym):
        if self._contracts is None:
            self._contracts = {c['symbol']: c for c in self.get('/api/v2/mix/market/contracts', productType=PRODUCT) or []}
        return self._contracts.get(sym)

    def last_price(self, sym):
        d = self.get('/api/v2/mix/market/ticker', symbol=sym, productType=PRODUCT) or []
        return float(d[0]['lastPr']) if d else None

    def book(self, sym):
        """(bester Geldkurs, bester Briefkurs)"""
        d = self.get('/api/v2/mix/market/ticker', symbol=sym, productType=PRODUCT) or []
        return (float(d[0]['bidPr']), float(d[0]['askPr'])) if d else (None, None)

    def positions(self):
        """{(symbol, holdSide): Stückzahl}"""
        d = self.get('/api/v2/mix/position/all-position', productType=PRODUCT, marginCoin='USDT') or []
        return {(p['symbol'], p['holdSide']): float(p['total']) for p in d if float(p.get('total') or 0) > 0}

    def account(self):
        d = self.get('/api/v2/mix/account/accounts', productType=PRODUCT) or []
        return next((a for a in d if a.get('marginCoin') == 'USDT'), {})

    def order(self, sym, oid):
        """Order per clientOid oder None, wenn es sie nicht gibt."""
        try:
            return self.get('/api/v2/mix/order/detail', symbol=sym, productType=PRODUCT, clientOid=oid)
        except ExchangeError as e:
            if any(x in str(e) for x in ('40109', '40768')) or 'not exist' in str(e).lower():
                return None
            raise

    def transfers(self, since_ms, until_ms=None):
        """Ein-/Auszahlungen [(billId, t_ms, betrag)] seit since_ms (Bitget-Kontobuchungen, Typen TRANSFER_TYPES)."""
        until_ms = until_ms or self.clock(); out = []; t0 = since_ms
        ids = set()
        while t0 < until_ms:
            t1 = min(until_ms, t0 + 30 * 86_400_000); last = None; cursors = set()
            while True:
                q = dict(productType=PRODUCT, startTime=t0, endTime=t1, limit=100)
                if last: q['idLessThan'] = last
                d = self.get('/api/v2/mix/account/bill', **q) or {}
                bills = d.get('bills') or []
                for b in bills:
                    if b.get('businessType') in TRANSFER_TYPES and b.get('coin', 'USDT') == 'USDT' and b['billId'] not in ids:
                        ids.add(b['billId']); out.append((b['billId'], int(b['cTime']), float(b['amount'])))
                nxt = d.get('endId')
                if len(bills) < 100 or not nxt or nxt in cursors: break
                cursors.add(nxt); last = nxt
            t0 = t1
        return sorted(out, key=lambda x: x[1])

    # --- Schreiben (nur enabled) ---------------------------------------------------------------------------------
    def prepare(self, sym, lev):
        self.post('/api/v2/mix/account/set-margin-mode', dict(symbol=sym, productType=PRODUCT, marginCoin='USDT', marginMode='isolated'))
        for side in ('long', 'short'):
            self.post('/api/v2/mix/account/set-leverage', dict(symbol=sym, productType=PRODUCT, marginCoin='USDT',
                                                               leverage=str(lev), holdSide=side))

    def ensure_hedge_mode(self):
        if self.account().get('posMode') != 'hedge_mode':
            self.post('/api/v2/mix/account/set-position-mode', dict(productType=PRODUCT, posMode='hedge_mode'))

    def market(self, sym, hold, size, oid, *, close=False):
        """Markt-Order Eröffnen/Schließen im Hedge-Modus. Gibt die Order (Details) zurück; nie doppelt senden."""
        existing = self.order(sym, oid)
        if existing is None:
            side = 'buy' if hold == 'long' else 'sell'
            try:
                self.post('/api/v2/mix/order/place-order', dict(
                    symbol=sym, productType=PRODUCT, marginMode='isolated', marginCoin='USDT', size=str(size), side=side,
                    tradeSide='close' if close else 'open', orderType='market', clientOid=oid))
            except NetworkUnknown:
                pass                                         # Ergebnis unbekannt → unten nachsehen, nicht neu senden
            for _ in range(10):
                existing = self.order(sym, oid)
                if existing and existing.get('state') in ('filled', 'canceled', 'cancelled'): break
                time.sleep(0.5)
        return existing


    def limit_close(self, sym, hold, size, price, oid):
        """Post-only-Limit zum Schließen (Maker). Gibt False zurück, wenn Bitget sie ablehnt (würde sofort ausführen)."""
        if self.order(sym, oid) is not None:
            return True
        side = 'buy' if hold == 'long' else 'sell'
        try:
            self.post('/api/v2/mix/order/place-order', dict(
                symbol=sym, productType=PRODUCT, marginMode='isolated', marginCoin='USDT', size=str(size), side=side,
                tradeSide='close', orderType='limit', force='post_only', price=str(price), clientOid=oid))
        except ExchangeError:
            return False
        except NetworkUnknown:
            pass
        return True

    def cancel(self, sym, oid):
        try:
            self.post('/api/v2/mix/order/cancel-order', dict(symbol=sym, productType=PRODUCT, clientOid=oid))
        except ExchangeError:
            pass                                             # schon gefüllt oder nicht vorhanden


def round_price(px, c, up):
    """Auf die Preisschritte des Kontrakts runden (beim Verkauf aufrunden, beim Kauf abrunden)."""
    places = int(c.get('pricePlace') or 6); step = float(c.get('priceEndStep') or 1) * 10 ** -places
    q = (math.ceil if up else math.floor)(px / step - 1e-9 if up else px / step + 1e-9) * step
    return round(q, places)


def partial(order):
    """(Ø-Kurs, gefüllte Menge, Gebühr) einer auch teilweise gefüllten Order; Menge 0 wenn nichts gefüllt."""
    if not order:
        return 0.0, 0.0, 0.0
    q = float(order.get('baseVolume') or 0)
    return (float(order.get('priceAvg') or 0) if q else 0.0), q, abs(float(order.get('fee') or 0))


def fill(order):
    """(Durchschnittspreis, Menge, Gebühr in USDT ≥ 0) einer gefüllten Order oder None."""
    if not order or order.get('state') != 'filled':
        return None
    return float(order['priceAvg']), float(order['baseVolume']), abs(float(order.get('fee') or 0))
