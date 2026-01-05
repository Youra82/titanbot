#!/usr/bin/env python3
"""
Interactive Charts für TitanBot - SMC (Smart Money Concepts) Strategie
Zeigt Candlestick-Chart mit Trade-Signalen (Entry/Exit Long/Short) + Equity Curve
Nutzt durchnummerierte Konfigurationsdateien zum Auswählen
Basiert auf utbot2 Layout (wie ltbbot): Single Chart mit secondary_y für Kontostand
"""

import os
import sys
import json
from datetime import datetime, timedelta, timezone
import logging

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
sys.path.append(os.path.join(PROJECT_ROOT, 'src'))

from titanbot.utils.exchange import Exchange
from titanbot.strategy.smc_engine import SMCEngine, Bias
from titanbot.analysis.backtester import run_smc_backtest

def setup_logging():
    logger = logging.getLogger('interactive_status')
    if not logger.handlers:
        logger.setLevel(logging.INFO)
        ch = logging.StreamHandler()
        ch.setFormatter(logging.Formatter('%(levelname)s: %(message)s'))
        logger.addHandler(ch)
    return logger

logger = setup_logging()

def get_config_files():
    """Sucht alle Konfigurationsdateien auf"""
    configs_dir = os.path.join(PROJECT_ROOT, 'src', 'titanbot', 'strategy', 'configs')
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
        clean_name = filename.replace('config_', '').replace('.json', '')
        print(f"{idx:2d}) {clean_name}")
    print("="*60)
    
    print("\nWähle Konfiguration(en) zum Anzeigen:")
    print("  Einzeln: z.B. '1' oder '5'")
    print("  Mehrfach: z.B. '1,3,5' oder '1 3 5'")
    
    selection = input("\nAuswahl: ").strip()
    
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

def run_backtest_for_chart(df, config, start_capital=1000):
    """
    Führt einen Backtest durch und gibt Trades, Equity Curve und Stats zurück
    Nutzt den existierenden SMC Backtester für Stats
    """
    try:
        strategy_params = config.get('strategy', {})
        risk_params = config.get('risk', {})
        
        # Symbol und Timeframe für SMC hinzufügen
        market = config.get('market', {})
        strategy_params['symbol'] = market.get('symbol', '')
        strategy_params['timeframe'] = market.get('timeframe', '')
        
        # Existierenden Backtester für Stats nutzen
        logger_backtest = logging.getLogger('titanbot.analysis.backtester')
        original_level = logger_backtest.level
        logger_backtest.setLevel(logging.ERROR)
        
        stats = run_smc_backtest(df.copy(), strategy_params, risk_params, start_capital=start_capital, verbose=False)
        
        logger_backtest.setLevel(original_level)
        
        # Trade-Signale für Visualisierung extrahieren (basiert auf SMC Events)
        trades = extract_trades_from_backtest(df, config, start_capital)
        
        # Equity Curve simulieren basierend auf Trades
        equity_df = build_equity_curve(df, trades, start_capital)
        
        return trades, equity_df, stats
    except Exception as e:
        logger.warning(f"Fehler bei Backtest-Simulation: {e}")
        import traceback
        traceback.print_exc()
        return [], df[[]].copy(), {}


def build_equity_curve(df, trades, start_capital):
    """
    Erstellt eine Equity Curve basierend auf den simulierten Trades
    """
    equity = start_capital
    equity_data = []
    
    # Sammle alle Trade-Events mit Zeitstempel
    trade_events = []
    for trade in trades:
        if 'exit_long' in trade:
            entry_price = trade.get('entry_long', {}).get('price', 0)
            exit_price = trade.get('exit_long', {}).get('price', 0)
            exit_time = trade.get('exit_long', {}).get('time')
            if entry_price and exit_price and exit_time:
                pnl_pct = (exit_price - entry_price) / entry_price
                trade_events.append({
                    'time': pd.to_datetime(exit_time),
                    'pnl_pct': pnl_pct,
                    'side': 'long'
                })
        
        if 'exit_short' in trade:
            entry_price = trade.get('entry_short', {}).get('price', 0)
            exit_price = trade.get('exit_short', {}).get('price', 0)
            exit_time = trade.get('exit_short', {}).get('time')
            if entry_price and exit_price and exit_time:
                pnl_pct = (entry_price - exit_price) / entry_price
                trade_events.append({
                    'time': pd.to_datetime(exit_time),
                    'pnl_pct': pnl_pct,
                    'side': 'short'
                })
    
    # Sortiere Trade-Events nach Zeit
    trade_events = sorted(trade_events, key=lambda x: x['time'])
    
    # Erstelle Equity Curve für jeden Timestamp im DataFrame
    trade_idx = 0
    for timestamp, row in df.iterrows():
        # Wende alle Trades bis zu diesem Timestamp an
        while trade_idx < len(trade_events) and trade_events[trade_idx]['time'] <= timestamp:
            trade = trade_events[trade_idx]
            equity += equity * trade['pnl_pct']
            trade_idx += 1
        
        equity_data.append({
            'timestamp': timestamp,
            'equity': equity
        })
    
    equity_df = pd.DataFrame(equity_data)
    equity_df.set_index('timestamp', inplace=True)
    return equity_df

