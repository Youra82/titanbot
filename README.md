# ⚡ TitanBot - High-Performance Trading System

<div align="center">

![TitanBot Logo](https://img.shields.io/badge/TitanBot-v2.0-blue?style=for-the-badge)
[![Python](https://img.shields.io/badge/Python-3.8+-green?style=for-the-badge&logo=python)](https://www.python.org/)
[![CCXT](https://img.shields.io/badge/CCXT-4.3.5-red?style=for-the-badge)](https://github.com/ccxt/ccxt)
[![Optuna](https://img.shields.io/badge/Optuna-4.5-purple?style=for-the-badge)](https://optuna.org/)
[![License](https://img.shields.io/badge/License-MIT-yellow?style=for-the-badge)](LICENSE)

**Ein leistungsstarker Trading-Bot mit dynamischem Stop-Loss, Multi-Asset-Support und fortgeschrittenem Risikomanagement**

[Features](#-features) • [Installation](#-installation) • [Optimierung](#-optimierung) • [Live-Trading](#-live-trading) • [Monitoring](#-monitoring) • [Wartung](#-wartung)

</div>

---

## 📊 Übersicht

TitanBot ist ein hochentwickelter Trading-Bot mit Fokus auf Performance und Risikokontrolle. Das System verfügt über dynamische Stop-Loss-Mechanismen, intelligente Positionsgrößenverwaltung und kann bis zu mehrere Positionen gleichzeitig managen.

### 🧭 Trading-Logik (Kurzfassung)
- **SMC-Momentum-Hybrid**: Nutzt Smart-Money-Concepts (Liquidity Sweeps/Structure Breaks) kombiniert mit Momentum-Indikatoren (z.B. MACD/RSI) für Entry-Qualität.
- **Dynamischer Stop**: SL-Level passen sich an Volatilität/ATR an; optionaler Trailing-SL folgt dem Trend.
- **Positions-Limit**: `max_open_positions` begrenzt parallele Trades, priorisiert höchste Signal-Qualität.
- **Execution**: CCXT-Orders mit Fee/Slippage-Annahmen aus Backtests; Telegram-Notifications für State-Änderungen.

Architektur-Skizze:
```
OHLCV → Momentum/Vol-Stack → Signal-Ranking → Risk Engine (SL/TP/Trail) → Order Router (CCXT)
```

### 🎯 Hauptmerkmale

- **🚀 High Performance**: Optimiert für schnelle Ausführung und niedrige Latenz
- **🎯 Dynamic Stop-Loss**: Intelligente, adaptive Stop-Loss-Strategien
- **💰 Position Management**: Maximale Anzahl offener Positionen konfigurierbar
- **📈 Multi-Asset**: Handel mehrerer Kryptowährungen parallel
- **🔧 Auto-Optimization**: Vollautomatische Parameteroptimierung
- **📊 Advanced Analytics**: Umfassende Performance-Analysen
- **🛡️ Risk Control**: Fortgeschrittenes Risikomanagement
- **🔔 Telegram Integration**: Real-time Notifications

---

## 🚀 Features

### Trading Features
- ✅ Dynamischer Stop-Loss (anpassbar an Volatilität)
- ✅ Maximale offene Positionen: Konfigurierbar (Standard: 3)
- ✅ Multi-Asset Trading (BTC, ETH, SOL, XRP, AAVE)
- ✅ Multiple Timeframes (5m, 2h, 4h, 6h)
- ✅ Optionaler MACD-Filter
- ✅ Intelligentes Position Sizing
- ✅ Take-Profit Management
- ✅ Trailing Stop-Loss

### Technical Features
- ✅ Optuna Hyperparameter-Optimierung
- ✅ Fortgeschrittene technische Indikatoren
- ✅ Volume-basierte Analysen
- ✅ Walk-Forward-Testing
- ✅ Backtesting mit realistischer Simulation
- ✅ Performance-Tracking und Reporting

---

## 📋 Systemanforderungen

### Hardware
- **CPU**: Multi-Core Prozessor (i5 oder besser empfohlen)
- **RAM**: Minimum 4GB, empfohlen 8GB+
- **Speicher**: 2GB freier Speicherplatz

### Software
- **OS**: Linux (Ubuntu 20.04+), macOS, Windows 10/11
- **Python**: Version 3.8 oder höher
- **Git**: Für Repository-Verwaltung

---

## 💻 Installation

### 1. Repository klonen

```bash
git clone <repository-url>
cd titanbot
```

### 2. Automatische Installation

```bash
# Linux/macOS
chmod +x install.sh
./install.sh

# Windows (PowerShell)
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

Das Installations-Script:
- ✅ Erstellt virtuelle Python-Umgebung
- ✅ Installiert alle Dependencies
- ✅ Erstellt Verzeichnisstruktur
- ✅ Initialisiert Konfigurationen

### 3. API-Credentials konfigurieren

Erstelle `secret.json`:

```json
{
  "titanbot": [
    {
      "name": "Binance Main",
      "exchange": "binance",
      "apiKey": "DEIN_API_KEY",
      "secret": "DEIN_SECRET_KEY",
      "options": {
        "defaultType": "future"
      }
    }
  ],
  "telegram": {
    "bot_token": "DEIN_BOT_TOKEN",
    "chat_id": "DEINE_CHAT_ID"
  }
}
```

⚠️ **Sicherheit**:
- Niemals `secret.json` committen!
- Nur API-Keys ohne Withdrawal-Rechte
- IP-Whitelist aktivieren
- 2FA aktivieren

### 4. Trading-Strategien konfigurieren

Bearbeite `settings.json`:

```json
{
  "live_trading_settings": {
    "use_auto_optimizer_results": false,
    "max_open_positions": 3,
    "active_strategies": [
      {
        "symbol": "BTC/USDT:USDT",
        "timeframe": "4h",
        "use_macd_filter": false,
        "active": true
      },
      {
        "symbol": "ETH/USDT:USDT",
        "timeframe": "6h",
        "use_macd_filter": false,
        "active": true
      }
    ]
  }
}
```

**Wichtige Parameter**:
- `max_open_positions`: Maximale Anzahl gleichzeitiger Positionen (Standard: 3)
- `symbol`: Handelspaar
- `timeframe`: Zeitrahmen (5m, 2h, 4h, 6h)
- `use_macd_filter`: MACD-Filter aktivieren
- `active`: Strategie aktivieren/deaktivieren

---

## 🎯 Optimierung & Training

### Vollständige Pipeline (Empfohlen)

```bash
./run_pipeline.sh
```

Pipeline-Schritte:
1. **Aufräumen** (Optional): Alte Configs löschen
2. **Symbol-Auswahl**: Handelspaare wählen
3. **Timeframe-Auswahl**: Zeitrahmen konfigurieren
4. **Daten-Download**: Historische Daten laden
5. **Optimierung**: Parameter mit Optuna optimieren
6. **Backtest**: Strategien validieren
7. **Deployment**: Configs für Live-Trading erstellen

### Manuelle Optimierung

```bash
source .venv/bin/activate
python src/titanbot/analysis/optimizer.py
```

**Erweiterte Optionen**:
```bash
# Spezifische Symbole
python src/titanbot/analysis/optimizer.py --symbols BTC ETH SOL

# Mehr Trials
python src/titanbot/analysis/optimizer.py --trials 300

# Walk-Forward Analyse
python src/titanbot/analysis/optimizer.py --walk-forward
```

---

## 🔴 Live Trading

### Start des Live-Trading

```bash
# Master Runner starten (alle aktiven Strategien)
python master_runner.py
```

### Manuell starten / Cronjob testen
Sofortige Ausführung auslösen (ohne 15-Minuten-Cron-Intervall):

```bash
cd /home/ubuntu/titanbot && /home/ubuntu/titanbot/.venv/bin/python3 /home/ubuntu/titanbot/master_runner.py
```

Der Master Runner:
- ✅ Verwaltet alle aktiven Strategien
- ✅ Überwacht `max_open_positions` Limit
- ✅ Führt dynamisches Stop-Loss Management durch
- ✅ Loggt alle Trading-Aktivitäten
- ✅ Sendet Telegram-Benachrichtigungen

### Automatischer Start

```bash
./run_pipeline_automated.sh
```

### Als Systemd Service (Linux)

```bash
sudo nano /etc/systemd/system/titanbot.service
```

```ini
[Unit]
Description=TitanBot Trading System
After=network.target

[Service]
Type=simple
User=your-user
WorkingDirectory=/path/to/titanbot
ExecStart=/path/to/titanbot/.venv/bin/python master_runner.py
Restart=always
RestartSec=10
Environment="PYTHONUNBUFFERED=1"

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable titanbot
sudo systemctl start titanbot
sudo systemctl status titanbot
```

---

## 📊 Monitoring & Status

### Status-Dashboard

```bash
./show_status.sh        # Vollständiger Status
./show_results.sh       # Performance-Ergebnisse
./show_chart.sh         # Charts generieren
```

### Real-time Monitoring

```bash
# Live-Trading Logs
tail -f logs/live_trading_*.log

# Nur Trades
grep -i "opened\|closed\|profit" logs/live_trading_*.log

# Fehler-Logs
tail -f logs/error_*.log
```

### Chart-Generierung

```bash
# Equity-Curve generieren
./show_chart.sh

# Per Telegram senden
python generate_and_send_chart.py
```

### Performance-Analyse

```bash
# Equity vergleichen
python -c "
import pandas as pd
manual = pd.read_csv('manual_portfolio_equity.csv')
optimal = pd.read_csv('optimal_portfolio_equity.csv')
print('Manual ROI:', (manual['equity'].iloc[-1] / manual['equity'].iloc[0] - 1) * 100, '%')
print('Optimal ROI:', (optimal['equity'].iloc[-1] / optimal['equity'].iloc[0] - 1) * 100, '%')
"
```

---

## 🛠️ Wartung & Pflege

### Regelmäßige Wartung

#### Updates installieren

```bash
./update.sh
```

#### Log-Rotation

```bash
# Logs komprimieren (>30 Tage)
find logs/ -name "*.log" -type f -mtime +30 -exec gzip {} \;

# Alte Logs löschen (>90 Tage)
find logs/ -name "*.log.gz" -type f -mtime +90 -delete
```

### Vollständiges Aufräumen

#### Konfigurationen zurücksetzen

```bash
# Generierte Configs löschen
rm -f src/titanbot/strategy/configs/config_*.json
ls -la src/titanbot/strategy/configs/

# Optimierungsergebnisse löschen
rm -rf artifacts/results/*
ls -la artifacts/results/
```

#### Daten löschen

```bash
# Cache löschen
rm -rf data/raw/* data/processed/*
du -sh data/*
```

#### Kompletter Neustart

```bash
# Backup erstellen
tar -czf titanbot_backup_$(date +%Y%m%d_%H%M%S).tar.gz \
    secret.json settings.json artifacts/ logs/

# Reset
rm -rf artifacts/* data/* logs/*
./install.sh

# Konfiguration wiederherstellen
cp settings.json.backup settings.json
```

### Tests ausführen

```bash
./run_tests.sh
pytest tests/ -v
pytest --cov=src tests/
```

---

## 🔧 Nützliche Befehle

### Konfiguration

```bash
# Settings validieren
python -c "import json; print(json.load(open('settings.json')))"

# max_open_positions prüfen
python -c "import json; print('Max Positions:', json.load(open('settings.json'))['live_trading_settings']['max_open_positions'])"

# Backup erstellen
cp settings.json settings.json.backup.$(date +%Y%m%d)
```

### Prozess-Management

```bash
# TitanBot-Prozesse anzeigen
ps aux | grep python | grep titanbot

# PID finden
pgrep -f master_runner.py

# Sauber beenden
pkill -f master_runner.py

# Sofort beenden
pkill -9 -f master_runner.py
```

### Exchange-Diagnose

```bash
# Verbindung testen
python -c "from src.titanbot.utils.exchange import Exchange; \
    e = Exchange('binance'); print(e.fetch_balance())"

# Offene Positionen prüfen
python -c "from src.titanbot.utils.exchange import Exchange; \
    e = Exchange('binance'); \
    positions = [p for p in e.fetch_positions() if float(p['contracts']) != 0]; \
    print('Open Positions:', len(positions)); \
    for p in positions: print(p['symbol'], p['contracts'])"

# Anzahl offener Positionen
python check_account_type.py
```

### Performance-Tracking

```bash
# Trade-History analysieren
python -c "
import pandas as pd
trades = pd.read_csv('logs/trades_history.csv')
print('Total Trades:', len(trades))
print('Win Rate:', (trades['pnl'] > 0).mean() * 100, '%')
print('Avg Profit per Trade:', trades['pnl'].mean())
print('Total PnL:', trades['pnl'].sum())
print('Best Trade:', trades['pnl'].max())
print('Worst Trade:', trades['pnl'].min())
"

# Positions-Limit-Statistik
grep "max_open_positions" logs/*.log | wc -l
```

---

## 📂 Projekt-Struktur

```
titanbot/
├── src/titanbot/
│   ├── analysis/          # Optimierung
│   ├── strategy/          # Trading-Strategien
│   ├── backtest/          # Backtesting
│   └── utils/             # Utilities
├── tests/                 # Unit-Tests
├── data/                  # Marktdaten
├── logs/                  # Log-Files
├── artifacts/             # Ergebnisse
├── master_runner.py       # Main Entry-Point
├── settings.json          # Konfiguration
├── secret.json            # API-Credentials
└── requirements.txt       # Dependencies
```

---

## ⚠️ Wichtige Hinweise

### Risiko-Disclaimer

⚠️ **Kryptowährungs-Trading ist hochriskant!**

- Nur Kapital riskieren, dessen Verlust Sie verkraften können
- Keine Gewinn-Garantien
- Vergangene Performance ≠ Zukünftige Ergebnisse
- Ausgiebiges Testing empfohlen
- Mit kleinen Beträgen starten

### Security Best Practices

- 🔐 Niemals API-Keys mit Withdrawal-Rechten
- 🔐 IP-Whitelist aktivieren
- 🔐 2FA für Exchange-Account
- 🔐 `secret.json` in `.gitignore`
- 🔐 Regelmäßige Security-Updates

### Performance-Tipps

- 💡 `max_open_positions` konservativ wählen (3-5)
- 💡 Längere Timeframes (4h+) für stabilere Signale
- 💡 Dynamischen Stop-Loss in volatilen Märkten nutzen
- 💡 Regelmäßiges Monitoring ist essentiell
- 💡 Re-Optimierung alle 3-4 Wochen

---

## 🤝 Support

### Bei Problemen

1. Logs prüfen: `logs/`
2. Tests ausführen: `./run_tests.sh`
3. GitHub Issue erstellen mit:
   - Problembeschreibung
   - Log-Auszüge
   - System-Info
   - Reproduktions-Schritte

---

## 📜 Lizenz

MIT License - siehe [LICENSE](LICENSE)

---

## 🙏 Credits

- [CCXT](https://github.com/ccxt/ccxt) - Exchange Integration
- [Optuna](https://optuna.org/) - Hyperparameter Optimization
- [Pandas](https://pandas.pydata.org/) - Data Analysis
- [python-telegram-bot](https://github.com/python-telegram-bot/python-telegram-bot) - Telegram Integration

---

<div align="center">

**Built with ❤️ for High-Performance Trading**

⭐ Star this repo!

[🔝 Nach oben](#-titanbot---high-performance-trading-system)

</div>
