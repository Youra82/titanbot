"""titanbot (SMC-Raid): Regel gegen die pandas-Definition der Forschung und Dienstablauf offline (keine Netz- oder Order-Aufrufe)."""
import math, random, tempfile, unittest
from pathlib import Path
from unittest import mock

from titanbot import regel as R, bot as K


def walk(n, seed, vol=0.002, drift=0.0):
    rng = random.Random(seed); c = [100.0]
    for _ in range(n - 1):
        c.append(c[-1] * math.exp(drift + rng.gauss(0, vol)))
    return c


class RuleTests(unittest.TestCase):
    def test_z_matches_pandas(self):
        import numpy as np, pandas as pd
        c = walk(9000, 1)
        lc = np.log(np.array(c)); r = np.full(len(c), np.nan); r[3:] = lc[3:] - lc[:-3]
        ref = (r / pd.Series(r).rolling(R.SIG_N, min_periods=R.SIG_MIN).std().values)[-1]
        self.assertAlmostEqual(R.z_flush(c), ref, places=9)

    def test_z_needs_min_window(self):
        self.assertIsNone(R.z_flush(walk(R.SIG_MIN + 2, 2)))
        self.assertIsNotNone(R.z_flush(walk(R.SIG_MIN + 3, 2)))

    def test_dd_and_oi(self):
        c = [100.0] * R.DD_MIN + [79.0]
        self.assertAlmostEqual(R.dd90(c), -0.21)
        self.assertIsNone(R.dd90(c[-100:]))
        self.assertAlmostEqual(R.oi_drop([100.0] * 71 + [96.0]), -0.04)
        self.assertIsNone(R.oi_drop([100.0] * 71 + [None]))
        self.assertIsNone(R.oi_drop([None] * 40 + [100.0] * 32))     # nur 32 Werte < 36

    def test_retail(self):
        self.assertTrue(R.retail_falling(2.0, 2.5))
        self.assertFalse(R.retail_falling(2.5, 2.0))
        self.assertFalse(R.retail_falling(None, 2.0))

    def test_coins(self):
        self.assertEqual(len(R.COINS), 60); self.assertEqual(len(set(R.COINS)), 60)

    def test_einsatz_nach_oi(self):
        self.assertEqual(R.einsatz(-0.05), 0.0625); self.assertEqual(R.einsatz(-0.08), 0.0625)
        self.assertEqual(R.einsatz(-0.049), 0.0375); self.assertEqual(R.einsatz(None), 0.0375)

    @staticmethod
    def crash_series():
        c = [200.0] * 9000 + walk(18000, 3, vol=0.002, drift=-0.00004)      # langer Abwärtstrend
        c[-1] = c[-4] * math.exp(-0.06)                                      # 15-min-Raid −6 %
        return c

    def test_signal_all_parts_needed(self):
        c = self.crash_series()
        ok, f = R.signal(c, [100.0] * 71 + [95.0], 2.0, 2.4)
        self.assertTrue(ok, f)
        self.assertFalse(R.signal(c, [100.0] * 71 + [98.0], 2.0, 2.4)[0])   # OI nur −2 %
        self.assertFalse(R.signal(c, [100.0] * 71 + [95.0], 2.4, 2.0)[0])   # Kleinanleger steigen
        flat = c[:-1] + [c[-4] * math.exp(-0.001)]
        self.assertFalse(R.signal(flat, [100.0] * 71 + [95.0], 2.0, 2.4)[0])  # kein Raid


