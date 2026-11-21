"""Parität titanbot: titanbot.regel (reines Python, Live-Regel) gegen die Forschungs-Signale der 60 Coins (pandas).

Speist die Forschungsdaten (5m-Klines + Metrics, data.binance.vision) Kerze für Kerze durch kapitulation_rule.signal
und vergleicht die Signalzeitpunkte mit forschung/2026-11-21-smc-neu/data/acct_trades.pkl (4 208 B-Ereignisse, 60 Coins, 12/2021–11/2026).
Ein lockerer, vektorisierter Vorfilter (z ≤ −3,9, Abstand ≤ −19 %) spart nur Rechenzeit; entscheiden tut allein die Regel.
Zweiter Lauf: Long/Short-Verhältnis auf 4 Nachkommastellen gerundet (so liefert es die Binance-API live).

Aufruf (Desktop, braucht pandas):  python tools/parity_signale.py [--forschung D:/botprojekte/forschung/2026-11-20-btc-abverkaeufe]
                                   [--ref D:/botprojekte/forschung/2026-11-21-smc-neu/data/acct_trades.pkl]
"""
from __future__ import annotations
import argparse, math, sys
from pathlib import Path
import numpy as np, pandas as pd
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from titanbot import regel as R


def load(fdir: Path, coin: str) -> pd.DataFrame:
    sym = coin + 'USDT'
    if coin == 'BTC':
        b = pd.read_parquet(fdir / 'data/btc5m.parquet')
        x = b[['fopen', 'fclose']].astype(float).dropna(); x.columns = ['open', 'close']
        x['oi'] = b.oi.astype(float).where(lambda v: v > 1000); x['glob_acc'] = b.glob_acc.astype(float)
        return x
    x = pd.read_parquet(fdir / f'data/fut5_{sym}.parquet')[['open', 'close']]
    m = pd.read_parquet(fdir / f'data/metrics_{sym}.parquet'); m.index = m.index.floor('5min'); m = m[~m.index.duplicated()]
    x = x.join(m[['oi', 'glob_acc']], how='left'); x['oi'] = x.oi.where(x.oi > 0)
    return x


def run_coin(x: pd.DataFrame, t0: pd.Timestamp, t1: pd.Timestamp, rnd: int | None):
    c = x.close.values.astype(float); n = len(c)
    oi = x.oi.values.astype(float); ga = x.glob_acc.values.astype(float)
    if rnd is not None:
        ga = np.round(ga, rnd)
    lc = np.log(c); r15 = np.full(n, np.nan); r15[3:] = lc[3:] - lc[:-3]
    z = r15 / pd.Series(r15).rolling(R.SIG_N, min_periods=R.SIG_MIN).std().values
    dd = c / pd.Series(c).rolling(R.DD_N, min_periods=R.DD_MIN).max().values - 1
    cand = np.where((z <= -3.9) & (dd <= -0.19))[0]
    out, busy = [], -1
    idx = x.index
    for i in cand:
        if i + 1 <= busy or idx[i] < t0 or idx[i] >= t1 or i + R.HOLD_BARS >= n:
            continue
        ois = [None if math.isnan(v) else float(v) for v in oi[max(0, i - R.OI_N + 1):i + 1]]
        g_now = None if math.isnan(ga[i]) else float(ga[i])
        g_lag = None if i < R.R_LAG_BARS or math.isnan(ga[i - R.R_LAG_BARS]) else float(ga[i - R.R_LAG_BARS])
        buy, _ = R.signal(c[max(0, i - R.DD_N + 1):i + 1].tolist(), ois, g_now, g_lag)
        if buy:
            out.append(idx[i]); busy = i + R.HOLD_BARS
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--forschung', type=Path, default=Path('D:/botprojekte/forschung/2026-11-20-btc-abverkaeufe'))
    ap.add_argument('--ref', type=Path, default=Path('D:/botprojekte/forschung/2026-11-21-smc-neu/data/acct_trades.pkl'))
    a = ap.parse_args(argv)
    ref = pd.read_pickle(a.ref); ref['sig'] = pd.to_datetime(ref.t0) - pd.Timedelta(minutes=5)
    t0, t1 = pd.Timestamp('2021-12-01'), pd.Timestamp('2026-11-19')
    tot = {'exakt': [0, 0, 0], 'gerundet': [0, 0, 0]}
    for coin in R.COINS:
        x = load(a.forschung, coin)
        want = set(ref[ref.sym == coin + 'USDT'].sig)
        for name, rnd in (('exakt', None), ('gerundet', 4)):
            got = set(run_coin(x, t0, t1, rnd))
            same, extra, miss = len(got & want), len(got - want), len(want - got)
            tot[name][0] += same; tot[name][1] += extra; tot[name][2] += miss
            if name == 'exakt' or extra or miss:
                print(f'{coin:6s} {name:9s} gleich {same:4d}  nur Regel {extra:3d}  nur Forschung {miss:3d}', flush=True)
    for name, (s, e, m) in tot.items():
        print(f'GESAMT {name}: gleich {s}, nur Regel {e}, nur Forschung {m}  (Forschung {len(ref)})')
    return 0 if tot['exakt'][1] == tot['exakt'][2] == 0 else 1


if __name__ == '__main__':
    raise SystemExit(main())
