# titanbot – SMC-Raid

**Kauft den Sell-Side-Liquidity-Raid im Discount**, aber nur, wenn echte Daten zeigen, dass Liquidität eingesammelt wurde.
Alle 5 Minuten, 60 Coins, Bitget USDT-M, Papier/Echtgeld-Schalter wie beim jaegerbot.

| SMC-Baustein | Messung (Binance-Daten, öffentlich) |
|---|---|
| Discount | Kurs ≥ 20 % unter dem 90-Tage-Hoch |
| Sell-Side-Sweep | 15-min-Abverkauf z ≤ −4 (Rendite / Schwankung der letzten 30 Tage) |
| Liquidität eingesammelt | Open Interest ≥ 3 % unter dem 6-h-Maximum (Stops und Liquidationen ausgelöst) |
| Inducement | Anteil long positionierter Kleinanleger-Konten kleiner als vor 72 h |
| Einstieg / Ausstieg | Long zur nächsten Minute nach Kerzenschluss, raus nach 24 h, kein Stop (Hebel 1) |
| Größe | 6,25 % des Kontostands bei starkem Hebel-Abbau (OI ≤ −5 %), sonst 3,75 %; höchstens 20 gleichzeitig |

## Warum diese Form (Forschung 21.11.2026, Claude + Codex)

`botprojekte/forschung/2026-11-21-smc-neu` (Präregistrierungen, Ideen und Prüfberichte von Codex, Skripte):

- **titanbot v1** (SMC-Kerzenmuster + Optuna je Coin) hatte keine Kante: Die „Gewinner“-Configs reproduzierten nicht. Der alte Code
  liegt im Git-Tag `v1-smc-alt`.
- **Runde 1:** 13 SMC-Ideen mit echten OI-/Taker-Daten (Sweeps gleicher Hochs/Tiefs, FVG, gefangene Ausbrecher, Funding, Paare)
  → 0/13; auf 5m/≤24 h liegen sie brutto bei ≈ 0.
- **Raid im Discount** (= kapbot-Kapitulationsregel) auf **30 nie benutzten Coins**: +1,28 %/Trade netto, p 0,0005,
  29/30 Coins und jedes Jahr 2021–2026 positiv. Das ist die vierte unabhängige Bestätigung.
- **SMC-Feinschliff** (Sweep+Rückeroberung, FVG-Limit, Ausstieg an Buy-Side-Liquidität, Wochen-Sweeps, früherer 1m-Einstieg) → alle
  schlechter. Wer auf Bestätigung wartet, verpasst den Rebound.
- **Größe nach OI-Abbau** (Codex) auf frischen Coins bestanden.

Konto 12/2021–11/2026 (Bewertung alle 5 Minuten, 0,22 % Kosten + Funding):

| | Rendite/Jahr | größter Rückgang |
|---|---:|---:|
| 30 frische Coins, Hebel 1 (diese Einstellung) | **32,4 %** | **−38,9 %** |
| dieselben, doppelte Kosten | 26,1 % | −39,3 % |
| Hebel 2 (Forschung) | ~50 % | ~−70 % |

Die Rückgänge kommen von Crash-Tagen, an denen viele Coins gleichzeitig raiden. Gerade diese Raids sind im Mittel die besten
(≥ 10 in 24 h: +3,8 %/Trade), sie laufen aber gemeinsam erst weiter nach unten.

## Schalter (`settings.json`)

| Schalter | Standard | Wirkung |
|---|---|---|
| `echtgeld.aktiv` · `papier_startkapital` | **aus** · 200 | aus = Papierhandel mit echten Bitget-Kursen, Spread, Gebühren, Funding (Konto `data/papier/konto.json`, Telegram „📝 PAPIER“); an = echte Orders über `secret.json` → `titanbot` (Konto `data/echtgeld`). Je Lauf neu gelesen, Wechsel wird gemeldet, offene Positionen des anderen Modus schließen trotzdem regulär. `update.sh` behält deine Einstellung. |
| `titanbot.aktiv` · `einsatz_oi_stark` · `einsatz_normal` · `hebel` · `max_offen` | an · 0,0625 · 0,0375 · 1 · 20 | aus = keine neuen Trades, offene laufen aus |

