"""Grafiken für die README (docs/images/*.png) aus dem Nachspiel (data/nachspiel.json, tools/nachspiel.py) und den Forschungsdaten.
Aufruf (Desktop, braucht pandas + matplotlib):  python tools/readme_grafiken.py [--forschung D:/botprojekte/forschung/2026-11-20-btc-abverkaeufe]"""
from __future__ import annotations
import argparse, json, sys
from pathlib import Path
import numpy as np, pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.patches import FancyBboxPatch
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from titanbot import regel as R

GELB, BLAU, GRUEN, ROT, GRAU, TEXT = '#d98e04', '#2a6fc4', '#16804a', '#c8372f', '#8a8f87', '#1c211d'
plt.rcParams.update({'font.size': 10.5, 'axes.edgecolor': '#c9cec5', 'axes.labelcolor': TEXT, 'xtick.color': '#555b53',
                     'ytick.color': '#555b53', 'axes.grid': True, 'grid.color': '#ebeee8', 'axes.spines.top': False, 'axes.spines.right': False})
de = lambda s: s.replace('-', '−')


def ablauf(out):
    fig, ax = plt.subplots(figsize=(12, 3.6)); ax.set_axis_off(); ax.set_xlim(0, 12); ax.set_ylim(0, 3.6); ax.grid(False)
    def box(x, y, w, h, titel, text, farbe):
        ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle='round,pad=0.02,rounding_size=0.08', fc='white', ec=farbe, lw=2))
        ax.text(x + 0.12, y + h - 0.22, titel, fontsize=11, weight='bold', color=farbe, va='top')
        ax.text(x + 0.12, y + h - 0.62, text, fontsize=9.6, color=TEXT, va='top', linespacing=1.35)
    def pfeil(x1, y1, x2, y2):
        ax.annotate('', (x2, y2), (x1, y1), arrowprops=dict(arrowstyle='-|>', color=GRAU, lw=1.6))
    ax.text(0.05, 3.5, 'Alle 5 Minuten, 60 Coins: Sell-Side-Liquidity-Raid im Discount kaufen', fontsize=12.5, weight='bold', color=TEXT, va='top')
    b = [('1 · Discount', 'Kurs ≥ 20 % unter\n90-Tage-Hoch'), ('2 · Sweep', '15-min-Abverkauf\n≤ −4 σ (30 Tage)'),
         ('3 · Liquidität weg', 'Open Interest ≥ 3 %\nunter 6-h-Maximum\n(Stops/Liquidationen)'),
         ('4 · Inducement', 'Long-Anteil der\nKleinanleger-Konten\n< vor 72 h')]
    for i, (t, x_) in enumerate(b):
        box(0.05 + i * 2.25, 0.35, 2.0, 2.5, t, x_, GELB)
        if i: pfeil(0.05 + i * 2.25 - 0.22, 1.6, 0.05 + i * 2.25 - 0.03, 1.6)
    pfeil(9.0 - 0.2, 1.6, 9.2, 1.6)
    box(9.2, 0.35, 2.75, 2.5, 'Long 24 h', 'Margin 6,25 % des Kontos\nbei OI ≤ −5 %, sonst 3,75 %\nHebel 1 · kein Stop\nmax. 20 gleichzeitig\nPapier/Echtgeld laut\nsettings.json', GRUEN)
    fig.savefig(out, dpi=130, bbox_inches='tight', facecolor='white'); plt.close(fig)


def kurve(nach):
    c = np.array(nach['kurve_voll'])
    return pd.Series(c[:, 1], index=pd.to_datetime(c[:, 0].astype(np.int64), unit='ms'))