def extract_trades_from_backtest(df, config, start_capital=1000):
    """
    Extrahiert Trade-Signale aus dem SMC-Chart für die Visualisierung
    Nutzt die SMC Engine Event-Log für BOS/CHoCH Signale
    Liefert Entry/Exit Punkte für Long und Short Positionen
    """
    trades = []
    try:
        strategy_params = config.get('strategy', {})
        
        # SMC Engine initialisieren und verarbeiten
        engine = SMCEngine(settings=strategy_params)
        smc_results = engine.process_dataframe(df[['open', 'high', 'low', 'close']].copy())
        
        # Hole die Events aus der SMC Engine
        events = smc_results.get('events', [])
        
        in_position = False
        position_type = None
        entry_price = None
        entry_time = None
        entry_index = None
        
        # Verarbeite Events chronologisch
        for event in events:
            event_type = event.get('type', '')
            event_index = event.get('index', 0)
            event_time = event.get('time')
            
            # Nur Swing-Events für Trades verwenden (stabiler als Internal)
            if 'Swing' not in event_type:
                continue
            
            # Bestimme Signal-Richtung
            is_bullish = 'Bullish' in event_type
            is_bearish = 'Bearish' in event_type
            
            # Hole Close-Preis zum Event-Zeitpunkt
            if event_index >= len(df):
                continue
            close_price = df.iloc[event_index]['close']
            timestamp = df.index[event_index]
            
            # State machine für Positionen
            if not in_position:
                if is_bullish:
                    in_position = True
                    position_type = 'long'
                    entry_price = close_price
                    entry_time = timestamp
                    entry_index = event_index
                elif is_bearish:
                    in_position = True
                    position_type = 'short'
                    entry_price = close_price
                    entry_time = timestamp
                    entry_index = event_index
            else:
                # Exit bei entgegengesetztem Signal
                should_exit = False
                if position_type == 'long' and is_bearish:
                    should_exit = True
                elif position_type == 'short' and is_bullish:
                    should_exit = True
                
                if should_exit:
                    trade = {
                        'entry_' + position_type: {
                            'time': entry_time.isoformat() if hasattr(entry_time, 'isoformat') else str(entry_time),
                            'price': float(entry_price)
                        },
                        'exit_' + position_type: {
                            'time': timestamp.isoformat() if hasattr(timestamp, 'isoformat') else str(timestamp),
                            'price': float(close_price)
                        }
                    }
                    trades.append(trade)
                    
                    # Sofort neue Position in entgegengesetzter Richtung eröffnen
                    in_position = True
                    position_type = 'long' if is_bullish else 'short'
                    entry_price = close_price
                    entry_time = timestamp
                    entry_index = event_index
        
        logger.info(f"SMC Events: {len(events)}, Trades extrahiert: {len(trades)}")
        return trades
    except Exception as e:
        logger.warning(f"Fehler beim Extrahieren von Trades: {e}")
        import traceback
        traceback.print_exc()
        return []


