"""Nachspiel: titanbot mit SEINEM EIGENEN Code über die Geschichte laufen lassen (Desktop, braucht pandas + Forschungsdaten).

Aufgerufen werden genau die Bot-Funktionen bot.pruefbar, bot.kauf, bot.exits mit dem Konto markt.Konto, in der Reihenfolge eines
echten Laufs (Ausstiege → Einstiege; gleichzeitige Signale nach Kerze, dann Coin wie bot.entries). Ersetzt werden nur die
Datenquellen: Kurse/Funding/Open Interest aus den Binance-Daten der Forschung statt live von Bitget/Binance.
- Signale: regel.signal auf jeder Kandidaten-Kerze (wie tools/parity_signale.py), Bearbeitung zur Laufzeit t + 6 min
  (Kerzenschluss + 1 min, wie der Timer *:01/5).
- Papier-Einstieg/-Ausstieg zum Eröffnungskurs der 5m-Kerze der Ausführungszeit, 0,05 % Spread + 0,06 % Gebühr je Seite
  (wie der Bot), Funding = echte Binance-Sätze (live: Bitget).
Kontobewertung alle 5 Minuten (Schlusskurse). Ergebnis: data/nachspiel.json (Kennzahlen, Trades, Trichter), data/nachspiel_kurve.npy.

Aufruf:  python tools/nachspiel.py [--forschung D:/botprojekte/forschung/2026-11-20-btc-abverkaeufe] [--start 2021-12-01]
"""
from __future__ import annotations
import argparse, json, math, sys, tempfile, time
from pathlib import Path
import numpy as np, pandas as pd
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from titanbot import regel as R, bot as K, settings as ST, markt as A

BAR, HOUR, DAY = R.BAR_MS, 3_600_000, 86_400_000
# 30 Coins, die bei der Entwicklung nie benutzt wurden (Forschung smclib.TEST2) – für die getrennte Auswertung
FRISCH = set("APE API3 AR BAT CHZ COMP COTI DASH DYDX EGLD ENS GALA HOT IMX IOST IOTA KAVA NEO ONE OP PEOPLE RSR RUNE RVN SNX "
             "SUSHI TRB XMR ZEN ZIL".split())


def load(fdir: Path, coin: str):
    sym = coin + 'USDT'
    if coin == 'BTC':
        b = pd.read_parquet(fdir / 'data/btc5m.parquet')
        x = b[['fopen', 'fhigh', 'flow', 'fclose']].astype(float).dropna(); x.columns = ['open', 'high', 'low', 'close']
        x['oi'] = b.oi.astype(float).where(lambda v: v > 1000); x['ga'] = b.glob_acc.astype(float)
    else:
        x = pd.read_parquet(fdir / f'data/fut5_{sym}.parquet')[['open', 'high', 'low', 'close']]
        m = pd.read_parquet(fdir / f'data/metrics_{sym}.parquet'); m.index = m.index.floor('5min'); m = m[~m.index.duplicated()]
        x = x.join(m[['oi', 'glob_acc']], how='left').rename(columns={'glob_acc': 'ga'}); x['oi'] = x.oi.where(x.oi > 0)
    fr = pd.read_parquet(fdir / f'data/funding_{sym}.parquet').fr.sort_index()
    return x, fr[~fr.index.duplicated()]