def konto(out, nach, fdir):
    s = kurve(nach); start = nach['kennzahlen']['start']; e = (s / start * 100).resample('1D').last().dropna()
    b = pd.read_parquet(fdir / 'data/btc5m.parquet').fclose.astype(float).reindex(s.index, method='ffill')
    b = (b / b.iloc[0] * 100).resample('1D').last().dropna()
    fig, (a1, a2) = plt.subplots(2, 1, figsize=(12, 6.6), gridspec_kw={'height_ratios': [3, 1.2]}, sharex=True)
    a1.plot(b.index, b, color=GRAU, lw=1.3, label=f'BTC halten  ({b.iloc[-1]:,.0f})'.replace(',', '.'))
    a1.plot(e.index, e, color=GELB, lw=2.2, label=f'titanbot  ({e.iloc[-1]:,.0f})'.replace(',', '.'))
    lo_, hi_ = min(e.min(), b.min()), max(e.max(), b.max())
    a1.set_yscale('log'); ticks = [t for t in (25, 50, 100, 200, 400, 800, 1600) if lo_ * 0.8 <= t <= hi_ * 1.3]
    a1.set_yticks(ticks); a1.set_yticklabels([f'{t:,}'.replace(',', '.') for t in ticks])
    a1.axhline(100, color=GRAU, ls=':', lw=1); a1.legend(loc='upper left', frameon=False, fontsize=11)
    k = nach['kennzahlen']
    a1.set_title(f"Kontowert (Start 100) – Nachspiel mit titanbots eigenem Code, 12/2021 – 11/2026\n"
                 + de(f"+{k['pro_jahr']:.0f} %/Jahr · größter Rückgang {k['maxdd']:.0f} % (Bewertung alle 5 Minuten)"), loc='left', fontsize=11.5, weight='bold')
    for ser, col in ((e, GELB), (b, GRAU)):
        dd = (ser / ser.cummax() - 1) * 100
        a2.fill_between(dd.index, dd, 0, color=col, alpha=0.35 if col == GELB else 0.18, lw=0); a2.plot(dd.index, dd, color=col, lw=1)
    a2.set_ylabel('Rückgang %'); a2.set_ylim(-82, 2)
    fig.tight_layout(); fig.savefig(out, dpi=130, facecolor='white'); plt.close(fig)


def trichter(out, nach):
    st = nach['kennzahlen']['trichter']; k = nach['kennzahlen']
    rows = [('5-min-Kerzen mit Sweep (z ≤ −4)', st['flush'], GELB), ('… und im Discount (≥ 20 % unter 90-T-Hoch)', st['discount'], GELB),
            ('… und Liquidität weg (OI ≤ −3 %)', st['oi'], GELB), ('… und Inducement (Kleinanleger geben auf) = Signal', st['kleinanleger'], GELB),
            ('Trades von titanbot im Konto (Coin-Sperre 24 h, Limits)', k['trades'], GRUEN)]
    fig, ax = plt.subplots(figsize=(12, 3.2)); ax.grid(False)
    for i, (l, v, col) in enumerate(rows):
        ax.barh(len(rows) - 1 - i, v, color=col, height=0.62)
        ax.text(v + rows[0][1] * 0.01, len(rows) - 1 - i, f'{v:,}'.replace(',', '.'), va='center', fontsize=10.5, weight='bold')
    ax.set_yticks(range(len(rows))); ax.set_yticklabels([r[0] for r in rows][::-1]); ax.set_xticks([]); ax.spines['bottom'].set_visible(False)
    fig.suptitle('Wie viele 5-Minuten-Kerzen jede SMC-Bedingung übrig lässt (60 Coins, 12/2021 – 11/2026)', x=0.01, ha='left', fontsize=11.5, weight='bold')
    ax.set_xlim(0, rows[0][1] * 1.12); fig.tight_layout(); fig.savefig(out, dpi=130, facecolor='white'); plt.close(fig)


