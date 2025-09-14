TitanBot
Ein vollautomatischer Trading-Bot für Krypto-Futures auf der Bitget-Börse, basierend auf "Smart Money Concepts" (SMC).

Dies ist ein vollautomatischer Trading-Bot für Krypto-Futures auf der Bitget-Börse. Das System wurde für den Betrieb auf einem Ubuntu-Server entwickelt und umfasst neben dem Live-Trading-Modul eine hochentwickelte Pipeline zur Strategie-Optimierung und -Analyse. Es unterstützt die Verwaltung mehrerer Accounts und ein dynamisches, zweistufiges Risikomanagement mit Trailing Stop.

## Kernstrategie (Smart Money Concepts)
Der Bot implementiert eine auf "Smart Money Concepts" (SMC) basierende Trendfolgestrategie.

Handelsthese: Preisbewegungen werden von institutionellem Orderflow angetrieben. Die Strategie zielt darauf ab, Zonen hoher Liquidität zu identifizieren ("Order Blocks"), in denen institutionelle Akteure wahrscheinlich in den Markt eintreten, nachdem ein bestehender Trend bestätigt wurde.

Signale:

Break of Structure (BoS): Der Bot identifiziert eine starke Trendbestätigung, indem der Preis ein vorheriges signifikantes Hoch (in einem Aufwärtstrend) oder Tief (in einem Abwärtstrend) durchbricht.

Order Block (OB): Nach einem BoS identifiziert der Bot die letzte gegensätzliche Kerze vor der starken Trendbewegung. Diese Zone wird als hochwahrscheinlicher Einstiegsbereich ("Order Block") markiert.

Einstieg: Der Bot platziert eine Limit-Order direkt am Rande des identifizierten Order Blocks, um auf einen Rücksetzer des Preises zu warten.

Ausstieg: Ein fester Stop-Loss wird auf der gegenüberliegenden Seite des Order Blocks platziert. Der initiale Take-Profit wird durch das in der Konfiguration definierte risk_reward_ratio bestimmt.

Dynamisches Risikomanagement:

Positionsgröße: Die Größe jeder Position wird dynamisch berechnet, um pro Trade nur einen festen Prozentsatz des verfügbaren Kapitals zu riskieren.

Zweistufiger Take-Profit: Die Position startet mit einem festen Take-Profit. Sobald ein vordefinierter Gewinn erreicht ist (z.B. 2:1 Risk/Reward), wird der feste TP storniert und durch eine native Trailing-Stop-Order der Börse ersetzt. Dies sichert Gewinne ab und ermöglicht gleichzeitig die Mitnahme weiterer Profite in starken Trendphasen ("die Welle reiten").

## Systemarchitektur
Das Projekt ist in drei Kernkomponenten unterteilt:

Live-Trading-Modul (/code/strategies/envelope)

Der eigentliche Bot (run.py), der per Cronjob ausgeführt wird.

Verwaltet seinen Zustand (z.B. Zeitstempel des letzten Signals) über eine lokale SQLite-Datenbank, um Duplikate zu vermeiden und nach Neustarts robust zu bleiben.

Kommuniziert über ein Utility-Modul (bitget_futures.py) mit der Bitget API.

Versendet Status-Updates, Handelssignale und kritische Fehler per Telegram.

Optimierungs- & Analyse-Pipeline (/code/analysis)

Ein leistungsstarkes Werkzeug (run_optimization_pipeline.sh), um die besten Strategie-Parameter für ein gegebenes Handelspaar und einen Zeitraum zu finden.

Zweistufige Optimierung:

Globale Suche (Pymoo): Ein genetischer Algorithmus durchsucht den gesamten Parameterraum, um eine Gruppe vielversprechender Kandidaten zu finden (Profit vs. Drawdown).

Lokale Verfeinerung (Optuna): Die besten Kandidaten werden einer detaillierten lokalen Optimierung unterzogen, um die Parameter zu perfektionieren.

Dedizierter Backtest-Modus: Ermöglicht das gezielte Testen einer einzelnen config.json-Datei gegen historische Daten, um Hypothesen schnell zu validieren.

Installation
Führe die folgenden Schritte auf einem frischen Ubuntu-Server (empfohlen: 22.04 LTS) aus, um den Bot einzurichten.

