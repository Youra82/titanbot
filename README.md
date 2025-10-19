# JaegerBot 🤖

Ein selbstoptimierender, KI-gesteuerter Trading-Bot für Krypto-Futures auf der Bitget-Börse, basierend auf einem neuronalen Netz (ANN).

Dieses System ist für den autonomen Betrieb auf einem Ubuntu-Server konzipiert und umfasst eine hochentwickelte Pipeline zur Modellerstellung, Strategie-Optimierung und zum Live-Handel.

-----

## Features 🧠

  * **KI-gestützte Vorhersagen:** Nutzt ein neuronales Netz, um signifikante Preisbewegungen vorherzusagen und kurzfristiges Marktrauschen zu ignorieren.
  * **Vollautomatische Pipeline:** Ein einziges Skript (`run_pipeline.sh`) steuert den gesamten Prozess von der Datenanalyse über das KI-Training bis zur Optimierung der Risikoparameter.
  * **Dynamisches Risikomanagement:** Die Positionsgröße wird vor jedem Trade dynamisch auf Basis des *aktuellen* Kontostandes berechnet, um den Zinseszinseffekt optimal zu nutzen.
  * **Robust & Sicher:** Entwickelt für einen stabilen 24/7-Betrieb mit Sicherheits-Checks, Schutz vor Doppel-Trades und einem "Guardian"-Mechanismus, der kritische Fehler abfängt und meldet.
  * **Kontext-basiertes Trading (optional):** Kann übergeordnete Trends analysieren (z.B. mit einem MACD-Filter), um Trades mit geringerer Wahrscheinlichkeit intelligent herauszufiltern.

-----

## Installation & Setup 🛠️

Führe diese Schritte aus, um den JaegerBot auf einem frischen Ubuntu-Server in Betrieb zu nehmen.

### 1\. Projekt klonen

```bash
git clone https://github.com/Youra82/jaegerbot.git
cd jaegerbot
```

### 2\. Installations-Skript ausführen

Dieses Skript ist der wichtigste Schritt. Es installiert alle Abhängigkeiten, richtet die Python-Umgebung ein und **macht alle anderen Skripte im Projekt automatisch ausführbar**.

```bash
bash ./install.sh
```

### 3\. API-Schlüssel eintragen

Erstelle deine persönliche `secret.json`-Datei aus der Vorlage und trage deine API-Schlüssel von Bitget sowie deine Telegram-Daten ein.

```bash
cp secret.json.example secret.json
nano secret.json
```

> Speichere mit `Strg + X`, dann `Y`, dann `Enter`.

### 4\. Strategien für den Handel aktivieren

Bearbeite die `settings.json`, um festzulegen, welche deiner optimierten Strategien im Live-Handel aktiv sein sollen.

```bash
nano settings.json
```

### 5\. Automatisierung per Cronjob einrichten

Richte den Cronjob ein, der den `master_runner` regelmäßig startet.

```bash
crontab -e
```

Füge die folgende **eine Zeile** am Ende der Datei ein (passe den Pfad an, falls nötig):

```
# Starte den JaegerBot Master-Runner alle 15 Minuten
*/15 * * * * cd /home/ubuntu/jaegerbot && /home/ubuntu/jaegerbot/.venv/bin/python3 /home/ubuntu/jaegerbot/master_runner.py >> /home/ubuntu/jaegerbot/logs/cron.log 2>&1
```

-----

## Workflow & Befehlsreferenz⚙️

Dies ist deine Kommandozentrale für die Erstellung, Analyse und Verwaltung deiner Handelsstrategien. Alle Befehle funktionieren direkt nach der Ausführung von `install.sh`.

### 1\. Pipeline: Strategien von Grund auf neu erstellen

Dieser Prozess findet neue Strategien, trainiert die KI-Modelle und optimiert die Handelsparameter.

```bash
./run_pipeline.sh
```

Nach Abschluss werden neue `config_...json`-Dateien in `src/jaegerbot/strategy/configs/` erstellt.

### 2\. Analyse: Performance der Strategien bewerten

Dieses Skript bietet drei Modi, um die erstellten Strategien zu analysieren.

```bash
./show_results.sh
```

Dabei werden `.csv`-Dateien mit den detaillierten Equity-Kurven im Hauptverzeichnis erstellt.

### 3\. Reporting: Ergebnisse an Telegram senden

Verwende diese Befehle, um deine Analyse-Ergebnisse direkt auf dein Handy zu bekommen.

  * **CSV-Rohdaten senden:**

    ```bash
    ./send_report.sh optimal_portfolio_equity.csv
    ./send_report.sh manual_portfolio_equity.csv
    ```

  * **Grafische Diagramme senden:**

    ```bash
    ./show_chart.sh optimal_portfolio_equity.csv
    ./show_chart.sh manual_portfolio_equity.csv
    ```

### 4\. Wartung & Verwaltung

  * **Logs live mitverfolgen (wichtigster Befehl):**

    ```bash
    tail -f logs/cron.log
    ```

  * **Die letzten 500 Log-Einträge anzeigen:**

    ```bash
    tail -n 500 logs/cron.log
    ```

  * **Alle Fehler-Einträge anzeigen:**

    ```bash
    grep -i "ERROR" logs/cron.log
    ```

  * **Die letzten 500 Fehler-Einträge anzeigen:**

    ```bash
    grep -i "ERROR" logs/cron.log | tail -n 500
    ```

  * **Bot auf die neueste Version aktualisieren:**

    ```bash
    ./update.sh
    ```

  * **Automatisierte Tests ausführen (nach jedem Update empfohlen):**

    ```bash
    ./run_tests.sh
    ```

  * **Projektstatus und Struktur anzeigen:**

    ```bash
    ./show_status.sh
    ```

  * **Alte Modelle & Konfigurationen für einen Neustart löschen:**

    ```bash
    # Alle alten Konfigurationen löschen
    rm -f src/jaegerbot/strategy/configs/config_*.json

    # Alle alten KI-Modelle löschen
    rm -f artifacts/models/*

    # Überprüfen, ob die Ordner leer sind
    ls -l src/jaegerbot/strategy/configs/
    ls -l artifacts/models/
    ```

### 5\. Backup auf GitHub

Sichere den kompletten Stand deines Bots inklusive aller Modelle und Konfigurationen auf GitHub. **WARNUNG:** Führe dies nur aus, wenn dein Repository auf "Privat" gestellt ist\!

```bash
# (Optional) .gitignore anpassen, um alle Dateien einzuschließen
# nano .gitignore

git add .
git commit -m "Vollständiges Projekt-Backup"
git push --force origin main
```

-----

## ⚠️ Disclaimer

Dieses Material dient ausschließlich zu Bildungs- und Unterhaltungszwecken. Es handelt sich nicht um eine Finanzberatung. Der Nutzer trägt die alleinige Verantwortung für alle Handlungen. Der Autor haftet nicht für etwaige Verluste.
