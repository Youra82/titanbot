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
    Führt einen Backtest durch und gibt Trades, Equity Curve, Stats und SMC-Strukturen zurück
    Nutzt den existierenden SMC Backtester - jetzt mit Trades, Equity-Curve und SMC-Strukturen!
    """
    try:
        strategy_params = config.get('strategy', {})
        risk_params = config.get('risk', {})
        
        # Symbol und Timeframe für SMC hinzufügen
        market = config.get('market', {})
        strategy_params['symbol'] = market.get('symbol', '')
        strategy_params['timeframe'] = market.get('timeframe', '')
        
        # Existierenden Backtester nutzen - gibt jetzt auch trades_list, equity_curve und smc_structures zurück
        logger_backtest = logging.getLogger('titanbot.analysis.backtester')
        original_level = logger_backtest.level
        logger_backtest.setLevel(logging.ERROR)
        
        result = run_smc_backtest(df.copy(), strategy_params, risk_params, start_capital=start_capital, verbose=False)
        
        logger_backtest.setLevel(original_level)
        
        # Trades direkt vom Backtester (konsistent mit Stats!)
        trades = result.get('trades_list', [])
        
        # Equity Curve direkt vom Backtester
        equity_data = result.get('equity_curve', [])
        if equity_data:
            equity_df = pd.DataFrame(equity_data)
            equity_df.set_index('timestamp', inplace=True)
        else:
            equity_df = pd.DataFrame()
        
        # SMC-Strukturen für Chart-Visualisierung
        smc_structures = result.get('smc_structures', {})
        
        # Stats extrahieren
        stats = {
            'total_pnl_pct': result.get('total_pnl_pct', 0),
            'trades_count': result.get('trades_count', 0),
            'win_rate': result.get('win_rate', 0),
            'max_drawdown_pct': result.get('max_drawdown_pct', 0),
            'end_capital': result.get('end_capital', start_capital)
        }
        
        logger.info(f"Backtester: {len(trades)} Trades, End Capital: ${stats['end_capital']:.2f}")
        
        return trades, equity_df, stats, smc_structures
    except Exception as e:
        logger.warning(f"Fehler bei Backtest-Simulation: {e}")
        import traceback
        traceback.print_exc()
        return [], pd.DataFrame(), {}, {}


def create_interactive_chart(symbol, timeframe, df, trades, equity_df, stats, start_date, end_date, window=None, start_capital=1000, smc_structures=None):
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
    
    # ===== SMC-STRUKTUREN (Order Blocks, FVGs) =====
    if smc_structures:
        # Hilfsfunktion: Konvertiere int64 Zeit zu datetime
        def int_to_datetime(time_int):
            try:
                return pd.to_datetime(time_int, unit='ns')
            except:
                return pd.to_datetime(time_int)
        
        # Berechne Kerzenbreite für die Rechtecke
        if len(df) > 1:
            # Timeframe-basierte Breite berechnen
            time_diff = (df.index[1] - df.index[0]).total_seconds() * 1000  # in ms
            bar_width = time_diff * 0.8  # 80% der Kerzenbreite
        else:
            bar_width = 3600000  # 1 Stunde default
        
        shapes = []
        annotations = []
        
        # --- Order Blocks zeichnen ---
        order_blocks = smc_structures.get('order_blocks', [])
        for ob in order_blocks:
            try:
                ob_time = int_to_datetime(ob.barTime)
                
                # Farbe basierend auf Bias und Mitigations-Status
                if ob.bias.name == 'BULLISH':
                    fill_color = 'rgba(34, 197, 94, 0.3)' if not ob.mitigated else 'rgba(34, 197, 94, 0.1)'  # Grün
                    line_color = 'rgba(34, 197, 94, 0.8)' if not ob.mitigated else 'rgba(34, 197, 94, 0.3)'
                else:
                    fill_color = 'rgba(239, 68, 68, 0.3)' if not ob.mitigated else 'rgba(239, 68, 68, 0.1)'  # Rot
                    line_color = 'rgba(239, 68, 68, 0.8)' if not ob.mitigated else 'rgba(239, 68, 68, 0.3)'
                
                # Rechteck für Order Block (erweitern nach rechts für bessere Sichtbarkeit)
                # Erweitere um 10 Kerzen oder bis zum Chart-Ende
                extend_bars = 10
                end_time = ob_time + pd.Timedelta(milliseconds=bar_width * extend_bars)
                
                shapes.append(dict(
                    type="rect",
                    x0=ob_time, x1=end_time,
                    y0=ob.barLow, y1=ob.barHigh,
                    fillcolor=fill_color,
                    line=dict(color=line_color, width=1),
                    layer="below"
                ))
            except Exception as e:
                continue
        
        # --- Fair Value Gaps zeichnen ---
        fair_value_gaps = smc_structures.get('fair_value_gaps', [])
        for fvg in fair_value_gaps:
            try:
                fvg_time = int_to_datetime(fvg.startTime)
                
                # Farbe basierend auf Bias und Mitigations-Status
                if fvg.bias.name == 'BULLISH':
                    fill_color = 'rgba(59, 130, 246, 0.25)' if not fvg.mitigated else 'rgba(59, 130, 246, 0.08)'  # Blau
                    line_color = 'rgba(59, 130, 246, 0.7)' if not fvg.mitigated else 'rgba(59, 130, 246, 0.2)'
                else:
                    fill_color = 'rgba(249, 115, 22, 0.25)' if not fvg.mitigated else 'rgba(249, 115, 22, 0.08)'  # Orange
                    line_color = 'rgba(249, 115, 22, 0.7)' if not fvg.mitigated else 'rgba(249, 115, 22, 0.2)'
                
                # Rechteck für FVG (erweitern nach rechts)
                extend_bars = 8
                end_time = fvg_time + pd.Timedelta(milliseconds=bar_width * extend_bars)
                
                shapes.append(dict(
                    type="rect",
                    x0=fvg_time, x1=end_time,
                    y0=fvg.bottom, y1=fvg.top,
                    fillcolor=fill_color,
                    line=dict(color=line_color, width=1, dash='dot'),
                    layer="below"
                ))
            except Exception as e:
                continue
        
        # --- BOS/CHoCH Events als Linien markieren ---
        events = smc_structures.get('events', [])
        for event in events:
            try:
                event_time = int_to_datetime(event['time'])
                event_type = event.get('type', '')
                level = event.get('level')
                
                # Nur Level-basierte Events (BOS/CHoCH) zeichnen
                if isinstance(level, (int, float)) and not pd.isna(level):
                    # Farbe nach Event-Typ
                    if 'Bullish' in event_type:
                        color = '#22c55e'  # Grün
                    elif 'Bearish' in event_type:
                        color = '#ef4444'  # Rot
                    else:
                        continue
                    
                    # Linien-Stil nach Typ (CHoCH gestrichelt, BOS durchgezogen)
                    dash_style = 'dash' if 'CHoCH' in event_type else 'solid'
                    
                    # Horizontale Linie am Level (erweitert nach rechts)
                    extend_bars = 5
                    end_time = event_time + pd.Timedelta(milliseconds=bar_width * extend_bars)
                    
                    shapes.append(dict(
                        type="line",
                        x0=event_time, x1=end_time,
                        y0=level, y1=level,
                        line=dict(color=color, width=1.5, dash=dash_style),
                        layer="above"
                    ))
            except Exception as e:
                continue
        
        # Shapes zum Layout hinzufügen
        if shapes:
            fig.update_layout(shapes=shapes)
        
        logger.info(f"SMC-Strukturen: {len(order_blocks)} OBs, {len(fair_value_gaps)} FVGs, {len(events)} Events")
    
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
    
    # SMC-Legende für Untertitel
    smc_legend = ""
    if smc_structures:
        ob_count = len(smc_structures.get('order_blocks', []))
        fvg_count = len(smc_structures.get('fair_value_gaps', []))
        smc_legend = f"<br><span style='font-size:11px; color:#666'>SMC: 🟩 Bullish OB | 🟥 Bearish OB | 🟦 Bullish FVG | 🟧 Bearish FVG | ━ BOS | ┄ CHoCH ({ob_count} OBs, {fvg_count} FVGs)</span>"
    
    fig.update_layout(
        title=dict(
            text=title_text + smc_legend,
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
            trades, equity_df, stats, smc_structures = run_backtest_for_chart(df, config, start_capital)
            
            # Chart erstellen
            logger.info("Erstelle Chart mit Trade-Signalen, SMC-Strukturen und Equity Curve...")
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
                start_capital,
                smc_structures
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