Projekt klonen
Lade den Code von GitHub auf deinen Server.

Bash

>git clone https://github.com/Youra82/titanbot.git

Installations-Skript ausführen
Dieses Skript aktualisiert den Server, installiert Python-Abhängigkeiten und richtet die virtuelle Umgebung ein.

Bash

>cd titanbot

>chmod +x install.sh

>./install.sh

API-Schlüssel eintragen
Bearbeite die secret.json-Datei und trage deine API-Daten ein.

Bash

>nano secret.json

Fülle die Felder für envelope (deine Live-Bitget-Keys) und optional für telegram aus. Speichere mit Strg + X, dann Y, dann Enter.

Strategie finden und konfigurieren
Führe die Analyse-Pipeline aus, um die beste Konfiguration für dein gewünschtes Handelspaar zu finden.

Bash

>chmod +x run_optimization_pipeline.sh

>./run_optimization_pipeline.sh

Wähle im Menü Option 1.

Folge den Anweisungen (Handelspaar, Zeitraum etc.).

Kopiere am Ende des erfolgreichen Laufs die ausgegebene Konfiguration in die Datei code/strategies/envelope/config.json.

Automatisierung per Cronjob einrichten
Füge einen Cronjob hinzu, damit der Bot automatisch alle 5 Minuten ausgeführt wird.

Bash

>crontab -e

Füge die folgende Zeile am Ende der Datei ein:

Code-Snippet

>*/5 * * * * flock -n /home/ubuntu/titanbot/bot.lock bash /home/ubuntu/titanbot/code/run_envelope.sh >> /home/ubuntu/titanbot/logs/cron.log 2>&1

Speichere und schließe die Datei. Der Bot ist nun live.

Bot-Verwaltung & Analyse
Diese Befehle werden im Hauptverzeichnis /home/ubuntu/titanbot ausgeführt.

Bot-Code aktualisieren
Dieses Skript lädt die neueste Version des Codes von GitHub herunter, ohne deine secret.json zu überschreiben.

Bash

>chmod +x update_bot.sh (einmalig)

>./update_bot.sh

>tail -n 100 ~/stbot/logs/cron.log

Bei Problemen mit dem ./update_bot.sh:

>git reset --hard HEAD

>git stash drop

>chmod +x update_bot.sh

>./update_bot.sh


Strategien finden & testen
Dies ist deine Steuerzentrale für alle Offline-Analysen.

Bash

>./run_optimization_pipeline.sh

Option 1: Startet die komplette 2-Stufen-Optimierung, um die beste config.json zu finden.

Option 2: Startet einen Einzel-Backtest mit der aktuell in config.json gespeicherten Strategie.

Option 3: Löscht die zwischengespeicherten Marktdaten.

Live-Logs ansehen
Zeigt die Aktivitäten des Bots in Echtzeit an.

Bash

>tail -f logs/livetradingbot.log

Mit Strg + C beendest du die Anzeige.

Automatisierung stoppen/starten
Um den Bot zu pausieren (z.B. für Wartungsarbeiten).

Bash

>crontab -e

Setze ein #-Zeichen an den Anfang der Zeile des Bots, um sie zu deaktivieren.

Code-Snippet

Entferne das #-Zeichen, um ihn wieder zu starten.

(Optional) Ersten Lauf manuell starten
Nachdem der Cronjob eingerichtet ist, würde der Bot innerhalb der nächsten 5 Minuten automatisch starten. Wenn du sofort sehen möchtest, ob alles funktioniert, kannst du den ersten Lauf direkt manuell anstoßen:

>bash code/run_envelope.sh

\
✅ Requirements
-------------
Python 3.12.x
\
See [requirements.txt](https://github.com/RobotTraders/LiveTradingBots/blob/main/requirements.txt) for the specific Python packages


\
📃 License
-------------
This project is licensed under the [GNU General Public License](LICENSE) - see the LICENSE file for details.


\
⚠️ Disclaimer
-------------
All this material are for educational and entertainment purposes only. It is not financial advice nor an endorsement of any provider, product or service. The user bears sole responsibility for any actions taken based on this information, and Robot Traders and its affiliates will not be held liable for any losses or damages resulting from its use. 
