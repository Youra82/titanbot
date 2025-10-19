# TitanBot 🤖

Ein selbstoptimierender, **SMC-gesteuerter** (Smart Money Concepts) Trading-Bot für Krypto-Futures auf der Bitget-Börse. Er identifiziert Marktstrukturen wie Order Blocks (OBs) und Fair Value Gaps (FVGs), um Handelsentscheidungen zu treffen.

Dieses System ist für den autonomen Betrieb auf einem Ubuntu-Server konzipiert und umfasst eine Pipeline zur **Optimierung von SMC- und Risiko-Parametern** sowie zum Live-Handel.

---

## Features 🧠

* **SMC-basierte Analyse:** Identifiziert automatisch wichtige Marktstrukturen (BOS, CHoCH, Order Blocks, Fair Value Gaps) zur Fundierung von Handelsentscheidungen.
* **Automatisierte Optimierungs-Pipeline:** Ein einziges Skript (`run_pipeline.sh`) steuert den Prozess der Datenanalyse und der **Optimierung der SMC- und Risikoparameter** mithilfe von `optuna` und Backtesting.
* **Dynamisches Risikomanagement:** Die Positionsgröße wird vor jedem Trade dynamisch auf Basis des *aktuellen* Kontostandes berechnet, um den Zinseszinseffekt optimal zu nutzen.
* **Robust & Sicher:** Entwickelt für einen stabilen 24/7-Betrieb mit Sicherheits-Checks, Schutz vor Doppel-Trades pro Kerze und einem "Guardian"-Mechanismus, der kritische Fehler abfängt und meldet.
* **Anpassbare Handelslogik:** Die konkrete Einstiegslogik (z.B. Entry bei FVG-Touch) ist in einer separaten Datei (`trade_logic.py`) definiert und kann leicht angepasst werden.

---

## Installation & Setup 🛠️

Führe diese Schritte aus, um den TitanBot auf einem frischen Ubuntu-Server in Betrieb zu nehmen.

### 1. Projekt klonen

```bash
# Ersetze <DEIN_GITHUB_REPO_LINK> mit dem Link zu deinem neuen TitanBot Repo
git clone https://github.com/Youra82/titanbot.git
cd titanbot
```


### 2\. Installations-Skript ausführen

Dieses Skript ist der wichtigste Schritt. Es installiert alle Abhängigkeiten (ohne Tensorflow), richtet die Python-Umgebung ein und **macht alle anderen Skripte im Projekt automatisch ausführbar**.

```bash
bash ./install.sh
```

*(Hinweis: Das `install.sh`-Skript selbst muss eventuell leicht angepasst werden, um die Tensorflow-spezifischen Teile zu entfernen, falls vorhanden. Die `requirements.txt` sollte bereits korrekt sein.)*

### 3\. API-Schlüssel eintragen

Erstelle deine persönliche `secret.json`-Datei aus der Vorlage (falls vorhanden, ansonsten manuell) und trage deine API-Schlüssel von Bitget sowie deine Telegram-Daten ein.

```bash
# Falls eine Vorlage existiert:
# cp secret.json.example secret.json
nano secret.json
```

**Beispielinhalt für `secret.json`:**

```json
{
    "jaegerbot": [
        {
            "name": "DeinAccountName",
            "apiKey": "DEIN_API_KEY",
            "secret": "DEIN_SECRET_KEY",
            "password": "DEIN_API_PASSWORT"
        }
    ],
    "telegram": {
        "bot_token": "DEIN_TELEGRAM_BOT_TOKEN",
        "chat_id": "DEINE_TELEGRAM_CHAT_ID"
    }
}
```

> Speichere mit `Strg + X`, dann `Y`, dann `Enter`.

### 4\. Strategien für den Handel aktivieren

Bearbeite die `settings.json`, um festzulegen, welche deiner optimierten SMC-Strategien (Symbol/Timeframe-Kombinationen) im Live-Handel aktiv sein sollen.

```bash
nano settings.json
```

Stelle sicher, dass die `"symbol"` und `"timeframe"` Einträge mit den Namen deiner `config_...json`-Dateien übereinstimmen.

### 5\. Automatisierung per Cronjob einrichten

Richte den Cronjob ein, der den `master_runner` regelmäßig startet (z.B. alle 5 oder 15 Minuten, je nach kürzestem Timeframe deiner Strategien).

```bash
crontab -e
```

Füge die folgende **eine Zeile** am Ende der Datei ein (passe den Pfad `/root/titanbot` an, falls dein Bot woanders liegt):

```
# Starte den TitanBot Master-Runner alle 5 Minuten
*/5 * * * * cd /root/titanbot && /root/titanbot/.venv/bin/python3 /root/titanbot/master_runner.py >> /root/titanbot/logs/cron.log 2>&1
```

-----

## Workflow & Befehlsreferenz ⚙️

