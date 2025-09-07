Envelope Trading Bot


Dies ist ein vollautomatischer Trading-Bot für Krypto-Futures auf der Bitget-Börse. Das System wurde für den Betrieb auf einem Ubuntu-Server entwickelt und umfasst neben dem Live-Trading-Modul eine hochentwickelte Pipeline zur Strategie-Optimierung und -Analyse.

Kernstrategie
Der Bot implementiert eine Mean-Reversion-Strategie, die auf prozentualen "Envelopes" (Hüllkurven) um einen gleitenden Durchschnitt basiert.

Handelsthese: Der Preis eines Assets tendiert dazu, nach einer starken Bewegung wieder zu seinem kurzfristigen Durchschnittswert zurückzukehren.

Signale:

Long-Einstieg: Der Preis fällt unter eine oder mehrere vordefinierte untere Hüllkurven. Der Bot platziert Limit-Kauf-Orders auf diesen Niveaus.

Short-Einstieg: Der Preis steigt über eine oder mehrere vordefinierte obere Hüllkurven. Der Bot platziert Limit-Verkaufs-Orders auf diesen Niveaus.

Ausstieg: Der Take-Profit für jede Position liegt auf dem gleitenden Durchschnitt selbst, basierend auf der Annahme der Rückkehr zum Mittelwert. Ein Stop-Loss sichert die Positionen zusätzlich ab.

Dynamisches Risiko: Der eingesetzte Hebel wird dynamisch auf Basis der aktuellen Marktvolatilität (gemessen durch die ATR - Average True Range) angepasst, um das Risiko in unruhigen Marktphasen zu reduzieren.

Systemarchitektur
Das Projekt ist in drei Kernkomponenten unterteilt:

Live-Trading-Modul (/code/strategies/envelope)

Der eigentliche Bot, der per Cronjob ausgeführt wird (run.py).

Verwaltet seinen Zustand (z.B. "in Position" oder "suche Trade") über eine lokale SQLite-Datenbank, um auch nach Neustarts robust zu bleiben.

Kommuniziert über ein Utility-Modul (bitget_futures.py) mit der Bitget API.

Versendet Status-Updates und kritische Fehler per Telegram.

Optimierungs- & Analyse-Pipeline (/code/analysis)

Ein leistungsstarkes Werkzeug (run_optimization_pipeline.sh), um die besten Strategie-Parameter für ein gegebenes Handelspaar und einen Zeitraum zu finden.

Zweistufige Optimierung:

Globale Suche (Pymoo): Ein genetischer Algorithmus (NSGA-II) durchsucht den gesamten Parameterraum, um eine Gruppe vielversprechender Kandidaten zu finden.

Lokale Verfeinerung (Optuna): Die besten Kandidaten werden einer detaillierten lokalen Optimierung unterzogen, um die Parameter zu perfektionieren.

Dedizierter Backtest-Modus: Ermöglicht das gezielte Testen einer einzelnen config.json-Datei gegen historische Daten, um Hypothesen schnell zu validieren.

Performance-Analyse (/code/utilities/tax_endpoint_analysis.py)

Ein Jupyter Notebook (run_pnl.ipynb) nutzt dieses Modul, um die tatsächliche Handels-Performance direkt vom Steuer-Endpunkt der Börse abzurufen. Dies dient als "Source of Truth" und ist unabhängig von den Bot-Logs.

Installation
Führe die folgenden Schritte auf einem frischen Ubuntu-Server (empfohlen: 22.04 LTS) aus, um den Bot einzurichten.

Projekt klonen
Lade den Code von GitHub auf deinen Server.

Bash

>git clone https://github.com/Youra82/LiveTradingBots.git

Installations-Skript ausführen
Dieses Skript aktualisiert den Server, installiert Python-Abhängigkeiten und richtet die virtuelle Umgebung ein.

Bash

>cd LiveTradingBots

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

>*/5 * * * * flock -n /home/ubuntu/LiveTradingBots/bot.lock bash /home/ubuntu/LiveTradingBots/code/run_envelope.sh >> /home/ubuntu/LiveTradingBots/logs/cron.log 2>&1

Speichere und schließe die Datei. Der Bot ist nun live.

Bot-Verwaltung & Analyse
Diese Befehle werden im Hauptverzeichnis /home/ubuntu/LiveTradingBots ausgeführt.

Bot-Code aktualisieren
Dieses Skript lädt die neueste Version des Codes von GitHub herunter, ohne deine secret.json zu überschreiben.

Bash

>chmod +x update_bot.sh (einmalig)

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