class FakeLive:
    """Simulierte Börse (keine Netz- oder Order-Aufrufe)."""
    C = {'symbol': 'SOLUSDT', 'sizeMultiplier': '0.1', 'volumePlace': '1', 'minTradeNum': '0.1', 'minTradeUSDT': '5',
         'symbolStatus': 'normal'}

    def __init__(self, bal=200.0, price=50.0):
        self.bal, self.price, self.pos, self.orders = bal, price, {}, {}

    def account(self): return {'available': str(self.bal), 'posMode': 'hedge_mode'}
    def transfers(self, since, until): return []
    def contract(self, sym): return dict(self.C, symbol=sym)
    def last_price(self, sym): return self.price
    def prepare(self, sym, lev): pass
    def positions(self): return dict(self.pos)
    def order(self, sym, oid): return self.orders.get(oid)

    def market(self, sym, hold, size, oid, close=False):
        if oid in self.orders:
            return self.orders[oid]
        self.pos[(sym, hold)] = self.pos.get((sym, hold), 0.0) + (-size if close else size)
        o = {'state': 'filled', 'priceAvg': str(self.price), 'baseVolume': str(size), 'fee': str(-size * self.price * 0.0006)}
        self.orders[oid] = o
        return o


class RunTests(unittest.TestCase):
    """Signal, Einstieg, Ausstieg 24 h später; Größe nach OI; Papier/Echtgeld-Schalter – mit ersetzten Datenquellen."""
    def setUp(self):
        self.oi_last = 95.0                                # OI −5 % → starker Abbau
        c = RuleTests.crash_series()
        self.bar = R.BAR_MS; self.t_sig = 1_800_000_000_000 // self.bar * self.bar
        rows = [(self.t_sig - (len(c) - 1 - i) * self.bar, v) for i, v in enumerate(c)]
        t_sig = self.t_sig
        self.patches = [mock.patch.object(R, 'COINS', ['SOL']),
                        mock.patch.object(K, 'klines_since', lambda coin, s, now: [r for r in rows if r[0] >= s]),
                        mock.patch.object(K, 'oi_window', lambda coin, t: [100.0] * 71 + [self.oi_last]),
                        mock.patch.object(K, 'ratio_at', lambda coin, t: 2.0 if t == t_sig else 2.4),
                        mock.patch.object(K, '_paper_px', lambda coin, t: 50.0),
                        mock.patch.object(K.A, 'bitget_funding_anteil', lambda *a, **k: 0.0)]
        for p in self.patches: p.start()
        self.addCleanup(lambda: [p.stop() for p in self.patches])

    @staticmethod
    def sw(echtgeld=False, aktiv=True, start=100.0, hebel=1, max_offen=20):
        return {'titanbot': aktiv, 'einsatz_stark': 0.0625, 'einsatz_normal': 0.0375, 'hebel': hebel, 'max_offen': max_offen,
                'echtgeld': echtgeld, 'papier_start': start}

    def test_paper_cycle(self):
        with tempfile.TemporaryDirectory() as d:
            base = Path(d); msgs = []
            now = self.t_sig + self.bar + 60_000
            log = K.run(base, now_ms=now, schalter=self.sw(start=200.0), hook=msgs.append)
            k = K.A.Konto(base / 'papier' / 'konto.json')
            self.assertEqual(len(k.s['positions']), 1, log)
            p = k.s['positions'][0]
            self.assertAlmostEqual(p['stake'], 12.5)                              # 6,25 % von 200
            self.assertEqual(p['exit_ms'], self.t_sig + (R.HOLD_BARS + 1) * self.bar)
            self.assertTrue(any(m.startswith('📝 PAPIER · titanbot: EINSTIEG SOL') for m in msgs), msgs)
            K.run(base, now_ms=now + self.bar, schalter=self.sw(start=200.0))     # kein zweiter Einstieg (Coin belegt)
            self.assertEqual(len(K.A.Konto(base / 'papier' / 'konto.json').s['positions']), 1)
            K.run(base, now_ms=p['exit_ms'] + 120_000, schalter=self.sw(start=200.0))
            k = K.A.Konto(base / 'papier' / 'konto.json')
            self.assertEqual(len(k.s['positions']), 0)
            r = k.s['closed'][0]['r']
            self.assertAlmostEqual(r, (1 - 0.0005) * (1 - 0.0006) / ((1 + 0.0005) * (1 + 0.0006)) - 1, places=9)
            self.assertAlmostEqual(k.s['cash'], 187.5 + 12.5 * (1 + r), places=9)

    def test_normal_oi_drop_smaller_stake(self):
        self.oi_last = 96.0                                # −4 %: Signal ja, aber normaler Abbau
        with tempfile.TemporaryDirectory() as d:
            K.run(Path(d), now_ms=self.t_sig + self.bar + 60_000, schalter=self.sw(start=200.0))
            p = K.A.Konto(Path(d) / 'papier' / 'konto.json').s['positions'][0]
            self.assertAlmostEqual(p['stake'], 7.5)
            self.assertEqual(p['einsatz_anteil'], 0.0375)

    def test_below_min_notional_skipped(self):
        self.oi_last = 96.0                                # 3,75 % von 100 = 3,75 USDT < 5 USDT Mindestwert
        with tempfile.TemporaryDirectory() as d:
            log = K.run(Path(d), now_ms=self.t_sig + self.bar + 60_000, schalter=self.sw())
            self.assertTrue(any('zu klein' in x for x in log), log)

    def test_max_offen(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.object(R, 'COINS', ['ETH', 'SOL']):   # beide Coins signalisieren
            log = K.run(Path(d), now_ms=self.t_sig + self.bar + 60_000, schalter=self.sw(max_offen=1))
            k = K.A.Konto(Path(d) / 'papier' / 'konto.json')
            self.assertEqual([p['coin'] for p in k.s['positions']], ['ETH'], log)
            self.assertTrue(any('SOL: schon 1 Positionen offen' in x for x in log), log)

    def test_switched_off_no_entries(self):
        with tempfile.TemporaryDirectory() as d:
            log = K.run(Path(d), now_ms=self.t_sig + self.bar + 60_000, schalter=self.sw(aktiv=False))
            self.assertTrue(any('ausgeschaltet' in x for x in log), log)
            self.assertEqual(K.A.Konto(Path(d) / 'papier' / 'konto.json').s['positions'], [])

    def test_live_without_access_does_nothing_and_reports_once(self):
        with tempfile.TemporaryDirectory() as d:
            msgs = []
            K.run(Path(d), now_ms=self.t_sig + self.bar + 60_000, schalter=self.sw(echtgeld=True), hook=msgs.append)
            K.run(Path(d), now_ms=self.t_sig + 2 * self.bar + 60_000, schalter=self.sw(echtgeld=True), hook=msgs.append)
            self.assertEqual(sum('keinen Bitget-Zugang' in m for m in msgs), 1, msgs)
            self.assertFalse((Path(d) / 'echtgeld' / 'konto.json').exists())

    def test_live_entry_then_switch_to_paper_still_exits_live(self):
        with tempfile.TemporaryDirectory() as d:
            base = Path(d); lv = FakeLive(); msgs = []
            K.run(base, now_ms=1_000, schalter=self.sw(echtgeld=False), hook=msgs.append)          # Modus merken: Papier
            K.run(base, now_ms=self.t_sig + self.bar + 60_000, live=lv, schalter=self.sw(echtgeld=True), hook=msgs.append)
            self.assertTrue(any('umgeschaltet auf ECHTGELD' in m for m in msgs), msgs)
            k = K.A.Konto(base / 'echtgeld' / 'konto.json')
            self.assertEqual(len(k.s['positions']), 1)
            self.assertEqual(k.s['start'], 200.0)                                  # Echtgeld-Start = Bitget-Guthaben
            self.assertAlmostEqual(lv.pos[('SOLUSDT', 'long')], 0.2)               # 12,5 USDT / 50 → 0,25 → Schritt 0,1 → 0,2
            exit_ms = k.s['positions'][0]['exit_ms']
            K.run(base, now_ms=exit_ms + 120_000, live=lv, schalter=self.sw(echtgeld=False), hook=msgs.append)
            self.assertTrue(any('umgeschaltet auf PAPIER' in m for m in msgs), msgs)
            self.assertEqual(K.A.Konto(base / 'echtgeld' / 'konto.json').s['positions'], [])
            self.assertAlmostEqual(lv.pos[('SOLUSDT', 'long')], 0.0)

    def _live_entry(self, base, lv):
        K.run(base, now_ms=1_000, schalter=self.sw(echtgeld=False))
        K.run(base, now_ms=self.t_sig + self.bar + 60_000, live=lv, schalter=self.sw(echtgeld=True))
        return K.A.Konto(base / 'echtgeld' / 'konto.json')

    def test_close_already_filled_is_not_liquidation(self):
        """Close wurde ausgeführt, Antwort/Speicherung ging verloren → nächster Lauf findet die eigene Close-Order, keine Liquidation."""
        from titanbot import bitget as BG
        with tempfile.TemporaryDirectory() as d:
            base = Path(d); lv = FakeLive(); k = self._live_entry(base, lv)
            p = k.s['positions'][0]
            lv.price = 55.0
            lv.market('SOLUSDT', 'long', p['size'], BG.client_oid('titanbot', 'SOL', p['entry_ms'], 'close'), close=True)
            K.run(base, now_ms=p['exit_ms'] + 120_000, live=lv, schalter=self.sw(echtgeld=True))
            c = K.A.Konto(base / 'echtgeld' / 'konto.json').s['closed'][0]
            self.assertFalse(c['liquidated']); self.assertEqual(c['exit_px'], 55.0)
            self.assertAlmostEqual(c['r'], (5.0 * 0.2 - 0.2 * 50 * 0.0006 - 0.2 * 55 * 0.0006) / 10.0, places=9)   # gebucht: 0,2 SOL = 10 USDT

    def test_missing_without_close_is_liquidation(self):
        with tempfile.TemporaryDirectory() as d:
            base = Path(d); lv = FakeLive(); k = self._live_entry(base, lv)
            lv.pos[('SOLUSDT', 'long')] = 0.0                                     # Börse hat Position zwangsgeschlossen
            ex = k.s['positions'][0]['exit_ms']
            K.run(base, now_ms=ex + 120_000, live=lv, schalter=self.sw(echtgeld=True))
            self.assertEqual(K.A.Konto(base / 'echtgeld' / 'konto.json').s['closed'], [])   # erst Verdacht, noch kein Totalverlust
            K.run(base, now_ms=ex + 120_000 + 11 * 60_000, live=lv, schalter=self.sw(echtgeld=True))
            self.assertTrue(K.A.Konto(base / 'echtgeld' / 'konto.json').s['closed'][0]['liquidated'])

    def test_unclear_entry_is_reserved_then_booked(self):
        """Order-Ergebnis unklar → Margin reserviert, kein Doppelkauf; nächster Lauf bucht die bestätigte Order nach."""
        with tempfile.TemporaryDirectory() as d:
            base = Path(d); lv = FakeLive(); real = lv.market; pend = {}
            def slow(sym, hold, size, oid, close=False):
                if close or oid in lv.orders: return real(sym, hold, size, oid, close=close)
                pend[oid] = (sym, hold, size); return {'state': 'live'}              # noch offen
            lv.market = slow
            k = self._live_entry(base, lv)
            self.assertEqual(k.s['positions'], []); self.assertEqual(len(k.s['pending_live']), 1)
            self.assertAlmostEqual(k.equity(), 200.0)                              # Reservierung zählt zum Kontowert
            for oid, (sym, hold, size) in pend.items(): real(sym, hold, size, oid)  # Börse füllt
            K.run(base, now_ms=self.t_sig + 2 * self.bar + 60_000, live=lv, schalter=self.sw(echtgeld=True))
            k = K.A.Konto(base / 'echtgeld' / 'konto.json')
            self.assertEqual(len(k.s['positions']), 1); self.assertEqual(k.s['pending_live'], [])
            self.assertTrue(k.s['positions'][0]['nachgebucht'])
            self.assertAlmostEqual(lv.pos[('SOLUSDT', 'long')], 0.2)               # genau eine Order

    def test_unfilled_pending_releases_margin(self):
        with tempfile.TemporaryDirectory() as d:
            base = Path(d); lv = FakeLive(); real = lv.market
            lv.market = lambda sym, hold, size, oid, close=False: (real(sym, hold, size, oid, close=close) if close else
                                                                  lv.orders.setdefault(oid, {'state': 'live'}))
            k = self._live_entry(base, lv)
            for o in lv.orders.values(): o.update(state='canceled', baseVolume='0')
            K.run(base, now_ms=self.t_sig + 2 * self.bar + 60_000, live=lv, schalter=self.sw(echtgeld=True))
            k = K.A.Konto(base / 'echtgeld' / 'konto.json')
            self.assertEqual(k.s['pending_live'], []); self.assertAlmostEqual(k.s['cash'], 200.0)

    def test_full_stake_or_skip(self):
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            K.run(base, now_ms=1_000, schalter=self.sw(start=200.0))
            k = K.A.Konto(base / 'papier' / 'konto.json'); k.s['cash'] = 8.0; k.s['positions'] = [{'stake': 192.0, 'coin': 'X', 'exit_ms': 10**15}]
            k.save()
            log = K.run(base, now_ms=self.t_sig + self.bar + 60_000, schalter=self.sw(start=200.0))
            self.assertTrue(any('reicht nicht' in x for x in log), log)

    def test_open_position_blocks_new_entry(self):
        """Ausstieg fehlgeschlagen (kein Kurs) → Coin-Sperre abgelaufen, aber kein zweiter Trade auf dem offenen Coin."""
        with tempfile.TemporaryDirectory() as d:
            base = Path(d); now = self.t_sig + self.bar + 60_000
            K.run(base, now_ms=now, schalter=self.sw(start=200.0))
            k = K.A.Konto(base / 'papier' / 'konto.json'); k.s['busy'] = {}; k.save()
            self.assertFalse(K.pruefbar(k, 'SOL', self.t_sig + 10 * self.bar))

    def test_no_access_still_closes_paper(self):
        with tempfile.TemporaryDirectory() as d:
            base = Path(d); msgs = []
            K.run(base, now_ms=self.t_sig + self.bar + 60_000, schalter=self.sw(start=200.0))
            p = K.A.Konto(base / 'papier' / 'konto.json').s['positions'][0]
            K.run(base, now_ms=p['exit_ms'] + 120_000, schalter=self.sw(echtgeld=True, start=200.0), hook=msgs.append)
            self.assertEqual(K.A.Konto(base / 'papier' / 'konto.json').s['positions'], [])
            self.assertTrue(any('keinen Bitget-Zugang' in m for m in msgs))

    def test_flag_only_after_successful_message(self):
        with tempfile.TemporaryDirectory() as d:
            def broken(_): raise OSError('Telegram weg')
            K.run(Path(d), now_ms=self.t_sig + self.bar + 60_000, schalter=self.sw(echtgeld=True), hook=broken)
            self.assertFalse((Path(d) / 'ohne_zugang.flag').exists())

    def test_missing_lag_ratio_is_retried(self):
        t_sig = self.t_sig
        with tempfile.TemporaryDirectory() as d, mock.patch.object(K, 'ratio_at', lambda coin, t: 2.0 if t == t_sig else None):
            K.run(Path(d), now_ms=self.t_sig + self.bar + 60_000, schalter=self.sw(start=200.0))
            k = K.A.Konto(Path(d) / 'papier' / 'konto.json')
            self.assertEqual(k.s['positions'], []); self.assertEqual(k.s['pending']['SOL'], [self.t_sig])

    def test_foreign_position_warns_once(self):
        with tempfile.TemporaryDirectory() as d:
            base = Path(d); lv = FakeLive(); msgs = []
            K.run(base, now_ms=1_000, schalter=self.sw(echtgeld=False))
            K.run(base, now_ms=self.t_sig + self.bar + 60_000, live=lv, schalter=self.sw(echtgeld=True), hook=msgs.append)
            lv.pos[('ETHUSDT', 'long')] = 1.0                                     # Fremdposition (anderer Bot)
            for i in (2, 3):
                K.run(base, now_ms=self.t_sig + i * self.bar + 60_000, live=lv, schalter=self.sw(echtgeld=True), hook=msgs.append)
            self.assertEqual(sum('WARNUNG ETHUSDT' in m for m in msgs), 1, msgs)

    def test_same_bar_signals_alphabetical(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.object(R, 'COINS', ['SOL', 'ETH']):
            K.run(Path(d), now_ms=self.t_sig + self.bar + 60_000, schalter=self.sw(max_offen=1, start=200.0))
            self.assertEqual([p['coin'] for p in K.A.Konto(Path(d) / 'papier' / 'konto.json').s['positions']], ['ETH'])

    def _live(self, lv, base):
        K.run(base, now_ms=1_000, schalter=self.sw(echtgeld=False))
        K.run(base, now_ms=self.t_sig + self.bar + 60_000, live=lv, schalter=self.sw(echtgeld=True))
        return K.A.Konto(base / 'echtgeld' / 'konto.json')

    def test_crash_right_after_send_keeps_vormerkung(self):
        """Absturz nach dem Senden: Vormerkung + Reservierung liegen schon auf Platte, nächster Lauf bucht die Order."""
        with tempfile.TemporaryDirectory() as d:
            base = Path(d); lv = FakeLive(); real = lv.market
            def crash(sym, hold, size, oid, close=False):
                if close: return real(sym, hold, size, oid, close=close)
                real(sym, hold, size, oid); raise KeyboardInterrupt
            lv.market = crash
            K.run(base, now_ms=1_000, schalter=self.sw(echtgeld=False))
            with self.assertRaises(KeyboardInterrupt):
                K.run(base, now_ms=self.t_sig + self.bar + 60_000, live=lv, schalter=self.sw(echtgeld=True))
            k = K.A.Konto(base / 'echtgeld' / 'konto.json')
            self.assertEqual(len(k.s['pending_live']), 1); self.assertLess(k.s['cash'], 200.0)
            lv.market = real
            K.run(base, now_ms=self.t_sig + 2 * self.bar + 60_000, live=lv, schalter=self.sw(echtgeld=True))
            k = K.A.Konto(base / 'echtgeld' / 'konto.json')
            self.assertEqual(len(k.s['positions']), 1); self.assertEqual(k.s['pending_live'], [])
            self.assertAlmostEqual(k.equity(), 200.0, places=6)

    def test_never_arrived_order_released_after_checks(self):
        with tempfile.TemporaryDirectory() as d:
            base = Path(d); lv = FakeLive(); real = lv.market
            def lost(sym, hold, size, oid, close=False):
                if close: return real(sym, hold, size, oid, close=close)
                raise OSError('Netz weg vor Bitget')
            lv.market = lost
            k = self._live(lv, base)
            self.assertEqual(len(k.s['pending_live']), 1)
            for i in range(2, 6):                                                  # 4 weitere Läufe, > 15 min
                K.run(base, now_ms=self.t_sig + self.bar + 60_000 + i * 5 * 60_000, live=lv, schalter=self.sw(echtgeld=True))
            k = K.A.Konto(base / 'echtgeld' / 'konto.json')
            self.assertEqual(k.s['pending_live'], []); self.assertAlmostEqual(k.s['cash'], 200.0)

    def test_partial_close_then_rest_liquidated_keeps_partial_profit(self):
        from titanbot import bitget as BG
        with tempfile.TemporaryDirectory() as d:
            base = Path(d); lv = FakeLive(); k = self._live(lv, base)
            p = k.s['positions'][0]; q0 = p['size']; oid = BG.client_oid(p['genom'], 'SOL', p['entry_ms'], 'close')
            lv.orders[oid] = {'state': 'canceled', 'priceAvg': '60', 'baseVolume': str(q0 / 2), 'fee': '0'}
            lv.pos[('SOLUSDT', 'long')] = 0.0                                     # Rest liquidiert
            ex = p['exit_ms']
            for dt in (120_000, 120_000 + 5 * 60_000, 120_000 + 20 * 60_000):
                K.run(base, now_ms=ex + dt, live=lv, schalter=self.sw(echtgeld=True))
            c = K.A.Konto(base / 'echtgeld' / 'konto.json').s['closed'][0]
            self.assertTrue(c['liquidated'])
            want = ((60 - 50) * q0 / 2 - p['fee_in'] * 0.5 - p['stake'] * 0.5) / (p['stake'] * p['lev'])
            self.assertAlmostEqual(c['r'], want, places=9)
            self.assertGreater(c['r'], -1.0)

    def test_same_order_id_not_booked_twice(self):
        with tempfile.TemporaryDirectory() as d:
            base = Path(d); lv = FakeLive(); k = self._live(lv, base)
            ex = k.s['positions'][0]['exit_ms']
            K.run(base, now_ms=ex + 120_000, live=lv, schalter=self.sw(echtgeld=True))
            k = K.A.Konto(base / 'echtgeld' / 'konto.json'); k.s['busy'] = {}; k.s['last_bar'] = {}; k.save()
            log = K.run(base, now_ms=self.t_sig + self.bar + 60_000, live=lv, schalter=self.sw(echtgeld=True))
            k = K.A.Konto(base / 'echtgeld' / 'konto.json')
            self.assertEqual(k.s['positions'], []); self.assertTrue(any('schon gebucht' in x for x in log), log)

    def test_no_live_entry_without_initialisation(self):
        with tempfile.TemporaryDirectory() as d:
            base = Path(d); lv = FakeLive()
            def broken(): raise OSError('Bitget nicht erreichbar')
            lv.account = broken
            K.run(base, now_ms=1_000, schalter=self.sw(echtgeld=False))
            log = K.run(base, now_ms=self.t_sig + self.bar + 60_000, live=lv, schalter=self.sw(echtgeld=True))
            self.assertEqual(lv.orders, {}, log)

    def test_kaufliste_survives_crash(self):
        """Absturz nach dem Scan (busy/last_bar gesichert), bevor gekauft wurde → nächster Lauf kauft aus der gespeicherten Liste."""
        with tempfile.TemporaryDirectory() as d:
            base = Path(d); real = K.kauf; n = {'c': 0}
            def crash(*a, **kw):
                n['c'] += 1; raise KeyboardInterrupt
            with mock.patch.object(K, 'kauf', crash), self.assertRaises(KeyboardInterrupt):
                K.run(base, now_ms=self.t_sig + self.bar + 60_000, schalter=self.sw(start=200.0))
            k = K.A.Konto(base / 'papier' / 'konto.json')
            self.assertEqual(len(k.s['kaufliste']), 1); self.assertEqual(k.s['positions'], [])
            K.run(base, now_ms=self.t_sig + self.bar + 120_000, schalter=self.sw(start=200.0))
            k = K.A.Konto(base / 'papier' / 'konto.json')
            self.assertEqual(len(k.s['positions']), 1); self.assertNotIn('kaufliste', k.s)

    def test_client_oid_prefix(self):
        from titanbot import bitget as BG
        self.assertTrue(BG.client_oid('titanbot', 'SOL', 1, 'open').startswith('tit'))


if __name__ == '__main__':
    unittest.main()