Dies ist deine Kommandozentrale für die Erstellung, Analyse und Verwaltung deiner SMC-Handelsstrategien. Alle Befehle funktionieren direkt nach der Ausführung von `install.sh`.

### 1\. Pipeline: SMC-Strategien optimieren

Dieser Prozess lädt historische Daten, führt Tausende von Backtests mit verschiedenen SMC- (`swingsLength`, `ob_mitigation`) und Risiko-Parametern (`RR`, `Leverage` etc.) durch und speichert die besten Kombinationen. **Es findet kein KI-Training mehr statt.**

```bash
./run_pipeline.sh
```

Nach Abschluss werden neue oder aktualisierte `config_...json`-Dateien in `src/titanbot/strategy/configs/` erstellt.

### 2\. Analyse: Performance der Strategien bewerten

Dieses Skript bietet Modi, um die erstellten Strategien zu analysieren. (Hinweis: Die Funktionalität von `show_results.sh` muss eventuell an die SMC-Logik angepasst werden, falls die Backtest-Ausgaben sich geändert haben).

```bash
./show_results.sh
```

Dabei werden `.csv`-Dateien mit den detaillierten Equity-Kurven im Hauptverzeichnis erstellt (wenn der Backtester entsprechend angepasst wurde).

### 3\. Reporting: Ergebnisse an Telegram senden

Verwende diese Befehle, um deine Analyse-Ergebnisse direkt auf dein Handy zu bekommen. Funktioniert, wenn die `.csv`-Dateien im korrekten Format generiert werden.

  * **CSV-Rohdaten senden:**

    ```bash
    ./send_report.sh optimal_portfolio_equity.csv
    # oder ./send_report.sh manual_portfolio_equity.csv
    ```

  * **Grafische Diagramme senden:**

    ```bash
    ./show_chart.sh optimal_portfolio_equity.csv
    # oder ./show_chart.sh manual_portfolio_equity.csv
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
    grep -i "ERROR" logs/cron.log | tail -n 500
    ```

  * **Bot auf die neueste Version aktualisieren:**

    ```bash
    ./update.sh
    ```

  * **Automatisierte Tests ausführen (nach jedem Update empfohlen):**
    *(Hinweis: Die Tests in `tests/` müssen komplett neu geschrieben werden, um die SMC-Logik zu testen\!)*

    ```bash
    ./run_tests.sh
    ```

  * **Projektstatus und Struktur anzeigen:**

    ```bash
    ./show_status.sh
    ```

  * **Alte Konfigurationen für einen Neustart löschen:**

    ```bash
    # Alle alten Konfigurationen löschen
    rm -f src/titanbot/strategy/configs/config_*.json

    # Überprüfen, ob der Ordner leer ist
    ls -l src/titanbot/strategy/configs/
    ```

### 5\. Backup auf GitHub

Sichere den kompletten Stand deines Bots inklusive aller Konfigurationen auf GitHub. **WARNUNG:** Führe dies nur aus, wenn dein Repository auf **"Privat"** gestellt ist, da deine Konfigurationen und eventuell deine `secret.json` (falls nicht in `.gitignore`) hochgeladen werden\!

```bash
# Sicherstellen, dass secret.json ignoriert wird (in .gitignore prüfen!)
# git add .
# git commit -m "Vollständiges Projekt-Backup TitanBot"
# git push origin main # Ggf. '--force', wenn du bewusst überschreiben willst
```

-----

## ⚠️ Disclaimer

Dieses Material dient ausschließlich zu Bildungs- und Unterhaltungszwecken. Es handelt sich nicht um eine Finanzberatung. Der Nutzer trägt die alleinige Verantwortung für alle Handlungen. Der Autor haftet nicht für etwaige Verluste.

```

---

**Wichtige Hinweise:**

1.  **GitHub Repo:** Ersetze `<DEIN_GITHUB_REPO_LINK>` im `git clone`-Befehl durch den tatsächlichen Link deines neuen TitanBot-Repositories.
2.  **`install.sh`:** Überprüfe kurz `install.sh`, ob dort noch spezifische Befehle für `tensorflow` oder `scikit-learn` drin sind, die entfernt werden können (obwohl es meistens nur `pip install -r requirements.txt` ist).
3.  **Tests:** Die alten Tests in `tests/` sind **ungültig**. Du müsstest neue Tests schreiben, die die `SMCEngine` und die neue `trade_logic` prüfen.
4.  **`show_results.sh` / `.csv`-Dateien:** Die Skripte zum Anzeigen und Senden von Ergebnissen (`show_results.sh`, `send_report.sh`, `show_chart.sh`) setzen voraus, dass der neue `backtester.py` (bzw. die darauf aufbauenden Skripte wie `portfolio_simulator.py`) weiterhin `.csv`-Dateien in einem ähnlichen Format wie zuvor ausgibt. Das musst du ggf. sicherstellen oder diese Skripte anpassen.
```