def create_interactive_chart(symbol, timeframe, df, trades, equity_df, stats, start_date, end_date, window=None, start_capital=1000):
    """
    Erstellt interaktiven Chart GENAU wie ltbbot/utbot2:
    - Ein einzelner Chart (kein make_subplots mit 2 Reihen)
    - Rangeslider für einfaches Zoomen
    - Kontostand auf zweiter Y-Achse (rechts) überlagert
    - Statistiken im Titel (wie im Screenshot)
    - SMC Indikatoren (Order Blocks, FVGs)
    """
    
    # Filter auf Fenster
    if window:
        cutoff_date = datetime.now(timezone.utc) - timedelta(days=window)
        df = df[df.index >= cutoff_date].copy()
    
    # Filter auf Start/End Datum
    if start_date:
        df = df[df.index >= pd.to_datetime(start_date, utc=True)]
    if end_date:
        df = df[df.index <= pd.to_datetime(end_date, utc=True)]
    
    # Ein einzelner Chart mit secondary_y für Equity (wie ltbbot)
    fig = make_subplots(specs=[[{"secondary_y": True}]])
    
    # Statistiken für Titel berechnen
    end_capital = equity_df['equity'].iloc[-1] if not equity_df.empty and 'equity' in equity_df.columns else start_capital
    pnl_pct = stats.get('total_pnl_pct', 0)
    pnl_sign = '+' if pnl_pct >= 0 else ''
    trades_count = stats.get('trades_count', len(trades))
    win_rate = stats.get('win_rate', 0)
    max_dd = stats.get('max_drawdown_pct', 0) * 100  # Convert to percentage
    
    # ===== CANDLESTICK CHART =====
    fig.add_trace(
        go.Candlestick(
            x=df.index,
            open=df['open'],
            high=df['high'],
            low=df['low'],
            close=df['close'],
            name='OHLC',
            increasing_line_color="#16a34a",
            decreasing_line_color="#dc2626",
            showlegend=True
        ),
        secondary_y=False
    )
    
    # ===== TRADE-SIGNALE =====
    entry_long_x, entry_long_y = [], []
    exit_long_x, exit_long_y = [], []
    entry_short_x, entry_short_y = [], []
    exit_short_x, exit_short_y = [], []
    
    for trade in trades:
        if 'entry_long' in trade and trade['entry_long'].get('time') and trade['entry_long'].get('price'):
            entry_long_x.append(pd.to_datetime(trade['entry_long']['time']))
            entry_long_y.append(trade['entry_long']['price'])
        if 'exit_long' in trade and trade['exit_long'].get('time') and trade['exit_long'].get('price'):
            exit_long_x.append(pd.to_datetime(trade['exit_long']['time']))
            exit_long_y.append(trade['exit_long']['price'])
        if 'entry_short' in trade and trade['entry_short'].get('time') and trade['entry_short'].get('price'):
            entry_short_x.append(pd.to_datetime(trade['entry_short']['time']))
            entry_short_y.append(trade['entry_short']['price'])
        if 'exit_short' in trade and trade['exit_short'].get('time') and trade['exit_short'].get('price'):
            exit_short_x.append(pd.to_datetime(trade['exit_short']['time']))
            exit_short_y.append(trade['exit_short']['price'])
    
    # Entry Long: grünes Dreieck nach oben
    if entry_long_x:
        fig.add_trace(go.Scatter(
            x=entry_long_x, y=entry_long_y, mode="markers",
            marker=dict(color="#16a34a", symbol="triangle-up", size=14, line=dict(width=1.2, color="#0f5132")),
            name="Entry Long", showlegend=True
        ), secondary_y=False)
    
    # Exit Long: cyan Kreis
    if exit_long_x:
        fig.add_trace(go.Scatter(
            x=exit_long_x, y=exit_long_y, mode="markers",
            marker=dict(color="#22d3ee", symbol="circle", size=12, line=dict(width=1.1, color="#0e7490")),
            name="Exit Long", showlegend=True
        ), secondary_y=False)
    
    # Entry Short: oranges Dreieck nach unten
    if entry_short_x:
        fig.add_trace(go.Scatter(
            x=entry_short_x, y=entry_short_y, mode="markers",
            marker=dict(color="#f59e0b", symbol="triangle-down", size=14, line=dict(width=1.2, color="#92400e")),
            name="Entry Short", showlegend=True
        ), secondary_y=False)
    
    # Exit Short: rotes Diamant
    if exit_short_x:
        fig.add_trace(go.Scatter(
            x=exit_short_x, y=exit_short_y, mode="markers",
            marker=dict(color="#ef4444", symbol="diamond", size=12, line=dict(width=1.1, color="#7f1d1d")),
            name="Exit Short", showlegend=True
        ), secondary_y=False)
    
    # ===== EQUITY CURVE AUF ZWEITER Y-ACHSE (rechts überlagert) =====
    if not equity_df.empty and 'equity' in equity_df.columns:
        fig.add_trace(
            go.Scatter(
                x=equity_df.index, 
                y=equity_df['equity'], 
                name='Kontostand',
                line=dict(color='#2563eb', width=2, dash='solid'),
                opacity=0.7,
                showlegend=True
            ),
            secondary_y=True
        )
    
    # ===== LAYOUT (genau wie ltbbot Screenshot) =====
    # Stats im Titel anzeigen wie im ltbbot Screenshot
    title_text = (
        f"{symbol} {timeframe} - TitanBot | "
        f"Start Capital: ${start_capital:.2f} | "
        f"End Capital: ${end_capital:.2f} | "
        f"PnL: {pnl_sign}{pnl_pct:.2f}% | "
        f"Max DD: {max_dd:.2f}% | "
        f"Trades: {trades_count} | "
        f"Win Rate: {win_rate:.1f}%"
    )
    
    fig.update_layout(
        title=dict(
            text=title_text,
            font=dict(size=14),
            x=0.5,
            xanchor='center'
        ),
        height=700,
        hovermode='x unified',
        template='plotly_white',
        dragmode='zoom',
        xaxis=dict(rangeslider=dict(visible=True), fixedrange=False),
        yaxis=dict(fixedrange=False),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="center", x=0.5),
        showlegend=True
    )
    
    fig.update_yaxes(title_text="Preis (USDT)", secondary_y=False)
    fig.update_yaxes(title_text="Kontostand (USDT)", secondary_y=True)
    fig.update_xaxes(fixedrange=False)
    
    return fig