def jahre_monate(out, nach):
    s = kurve(nach); start = nach['kennzahlen']['start']
    m = s.resample('ME').last(); m = pd.concat([pd.Series([float(start)], index=[s.index[0] - pd.Timedelta(minutes=5)]), m]).pct_change().dropna() * 100
    jahre = sorted(set(m.index.year))
    fig = plt.figure(figsize=(12, 4.6)); g = fig.add_gridspec(1, 2, width_ratios=[1, 2.3])
    a1 = fig.add_subplot(g[0]); a2 = fig.add_subplot(g[1]); a2.grid(False)
    jr = [(np.prod(1 + m[m.index.year == y] / 100) - 1) * 100 for y in jahre]
    lab = [('2021\nab Dez' if y == 2021 else '2026\nbis 19.11.' if y == 2026 else str(y)) for y in jahre]
    a1.bar(lab, jr, color=[GRUEN if v >= 0 else ROT for v in jr], width=0.65)
    for i, v in enumerate(jr):
        a1.text(i, v + (2 if v >= 0 else -6), de(f'{v:+.0f} %'), ha='center', fontsize=9.5, weight='bold')
    a1.set_title('Konto je Jahr', loc='left', fontsize=11.5, weight='bold')
    mat = np.full((len(jahre), 12), np.nan)
    for k_, v in m.items(): mat[jahre.index(k_.year), k_.month - 1] = v
    lim = np.nanmax(np.abs(mat))
    a2.imshow(mat, cmap='RdYlGn', vmin=-lim, vmax=lim, aspect='auto')
    for i in range(len(jahre)):
        for j in range(12):
            if np.isfinite(mat[i, j]):
                v = round(mat[i, j]); a2.text(j, i, '0' if v == 0 else de(f'{v:+.0f}'), ha='center', va='center', fontsize=8.6)
    a2.set_xticks(range(12)); a2.set_xticklabels(['Jan', 'Feb', 'Mär', 'Apr', 'Mai', 'Jun', 'Jul', 'Aug', 'Sep', 'Okt', 'Nov', 'Dez'])
    a2.set_yticks(range(len(jahre))); a2.set_yticklabels([str(y) for y in jahre])
    a2.set_title('Monatsrenditen des Kontos (%)', loc='left', fontsize=11.5, weight='bold')
    fig.tight_layout(); fig.savefig(out, dpi=130, facecolor='white'); plt.close(fig)