**Kontogröße:** mindestens ~135 USDT. Sonst liegen normale Trades (3,75 %) unter Bitgets Mindestwert von 5 USDT und werden übersprungen.

**kapbot:** kapbot handelt dieselbe Regel auf 30 dieser Coins. Beide mit Echtgeld auf demselben Konto wären doppeltes Risiko,
und die Positionen desselben Symbols würden sich mischen. Wenn beide laufen, dann auf getrennten Bitget-Unterkonten.

## `secret.json` (nicht im Git)

```json
{
  "titanbot": [{"apiKey": "...", "secret": "...", "password": "..."}],
  "telegram": {"bot_token": "...", "chat_id": "..."}
}
```
Für Papier reicht `telegram`. Ein leerer `apiKey` gilt als „kein Zugang“.

## Installation auf dem Mini-PC

```sh
git clone https://github.com/Youra82/titanbot.git ~/botprojekte/titanbot && cd ~/botprojekte/titanbot
./install.sh            # .venv, Dienst titanbot.timer (alle 5 min, 1 min nach Kerzenschluss)
sudo systemctl start titanbot.service ; journalctl -u titanbot -n 40
```
Der erste Lauf lädt 90 Tage 5m-Kerzen für 60 Coins nach (≈ 15–20 min, ≈ 25 MB in `data/`), danach dauert jeder Lauf wenige Sekunden.
Bericht: `.venv/bin/python -m titanbot bericht` · Update: `./update.sh`.

## Echtgeld-Absicherung (Codex-Review 21.11.2026, zwei Prüfrunden)

- **Kein Doppelkauf:** Jede Order hat eine feste clientOid (aus dem Signal bzw. fortlaufend), die zusammen mit der reservierten Margin **vor**
  dem Senden gespeichert wird. Nach Netzfehler oder Absturz schlägt der nächste Lauf genau diese Order nach und bucht die gefüllte Menge.
  Eine unauffindbare Order wird erst nach 15 min, 3 leeren Abfragen und ohne unerklärte Börsenmenge freigegeben, sonst kommt eine Warnung.
- **Keine falsche Liquidation:** Zuerst wird die eigene Close-Order nachgeschlagen. Fehlt Menge ohne eigenen Close, gilt das erst nach
  10 min als Liquidation. Bereits realisierte Teil-Closes zählen dabei mit.
- **Gebucht wird die tatsächliche Füllung** (Menge, Kurs, Gebühren, Funding). Order-IDs werden nur einmal gebucht, und Vormerkungen zählen zu `max_offen`.
- **Fehler isoliert:** Ein Fehler im Kontoabgleich oder bei einem Ausstieg blockiert die übrigen Ausstiege nicht. Ohne gesicherte
  Echtgeld-Initialisierung gibt es keine Einstiege, und ohne Zugang laufen die Papier-Ausstiege weiter.
- **Fremde Positionen** auf demselben Bitget-Konto werden einmal am Tag gemeldet. Trotzdem gilt: **eigenes Unterkonto je Bot.**
- `update.sh` wartet auf einen laufenden Takt und pausiert den Timer bis zum Ende des Updates.
- Bewusste Grenzen: Das Konto ist ein Buchwert (offene Gewinne zählen erst beim Ausstieg), und Funding wird aus den Bitget-Sätzen des
  Haltefensters berechnet. Eine aggregierte Symbolposition, die mit einem anderen Bot geteilt wird, ist nicht sauber zuzuordnen.

## Cockpit

`cockpit.json` meldet das Papierkonto (`data/papier/konto.json`, Format wie evobot, Takt 5 min, ohne SL).

## Prüfungen

- `PYTHONPATH=src python -m unittest discover -s tests`: offline, keine Netz- oder Order-Aufrufe
- `python tools/parity_signale.py`: Live-Regel gegen alle 4.208 Forschungssignale der 60 Coins
