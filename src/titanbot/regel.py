"""titanbot-Regel „SMC-Raid“ (Claude + Codex, 21.11.2026): EINE Regel für Live und Backtest, reines Python (Mini-PC).

SMC-Lesart, jede Bedingung mit echten Daten statt Kerzenform:
  Discount         Kurs ≥ 20 % unter dem 90-Tage-Hoch
  Sell-Side-Sweep  15-min-Abverkauf z ≤ −4
  Liquidität weg   Open Interest ≥ 3 % unter dem 6-h-Maximum (Stops/Liquidationen ausgelöst)
  Inducement       Anteil long positionierter Kleinanleger-Konten kleiner als vor 72 h
→ Long zur Eröffnung der Folgekerze, Ausstieg nach 24 h.

Forschung: botprojekte/forschung/2026-11-21-smc-neu (ERGEBNIS*.md) und 2026-11-20-btc-abverkaeufe.
Bestanden u. a. auf 30 nie benutzten Coins (D4): +1,28 %/Trade netto, p 0,0005, 29/30 Coins und jedes Jahr 2021–26 positiv.
Größe (Codex R4, auf frischen Coins bestanden): 6,25 % Margin bei OI-Abbau ≤ −5 %, sonst 3,75 %; Hebel 1, max. 20 offen
→ frische Coins 2021–26: 32,4 %/J, größter Rückgang −38,9 % (5m-Bewertung).

Signal am Schluss einer Binance-USDT-M-5m-Kerze t (alle Bedingungen):
1. r15 = ln(c_t / c_{t−3}); z = r15 / Std(r15 der letzten 8640 Kerzen inkl. t, ddof 1, min. 2000) ≤ −4.
2. OI_t / max(OI der letzten 72 Kerzen inkl. t, min. 36 Werte) − 1 ≤ −3 %.
3. R = g/(1+g) (g = globalLongShortAccountRatio) jetzt KLEINER als vor 72 h.
4. c_t / max(Schlusskurse der letzten 25 920 Kerzen = 90 T inkl. t, min. 17 280) − 1 ≤ −20 %.
Je Coin keine Überlappung (neues Signal frühestens ab der Kerze, in der der alte Trade endet).
"""
from __future__ import annotations
import math

# 60 Coins der Forschung (30 Entwicklungs- + 30 frische Prüf-Coins), alle auf Binance und Bitget USDT-M (geprüft 21.11.2026)
COINS = ("BTC ETH SOL XRP BNB DOGE ADA LINK AVAX LTC DOT ATOM NEAR FIL UNI ETC BCH TRX AAVE ALGO "
         "XLM VET THETA SAND MANA AXS CRV ZEC HBAR ICP "
         "APE API3 AR BAT CHZ COMP COTI DASH DYDX EGLD ENS GALA HOT IMX IOST IOTA KAVA NEO ONE OP "
         "PEOPLE RSR RUNE RVN SNX SUSHI TRB XMR ZEN ZIL").split()
BAR_MS = 300_000
Z_THR, OI_DROP, DD_THR = -4.0, -0.03, -0.20
SIG_N, SIG_MIN = 8640, 2000
OI_N, OI_MIN = 72, 36
R_LAG_BARS = 864                      # 72 h
DD_N, DD_MIN = 25_920, 17_280         # 90 / 60 Tage
HOLD_BARS = 288                       # 24 h
NEED_CLOSES = DD_N                    # Kerzen, die der Live-Dienst vorhalten muss (≥ SIG_N + 3)
OI_STARK = -0.05                      # Größenregel (Codex R4): starker Hebel-Abbau


def z_flush(closes) -> float | None:
    """z der 15-min-Rendite der letzten Kerze. closes = aufeinanderfolgende 5m-Schlüsse, letzter = Kerze t."""
    c = closes[-(SIG_N + 3):]
    r = [math.log(c[i] / c[i - 3]) for i in range(3, len(c))]
    if len(r) < SIG_MIN:
        return None
    n = len(r); m = sum(r) / n
    sd = math.sqrt(sum((x - m) ** 2 for x in r) / (n - 1))
    return r[-1] / sd if sd > 0 else None


def dd90(closes) -> float | None:
    c = closes[-DD_N:]
    if len(c) < DD_MIN:
        return None
    return c[-1] / max(c) - 1


def oi_drop(ois) -> float | None:
    """ois = OI-Werte der letzten 72 Kerzen (None = fehlt), letzter = Kerze t."""
    w = ois[-OI_N:]
    vals = [v for v in w if v is not None]
    if w[-1] is None or len(vals) < OI_MIN:
        return None
    mx = max(vals)
    return w[-1] / mx - 1 if mx > 0 else None


def retail_falling(g_now, g_72h_ago) -> bool:
    if g_now is None or g_72h_ago is None:
        return False
    return g_now / (1 + g_now) < g_72h_ago / (1 + g_72h_ago)


def price_ok(closes) -> tuple[bool, float | None, float | None]:
    """Nur Kursbedingungen (1 und 4) – der Live-Dienst lädt OI/Long-Short nur, wenn diese schon erfüllt sind."""
    z = z_flush(closes)
    d = dd90(closes)
    return (z is not None and z <= Z_THR and d is not None and d <= DD_THR), z, d


def einsatz(oi_wert, stark=0.0625, normal=0.0375) -> float:
    """Margin-Anteil am Kontostand: starker Hebel-Abbau (OI ≤ −5 % ggü. 6-h-Max) → größer."""
    return stark if (oi_wert is not None and oi_wert <= OI_STARK) else normal


def signal(closes, ois, g_now, g_72h_ago) -> tuple[bool, dict]:
    """Vollständige Entscheidung am Schluss von Kerze t. Rückgabe (Kaufen?, Merkmale)."""
    ok, z, d = price_ok(closes)
    o = oi_drop(ois) if ok else None
    rf = retail_falling(g_now, g_72h_ago) if ok else False
    buy = bool(ok and o is not None and o <= OI_DROP and rf)
    return buy, {'z': z, 'dd': d, 'oi_drop': o, 'retail_falling': rf}
