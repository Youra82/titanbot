#!/usr/bin/env python3
"""
Interactive Status für SMC+EMA Bots (PBot, STBot, UtBot2, TitanBot)
Zeigt Candlestick-Chart mit EMAs, Bollinger Bands und simulierten Trades
Nutzt durchnummerierte Konfigurationsdateien zum Auswählen
"""

import os
import sys
import json
from datetime import datetime, timedelta
import logging

import pandas as pd
import numpy as np
import plotly.graph_objects as go
import ta

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
sys.path.append(os.path.join(PROJECT_ROOT, 'src'))

def setup_logging():
    logger = logging.getLogger('interactive_status')
    if not logger.handlers:
        logger.setLevel(logging.INFO)
        ch = logging.StreamHandler()
        ch.setFormatter(logging.Formatter('%(levelname)s: %(message)s'))
        logger.addHandler(ch)
    return logger

logger = setup_logging()

def detect_bot_name():
    """Erkennt welcher Bot am laufen ist basierend auf PWD"""
    cwd = os.getcwd()
    for bot_name in ['pbot', 'stbot', 'utbot2', 'titanbot']:
        if bot_name in cwd:
            return bot_name
    return 'pbot'  # Default

BOT_NAME = detect_bot_name()

def get_config_files():
    """Sucht alle Konfigurationsdateien auf"""
    configs_dir = os.path.join(PROJECT_ROOT, 'src', BOT_NAME, 'strategy', 'configs')
    if not os.path.exists(configs_dir):
        return []
    
    configs = []
    for filename in sorted(os.listdir(configs_dir)):
        if filename.startswith('config_') and filename.endswith('.json'):
            filepath = os.path.join(configs_dir, filename)
            configs.append((filename, filepath))
    
    return configs

def select_configs():
    """Zeigt durchnummerierte Konfigurationsdateien und lässt User wählen"""
    configs = get_config_files()
    
    if not configs:
        logger.error("Keine Konfigurationsdateien gefunden!")
        sys.exit(1)
    
    print("\n" + "="*60)
    print("Verfügbare Konfigurationen:")
    print("="*60)
    for idx, (filename, _) in enumerate(configs, 1):
        # Extrahiere Symbol/Timeframe aus Dateiname
        clean_name = filename.replace('config_', '').replace('.json', '')
        print(f"{idx:2d}) {clean_name}")
    print("="*60)
    
    print("\nWähle Konfiguration(en) zum Anzeigen:")
    print("  Einzeln: z.B. '1' oder '5'")
    print("  Mehrfach: z.B. '1,3,5' oder '1 3 5'")
    
    selection = input("\nAuswahl: ").strip()
    
    # Parse Eingabe
    selected_indices = []
    for part in selection.replace(',', ' ').split():
        try:
            idx = int(part)
            if 1 <= idx <= len(configs):
                selected_indices.append(idx - 1)
            else:
                logger.warning(f"Index {idx} außerhalb des Bereichs")
        except ValueError:
            logger.warning(f"Ungültige Eingabe: {part}")
    
    if not selected_indices:
        logger.error("Keine gültigen Konfigurationen gewählt!")
        sys.exit(1)
    
    return [configs[i] for i in selected_indices]

def load_config(filepath):
    """Lädt eine Konfiguration"""
    with open(filepath, 'r') as f:
        return json.load(f)

def add_smc_ema_indicators(df):
    """Fügt SMC+EMA Indikatoren hinzu"""
    # EMAs für Trend-Erkennung
    df['ema_20'] = ta.trend.ema_indicator(df['close'], window=20)
    df['ema_50'] = ta.trend.ema_indicator(df['close'], window=50)
    df['ema_200'] = ta.trend.ema_indicator(df['close'], window=200)
    
    # Bollinger Bands für Squeeze Detection
    bb = ta.volatility.BollingerBands(df['close'], window=20, window_dev=2)
    df['bb_upper'] = bb.bollinger_hband()
    df['bb_middle'] = bb.bollinger_mavg()
    df['bb_lower'] = bb.bollinger_lband()
    df['bb_width'] = bb.bollinger_wband()
    
    # ATR für Stop Loss
    df['atr'] = ta.volatility.average_true_range(df['high'], df['low'], df['close'], window=14)
    
    return df