def signale(x: pd.DataFrame, t0: pd.Timestamp, t1: pd.Timestamp):
    """Alle Kerzen, auf denen regel.signal kauft (ohne Belegung – die prüft der Bot selbst), plus Trichter-Zählung."""
    c = x.close.values.astype(float); n = len(c); oi = x.oi.values.astype(float); ga = x.ga.values.astype(float)
    lc = np.log(c); r15 = np.full(n, np.nan); r15[3:] = lc[3:] - lc[:-3]
    z = r15 / pd.Series(r15).rolling(R.SIG_N, min_periods=R.SIG_MIN).std().values
    dd = c / pd.Series(c).rolling(R.DD_N, min_periods=R.DD_MIN).max().values - 1
    oid = x.oi.values / x.oi.rolling(R.OI_N, min_periods=R.OI_MIN).max().values - 1
    rr = x.ga / (1 + x.ga); rf = (rr < rr.shift(R.R_LAG_BARS)).values
    inw = (x.index >= t0) & (x.index < t1)
    f1 = inw & (z <= R.Z_THR); f2 = f1 & (dd <= R.DD_THR); f3 = f2 & (oid <= R.OI_DROP); f4 = f3 & rf
    zahl = dict(flush=int(f1.sum()), discount=int(f2.sum()), oi=int(f3.sum()), kleinanleger=int(f4.sum()))
    out = []
    for i in np.where((z <= -3.9) & (dd <= -0.19) & inw)[0]:
        if i + R.HOLD_BARS >= n:
            continue
        ois = [None if math.isnan(v) else float(v) for v in oi[max(0, i - R.OI_N + 1):i + 1]]
        g_now = None if math.isnan(ga[i]) else float(ga[i])
        g_lag = None if i < R.R_LAG_BARS or math.isnan(ga[i - R.R_LAG_BARS]) else float(ga[i - R.R_LAG_BARS])
        buy, feat = R.signal(c[max(0, i - R.DD_N + 1):i + 1].tolist(), ois, g_now, g_lag)
        if buy:
            out.append((int(x.index[i].value // 1_000_000), feat))
    return out, zahl


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--forschung', type=Path, default=Path('D:/botprojekte/forschung/2026-11-20-btc-abverkaeufe'))
    ap.add_argument('--start', default='2021-12-01')
    ap.add_argument('--ende', default='2026-11-19')
    a = ap.parse_args(argv)
    sw = ST.load(Path(__file__).resolve().parents[1] / 'settings.json')
    assert not sw['echtgeld'], 'Nachspiel nur im Papiermodus'
    t0, t1 = pd.Timestamp(a.start), pd.Timestamp(a.ende)
    D, F, ev, trichter = {}, {}, [], {}
    tic = time.time()
    for coin in R.COINS:
        x, fr = load(a.forschung, coin)
        D[coin] = (x.index.values.astype('datetime64[ms]').astype(np.int64), x.open.values, x.low.values, x.close.values)
        F[coin] = (fr.index.floor('5min').values.astype('datetime64[ms]').astype(np.int64), np.cumsum(fr.values))
        sig, zahl = signale(x, t0, t1)
        for k_, v in zahl.items():
            trichter[k_] = trichter.get(k_, 0) + v
        for t, feat in sig:
            ev.append((t + BAR + 60_000, t, coin, feat))
        print(f'{coin:6s} Signale bis jetzt {len(ev)}  ({time.time() - tic:.0f} s)', flush=True)
    ev.sort(key=lambda e: (e[0], e[1], e[2]))
    trichter['signale'] = len(ev)

    def bar_idx(coin, t_ms):
        ts = D[coin][0]; i = int(np.searchsorted(ts, t_ms, side='right')) - 1
        return max(0, i)
    def px_open(coin, t_ms):                        # Eröffnungskurs der 5m-Kerze, in der t_ms liegt
        return float(D[coin][1][bar_idx(coin, t_ms)])
    def funding(symbol, von, bis, side=1):
        coin = symbol[:-4]; ts, cs = F[coin]
        i, j = np.searchsorted(ts, von, side='right'), np.searchsorted(ts, bis, side='right')
        s = (cs[j - 1] if j > 0 else 0.0) - (cs[i - 1] if i > 0 else 0.0)
        return -side * float(s)
    K._paper_px = px_open
    A.bitget_funding_anteil = funding

    tmp = Path(tempfile.mkdtemp()); k = K.konto(tmp, 'papier', sw['papier_start'])
    k.save = lambda: None                            # kein Schreiben je Buchung (Nachspiel im Speicher)
    start_ms, end_ms = int(t0.value // 1_000_000), int(t1.value // 1_000_000)
    curve, log_all = [], []
    ei = 0; zuletzt = None; maxpos = [0]
    def mark(now):                                   # Kontowert: Kasse + offene Positionen zum letzten 5m-Schluss
        v = k.s['cash']
        for p in k.s['positions']:
            ts, _, _, c = D[p['coin']]; px = float(c[bar_idx(p['coin'], now - BAR)])
            v += p['stake'] * max(0.0, 1 + p['lev'] * (px * (1 - A.SPREAD) * (1 - A.FEE) / p['entry_eff'] - 1))
        return v
    def takt(now, kaeufe):
        log = []
        K.exits(k, None, now, log)
        for _, t, coin, feat in kaeufe:              # bereits nach (Kerze, Coin) sortiert wie bot.entries
            if K.pruefbar(k, coin, t):
                K.kauf(k, None, sw, coin, t, feat, now, log)
        log_all.extend((now, x_) for x_ in log)
    grid = range(start_ms + BAR, end_ms + DAY, BAR)
    for g in grid:
        while True:
            nxt_e = ev[ei][0] if ei < len(ev) else None
            nxt_x = min((p['exit_ms'] + 60_000 for p in k.s['positions']), default=None)
            cand = [x_ for x_ in (nxt_e, nxt_x) if x_ is not None and x_ <= g]
            if not cand:
                break
            now = min(cand)
            batch = []
            while ei < len(ev) and ev[ei][0] == now:
                batch.append(ev[ei]); ei += 1
            if now == zuletzt and not batch:
                break                                # fälliger Ausstieg blieb offen → nicht endlos wiederholen
            zuletzt = now
            takt(now, batch)
        curve.append((g, mark(g)))
        maxpos[0] = max(maxpos[0], len(k.s['positions']))
    c_ = np.array(curve); t_ = c_[:, 0]; e_ = c_[:, 1]
    yrs = (t_[-1] - t_[0]) / (365.25 * DAY)
    dd = e_ / np.maximum.accumulate(e_) - 1
    def window(a_, b_):
        i, j = np.searchsorted(t_, a_), np.searchsorted(t_, b_) - 1
        e = e_[i:j + 1]; y = (t_[j] - t_[i]) / (365.25 * DAY)
        return dict(pro_jahr=round(((e[-1] / e[0]) ** (1 / y) - 1) * 100, 1), maxdd=round(float((e / np.maximum.accumulate(e) - 1).min()) * 100, 1))
    ms = lambda s_: int(pd.Timestamp(s_).value // 1_000_000)
    cl = k.s['closed']; rs = np.array([c['r'] for c in cl])
    fr_ = np.array([c['r'] for c in cl if c['coin'] in FRISCH]); al = np.array([c['r'] for c in cl if c['coin'] not in FRISCH])
    yr = {}
    for y in range(t0.year, t1.year + 1):
        i, j = np.searchsorted(t_, ms(f'{y}-01-01')), np.searchsorted(t_, ms(f'{y + 1}-01-01')) - 1
        i = max(i, 0)
        if j > i: yr[y] = round((e_[j] / e_[i] - 1) * 100, 1)
    res = dict(start=sw['papier_start'], ende=round(float(e_[-1]), 1), faktor=round(float(e_[-1] / e_[0]), 2),
               pro_jahr=round(((e_[-1] / e_[0]) ** (1 / yrs) - 1) * 100, 1), maxdd=round(float(dd.min()) * 100, 1),
               maxdd_datum=str(pd.Timestamp(int(t_[int(np.argmin(dd))]), unit='ms').date()),
               haelfte1=window(t_[0], ms('2024-06-01')), haelfte2=window(ms('2024-06-01'), t_[-1] + 1), jahre=yr,
               trades=len(cl), trades_pro_jahr=round(len(cl) / yrs), treffer=round(float((rs > 0).mean()) * 100, 1),
               mittel_r=round(float(rs.mean()) * 100, 2), median_r=round(float(np.median(rs)) * 100, 2),
               frische_coins=dict(trades=len(fr_), mittel_r=round(float(fr_.mean()) * 100, 2)),
               entwicklungs_coins=dict(trades=len(al), mittel_r=round(float(al.mean()) * 100, 2)),
               einsatz_stark=sum(1 for c in cl if c.get('einsatz_anteil') == sw['einsatz_stark']),
               einsatz_normal=sum(1 for c in cl if c.get('einsatz_anteil') == sw['einsatz_normal']),
               uebersprungen_max=sum(1 for _, x_ in log_all if 'Positionen offen' in x_),
               uebersprungen_kasse=sum(1 for _, x_ in log_all if 'reicht nicht' in x_),
               zu_klein=sum(1 for _, x_ in log_all if 'zu klein' in x_),
               max_gleichzeitig=maxpos[0], trichter=trichter)
    out = dict(kennzahlen=res, trades=cl)
    d = Path(__file__).resolve().parents[1] / 'data'; d.mkdir(exist_ok=True)
    json.dump(out, open(d / 'nachspiel.json', 'w'), default=float)
    np.save(d / 'nachspiel_kurve.npy', c_[::12])                   # stündlich speichern (Grafik), Kennzahlen aus 5m
    print(json.dumps(res, indent=1), flush=True)


if __name__ == '__main__':
    main()