def beispiel(out, nach, fdir):
    tr = pd.DataFrame(nach['trades']); tr = tr[tr.einsatz_anteil == tr.einsatz_anteil.max()]
    tr = tr[pd.to_datetime(tr.signal_ms, unit='ms') >= '2025-06-01']
    t = tr.iloc[(tr.r - tr.r.median()).abs().argsort().iloc[0]]          # typischer Trade (Ergebnis nahe Median), jüngere Zeit
    coin = t.coin; ts = pd.Timestamp(int(t.signal_ms), unit='ms'); sym = coin + 'USDT'
    if coin == 'BTC':
        b = pd.read_parquet(fdir / 'data/btc5m.parquet'); x = b[['fopen', 'fhigh', 'flow', 'fclose']].astype(float); x.columns = ['open', 'high', 'low', 'close']
        x['oi'] = b.oi.astype(float); x['ga'] = b.glob_acc.astype(float)
    else:
        x = pd.read_parquet(fdir / f'data/fut5_{sym}.parquet')[['open', 'high', 'low', 'close']]
        m = pd.read_parquet(fdir / f'data/metrics_{sym}.parquet'); m.index = m.index.floor('5min'); m = m[~m.index.duplicated()]
        x = x.join(m[['oi', 'glob_acc']], how='left').rename(columns={'glob_acc': 'ga'})
    a, b_ = ts - pd.Timedelta(hours=12), ts + pd.Timedelta(hours=27)
    w = x.loc[a:b_].resample('30min').agg({'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'oi': 'last', 'ga': 'last'})
    lag = x.ga.asof(ts - pd.Timedelta(hours=72)); lag_p = lag / (1 + lag) * 100
    hi90 = x.close.loc[ts - pd.Timedelta(days=90):ts].max()
    fig, (a1, a2, a3) = plt.subplots(3, 1, figsize=(12, 7.6), gridspec_kw={'height_ratios': [3, 1, 1]}, sharex=True)
    width = pd.Timedelta(minutes=19)
    for i, r in w.iterrows():
        col = GRUEN if r.close >= r.open else ROT
        a1.vlines(i, r.low, r.high, color=col, lw=1); a1.bar(i, abs(r.close - r.open) or r.close * 1e-4, bottom=min(r.open, r.close), width=width, color=col)
    ein, aus = pd.Timestamp(int(t.entry_ms), unit='ms'), pd.Timestamp(int(t.exit_ms), unit='ms')
    for ax_ in (a1, a2, a3):
        ax_.axvspan(ein, aus, color=GRUEN, alpha=0.08); ax_.axvline(ts, color=GELB, ls='--', lw=1.4)
    a1.scatter([ein], [t.entry_px], marker='^', s=140, color=GRUEN, zorder=5, edgecolor='white')
    a1.scatter([aus], [t.exit_px], marker='v', s=140, color=TEXT, zorder=5, edgecolor='white')
    a1.annotate(f'Kauf {t.entry_px:g}', (ein, t.entry_px), (12, -18), textcoords='offset points', color=GRUEN, weight='bold')
    a1.annotate(de(f'Verkauf {t.exit_px:g}  ({100 * t.r:+.1f} %)'), (aus, t.exit_px), (-200, 14), textcoords='offset points', color=TEXT, weight='bold')
    a1.set_title(f"Echter Trade aus dem Nachspiel: {coin}, Raid {ts:%d.%m.%Y %H:%M} UTC, 24 h gehalten\n"
                 + de(f"Sweep z {t.z:+.1f} σ  ·  Discount {100 * t.dd:+.0f} % unter 90-Tage-Hoch ({hi90:g})  ·  OI {100 * t.oi_drop:+.1f} %  ·  "
                      f"Margin {100 * t.einsatz_anteil:.2f} %".replace('.', ',')).replace('90−Tage−Hoch', '90-Tage-Hoch') + ' (starker Hebel-Abbau)',
                 loc='left', fontsize=10.5, weight='bold')
    oi0 = x.oi.asof(ts); a2.plot(w.index, (w.oi / oi0 - 1) * 100, color=BLAU, lw=1.8); a2.axhline(0, color=GRAU, lw=0.8)
    a2.set_ylabel('OI % zum\nRaid'); a3.plot(w.index, w.ga / (1 + w.ga) * 100, color=GELB, lw=1.8)
    a3.axhline(lag_p, color=TEXT, ls=':', lw=1.2); a3.text(w.index[1], lag_p, ' vor 72 h', va='bottom', fontsize=9)
    a3.set_ylabel('Kleinanleger\nlong %'); a3.xaxis.set_major_formatter(mdates.DateFormatter('%d.%m. %H:%M'))
    fig.tight_layout(); fig.savefig(out, dpi=130, facecolor='white'); plt.close(fig)
    return coin, ts, t


def main(argv=None):
    ap = argparse.ArgumentParser(); ap.add_argument('--forschung', type=Path, default=Path('D:/botprojekte/forschung/2026-11-20-btc-abverkaeufe'))
    a = ap.parse_args(argv)
    nach = json.load(open(ROOT / 'data/nachspiel.json'))
    nach['kurve_voll'] = np.load(ROOT / 'data/nachspiel_kurve.npy').tolist()
    d = ROOT / 'docs/images'; d.mkdir(parents=True, exist_ok=True)
    ablauf(d / 'ablauf.png'); konto(d / 'konto.png', nach, a.forschung); trichter(d / 'trichter.png', nach)
    jahre_monate(d / 'jahre_monate.png', nach); c, ts, t = beispiel(d / 'beispiel_trade.png', nach, a.forschung)
    print('Grafiken erstellt:', sorted(p.name for p in d.glob('*.png')), '· Beispiel', c, ts, f'r {100 * t.r:+.2f} %')


if __name__ == '__main__':
    main()