def create_interactive_chart(symbol, timeframe, df, trades, start_date, end_date, window=None):
    """Erstellt interaktiven Chart mit SMC+EMA Indikatoren und Trades"""
    
    # Filter auf Fenster
    if window:
        cutoff_date = datetime.now() - timedelta(days=window)
        df = df[df.index >= cutoff_date].copy()
    
    # Filter auf Start/End Datum
    if start_date:
        df = df[df.index >= pd.to_datetime(start_date)]
    if end_date:
        df = df[df.index <= pd.to_datetime(end_date)]
    
    fig = go.Figure()
    
    # Candlestick
    fig.add_trace(
        go.Candlestick(
            x=df.index,
            open=df['open'],
            high=df['high'],
            low=df['low'],
            close=df['close'],
            name='OHLC',
            showlegend=True
        )
    )
    
    # EMAs
    fig.add_trace(
        go.Scatter(x=df.index, y=df['ema_20'], name='EMA 20', line=dict(color='orange', width=1.5))
    )
    fig.add_trace(
        go.Scatter(x=df.index, y=df['ema_50'], name='EMA 50', line=dict(color='blue', width=1.5))
    )
    fig.add_trace(
        go.Scatter(x=df.index, y=df['ema_200'], name='EMA 200', line=dict(color='red', width=2))
    )
    
    # Bollinger Bands
    fig.add_trace(
        go.Scatter(x=df.index, y=df['bb_upper'], 
                   name='BB Upper', line=dict(color='green', width=1, dash='dash'))
    )
    fig.add_trace(
        go.Scatter(x=df.index, y=df['bb_lower'], 
                   name='BB Lower', line=dict(color='green', width=1, dash='dash'),
                   fill='tonexty', fillcolor='rgba(0,255,0,0.1)')
    )
    
    # Trade Marker
    for trade in trades:
        entry_time = trade['entry_time']
        entry_price = trade['entry_price']
        exit_time = trade['exit_time']
        exit_price = trade['exit_price']
        profit = trade['profit']
        
        color = 'green' if profit > 0 else 'red'
        
        # Entry
        fig.add_trace(
            go.Scatter(
                x=[entry_time],
                y=[entry_price],
                mode='markers',
                marker=dict(size=10, color='green', symbol='triangle-up'),
                name=f'Entry ({entry_price:.2f})',
                showlegend=False
            )
        )
        
        # Exit
        fig.add_trace(
            go.Scatter(
                x=[exit_time],
                y=[exit_price],
                mode='markers',
                marker=dict(size=10, color=color, symbol='triangle-down'),
                name=f'Exit ({exit_price:.2f})',
                showlegend=False
            )
        )
        
        # Verbindungslinie
        fig.add_trace(
            go.Scatter(
                x=[entry_time, exit_time],
                y=[entry_price, exit_price],
                mode='lines',
                line=dict(color=color, width=1, dash='dash'),
                showlegend=False
            )
        )
    
    bot_display = BOT_NAME.upper()
    title = f"{symbol} {timeframe} - {bot_display} (SMC+EMA Strategy)"
    fig.update_layout(
        title=title,
        height=800,
        hovermode='x unified',
        template='plotly_dark'
    )
    
    fig.update_yaxes(title_text="Price")
    fig.update_xaxes(title_text="Time")
    
    return fig

def main():
    # Wähle Konfigurationsdateien
    selected_configs = select_configs()
    
    # Parameter für Chart-Generierung
    print("\n" + "="*60)
    print("Chart-Optionen:")
    print("="*60)
    
    start_date = input("Startdatum (YYYY-MM-DD) [leer=beliebig]: ").strip() or None
    end_date = input("Enddatum (YYYY-MM-DD) [leer=heute]: ").strip() or None
    window_input = input("Letzten N Tage anzeigen [leer=alle]: ").strip()
    window = int(window_input) if window_input.isdigit() else None
    send_telegram = input("Telegram versenden? (j/n) [Standard: n]: ").strip().lower() in ['j', 'y', 'yes']
    
    try:
        with open(os.path.join(PROJECT_ROOT, 'secret.json'), 'r') as f:
            secrets = json.load(f)
    except Exception as e:
        logger.error(f"Fehler beim Laden von secret.json: {e}")
        sys.exit(1)
    
    account = secrets.get(BOT_NAME, [None])[0]
    if not account:
        logger.error(f"Keine {BOT_NAME.upper()}-Accountkonfiguration gefunden")
        sys.exit(1)
    
    # Import der passenden Exchange-Klasse
    module = __import__(f'{BOT_NAME}.utils.exchange', fromlist=['Exchange'])
    Exchange = module.Exchange
    
    exchange = Exchange(account)
    telegram_config = secrets.get('telegram', {})
    
    # Generiere Chart für jede gewählte Config
    for filename, filepath in selected_configs:
        try:
            logger.info(f"\nVerarbeite {filename}...")
            
            config = load_config(filepath)
            symbol = config['market']['symbol']
            timeframe = config['market']['timeframe']
            
            logger.info(f"Lade OHLCV-Daten für {symbol} {timeframe}...")
            df = exchange.fetch_recent_ohlcv(symbol, timeframe, limit=500)
            
            if df is None or len(df) == 0:
                logger.warning(f"Keine Daten für {symbol} {timeframe}")
                continue
            
            logger.info("Berechne Indikatoren...")
            df = add_smc_ema_indicators(df)
            
            # Erstelle Chart
            logger.info("Erstelle Chart...")
            fig = create_interactive_chart(
                symbol,
                timeframe,
                df,
                [],  # Keine Trades für diese vereinachte Version
                start_date,
                end_date,
                window
            )
            
            # Speichere HTML
            safe_name = f"{symbol.replace('/', '_')}_{timeframe}"
            output_file = f"/tmp/{BOT_NAME}_{safe_name}.html"
            fig.write_html(output_file)
            logger.info(f"✅ Chart gespeichert: {output_file}")
            
            # Telegram versenden (optional)
            if send_telegram and telegram_config:
                try:
                    logger.info(f"Sende Chart via Telegram...")
                    telegram_module = __import__(f'{BOT_NAME}.utils.telegram', fromlist=['send_file'])
                    send_file = telegram_module.send_file
                    send_file(output_file, telegram_config)
                except Exception as e:
                    logger.warning(f"Konnte Chart nicht via Telegram versenden: {e}")
        
        except Exception as e:
            logger.error(f"Fehler bei {filename}: {e}", exc_info=False)
            continue
    
    logger.info("\n✅ Alle Charts generiert!")

if __name__ == '__main__':
    main()