def main():
    selected_configs = select_configs()
    
    print("\n" + "="*60)
    print("Chart-Optionen:")
    print("="*60)
    
    start_date = input("Startdatum (YYYY-MM-DD) [leer=beliebig]: ").strip() or None
    end_date = input("Enddatum (YYYY-MM-DD) [leer=heute]: ").strip() or None
    start_capital_input = input("Startkapital (USDT) [Standard: 1000]: ").strip()
    start_capital = int(start_capital_input) if start_capital_input.isdigit() else 1000
    window_input = input("Letzten N Tage anzeigen [leer=alle]: ").strip()
    window = int(window_input) if window_input.isdigit() else None
    send_telegram = input("Telegram versenden? (j/n) [Standard: n]: ").strip().lower() in ['j', 'y', 'yes']
    
    try:
        with open(os.path.join(PROJECT_ROOT, 'secret.json'), 'r') as f:
            secrets = json.load(f)
    except Exception as e:
        logger.error(f"Fehler beim Laden von secret.json: {e}")
        sys.exit(1)
    
    account = secrets.get('titanbot', [None])[0]
    if not account:
        logger.error("Keine TitanBot-Accountkonfiguration gefunden")
        sys.exit(1)
    
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
            
            # Bestimme Ladetarife
            if not start_date:
                start_date_for_load = (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%d")
            else:
                start_date_for_load = start_date
            
            if not end_date:
                end_date_for_load = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            else:
                end_date_for_load = end_date
            
            df = exchange.fetch_historical_ohlcv(symbol, timeframe, start_date_for_load, end_date_for_load)
            
            if df is None or len(df) == 0:
                logger.warning(f"Keine Daten für {symbol} {timeframe}")
                continue
            
            # Backtest-Simulation durchführen
            logger.info("Führe SMC-Backtest-Simulation durch...")
            trades, equity_df, stats = run_backtest_for_chart(df, config, start_capital)
            
            # Chart erstellen
            logger.info("Erstelle Chart mit Trade-Signalen und Equity Curve...")
            fig = create_interactive_chart(
                symbol,
                timeframe,
                df,
                trades,
                equity_df,
                stats,
                start_date,
                end_date,
                window,
                start_capital
            )
            
            safe_name = f"{symbol.replace('/', '_')}_{timeframe}"
            output_file = f"/tmp/titanbot_{safe_name}.html"
            fig.write_html(output_file)
            logger.info(f"✅ Chart gespeichert: {output_file}")
            
            # Telegram versenden (optional)
            if send_telegram and telegram_config:
                try:
                    logger.info("Sende Chart via Telegram...")
                    from titanbot.utils.telegram import send_document
                    bot_token = telegram_config.get('bot_token')
                    chat_id = telegram_config.get('chat_id')
                    if bot_token and chat_id:
                        send_document(bot_token, chat_id, output_file, caption=f"Chart: {symbol} {timeframe}")
                        logger.info("✅ Chart via Telegram versendet")
                except Exception as e:
                    logger.warning(f"Konnte Chart nicht via Telegram versenden: {e}")
        
        except Exception as e:
            logger.error(f"Fehler bei {filename}: {e}", exc_info=True)
            continue
    
    logger.info("\n✅ Alle Charts generiert!")

if __name__ == '__main__':
    main()
