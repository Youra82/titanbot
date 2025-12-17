# /root/titanbot/src/titanbot/utils/exchange.py
# KORRIGIERTE VERSION V3 - NUTZT DIE BITGET-SPEZIFISCHEN PARAMETER INNERHALB EINER MARKET ORDER (WIE IM ERFOLGREICHEN BEISPIEL)
import ccxt
import pandas as pd
from datetime import datetime, timezone, timedelta
import time
import logging

logger = logging.getLogger(__name__)

class Exchange:
    def __init__(self, account_config):
        self.account = account_config
        self.exchange = getattr(ccxt, 'bitget')({
            'apiKey': self.account.get('apiKey'),
            'secret': self.account.get('secret'),
            'password': self.account.get('password'),
            'options': {
                'defaultType': 'swap',
            },
            'enableRateLimit': True,
        })
        try:
            self.markets = self.exchange.load_markets()
            logger.info("Bitget Märkte erfolgreich geladen.")
        except ccxt.AuthenticationError as e:
            logger.critical(f"FATAL: Bitget Authentifizierungsfehler: {e}. Bitte API-Schlüssel prüfen.")
            self.markets = None
        except ccxt.NetworkError as e:
            logger.warning(f"WARNUNG: Netzwerkfehler beim Laden der Märkte: {e}.")
            self.markets = None
        except Exception as e:
            logger.warning(f"WARNUNG: Unerwarteter Fehler beim Laden der Märkte: {e}")
            self.markets = None

    def fetch_recent_ohlcv(self, symbol, timeframe, limit=100, ensure_min_data=None):
        """
        Holt die letzten Kerzen. Wenn ensure_min_data angegeben ist und nicht genug Daten 
        vorhanden sind, lädt automatisch historische Daten nach.
        
        Args:
            symbol: Trading-Paar (z.B. 'BTC/USDT:USDT')
            timeframe: Zeitrahmen (z.B. '1d', '4h', '30m')
            limit: Anzahl der zu holenden Kerzen
            ensure_min_data: Mindestanzahl Kerzen, die vorhanden sein müssen (lädt ggf. historisch nach)
        """
        if not self.markets: return pd.DataFrame()
        try:
            effective_limit = min(limit, 1000)
            data = self.exchange.fetch_ohlcv(symbol, timeframe, limit=effective_limit)
            if not data: return pd.DataFrame()
            df = pd.DataFrame(data, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms', utc=True)
            df.set_index('timestamp', inplace=True)
            df.sort_index(inplace=True)
            
            # Wenn ensure_min_data gesetzt ist und nicht genug Daten vorhanden sind
            if ensure_min_data and len(df) < ensure_min_data:
                logger.warning(f"Nur {len(df)} Kerzen für {symbol} ({timeframe}) verfügbar, lade historische Daten nach...")
                # Berechne, wie viele Tage wir zurückgehen müssen
                timeframe_map = {'5m': 5/1440, '15m': 15/1440, '30m': 30/1440, '1h': 1/24, 
                                 '2h': 2/24, '4h': 4/24, '6h': 6/24, '1d': 1}
                days_per_candle = timeframe_map.get(timeframe, 1)
                days_needed = int(ensure_min_data * days_per_candle) + 10  # +10 Tage Puffer
                
                end_date = datetime.now(timezone.utc)
                start_date = end_date - timedelta(days=days_needed)
                
                historical_df = self.fetch_historical_ohlcv(
                    symbol, timeframe, 
                    start_date.strftime('%Y-%m-%d'),
                    end_date.strftime('%Y-%m-%d')
                )
                
                if not historical_df.empty:
                    logger.info(f"✓ {len(historical_df)} historische Kerzen für {symbol} ({timeframe}) nachgeladen.")
                    return historical_df
                else:
                    logger.warning(f"Konnte keine historischen Daten für {symbol} ({timeframe}) laden.")
            
            return df
        except Exception as e:
            logger.error(f"Fehler bei fetch_recent_ohlcv für {symbol}: {e}")
            return pd.DataFrame()

    def fetch_historical_ohlcv(self, symbol, timeframe, start_date_str, end_date_str, max_retries=3):
        if not self.markets: return pd.DataFrame()
        try:
            start_dt = pd.to_datetime(start_date_str + 'T00:00:00Z', utc=True)
            end_dt = pd.to_datetime(end_date_str + 'T23:59:59Z', utc=True)
            start_ts = int(start_dt.timestamp() * 1000)
            end_ts = int(end_dt.timestamp() * 1000)
        except ValueError as e:
            logger.error(f"FEHLER: Ungültiges Datumsformat: {e}")
            return pd.DataFrame()

        all_ohlcv = []
        current_ts = start_ts
        retries = 0
        limit = 1000
        timeframe_duration_ms = self.exchange.parse_timeframe(timeframe) * 1000 if self.exchange.parse_timeframe(timeframe) else 60000

        while current_ts < end_ts and retries < max_retries:
            try:
                ohlcv = self.exchange.fetch_ohlcv(symbol, timeframe, since=current_ts, limit=limit)
                if not ohlcv:
                    logger.warning(f"Keine OHLCV-Daten für {symbol} {timeframe} ab {pd.to_datetime(current_ts, unit='ms', utc=True)} erhalten.")
                    current_ts += limit * timeframe_duration_ms
                    continue

                ohlcv = [candle for candle in ohlcv if candle[0] <= end_ts]
                if not ohlcv: break

                all_ohlcv.extend(ohlcv)
                last_ts = ohlcv[-1][0]

                if last_ts >= current_ts:
                    current_ts = last_ts + timeframe_duration_ms
                else:
                    logger.warning("WARNUNG: Kein Zeitfortschritt beim Datenabruf, breche ab.")
                    break
                retries = 0
            except (ccxt.RateLimitExceeded, ccxt.NetworkError) as e:
                logger.warning(f"Netzwerk/Ratelimit-Fehler bei fetch_historical_ohlcv: {e}. Versuch {retries+1}/{max_retries}. Warte...")
                time.sleep(5 * (retries + 1))
                retries += 1
            except ccxt.BadSymbol as e:
                logger.error(f"FEHLER: Ungültiges Symbol bei fetch_historical_ohlcv: {symbol}. {e}")
                return pd.DataFrame()
            except Exception as e:
                logger.error(f"Unerwarteter Fehler bei fetch_historical_ohlcv: {e}. Versuch {retries+1}/{max_retries}.")
                time.sleep(5)
                retries += 1

        if not all_ohlcv:
            logger.warning(f"Keine historischen Daten für {symbol} ({timeframe}) im Zeitraum {start_date_str} - {end_date_str} gefunden.")
            return pd.DataFrame()

        df = pd.DataFrame(all_ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms', utc=True)
        df.set_index('timestamp', inplace=True)
        df = df[~df.index.duplicated(keep='first')].sort_index()
        return df.loc[start_dt:end_dt]

    def fetch_ticker(self, symbol):
        if not self.markets: return None
        try:
            return self.exchange.fetch_ticker(symbol)
        except Exception as e:
            logger.error(f"Fehler bei fetch_ticker für {symbol}: {e}")
            return None

    # *** START: 1:1 JAEGERBOT LOGIK ***
    def set_margin_mode(self, symbol, mode='isolated'):
        if not self.markets: return False
        try:
            self.exchange.set_margin_mode(mode, symbol)
            return True
        except Exception as e:
            if 'Margin mode is the same' not in str(e):
                logger.warning(f"Warnung: Margin-Modus konnte nicht gesetzt werden: {e}")
            else:
                return True # War bereits gesetzt, ist OK
            return False # Expliziter Fehler

    def set_leverage(self, symbol, level=10):
        if not self.markets: return False
        try:
            self.exchange.set_leverage(level, symbol)
            return True
        except Exception as e:
            if 'Leverage not changed' not in str(e):
                logger.warning(f"Warnung: Set leverage failed: {e}")
            else:
                return True # War bereits gesetzt, ist OK
            return False # Expliziter Fehler
    # *** ENDE: 1:1 JAEGERBOT LOGIK ***

    def create_market_order(self, symbol, side, amount, params={}):
        if not self.markets: return None
        try:
            order_params = {**params}
            if 'productType' not in order_params:
                order_params['productType'] = 'USDT-FUTURES' # Behalten wir zur Sicherheit bei
            rounded_amount = float(self.exchange.amount_to_precision(symbol, amount))
            if rounded_amount <= 0:
                logger.error(f"FEHLER: Berechneter Order-Betrag ist Null oder negativ ({rounded_amount}).")
                return None
            order = self.exchange.create_order(symbol, 'market', side, rounded_amount, params=order_params)
            return order
        except ccxt.InsufficientFunds as e:
            logger.error(f"FEHLER: Nicht genügend Guthaben (InsufficientFunds): {e}")
            raise e
        except Exception as e:
            logger.error(f"FEHLER beim Erstellen der Market Order ({symbol}, {side}, {amount}): {e}")
            return None

    # *** KORRIGIERTE TRIGGER ORDER FUNKTION - 1:1 WIE JAEGERBOT ***
    def place_trigger_market_order(self, symbol, side, amount, trigger_price, params={}):
        """
        Platziert eine Standard Trigger-Order (Stop-Loss oder Take-Profit).
        """
        if not self.markets: return None
        try:
            rounded_price = float(self.exchange.price_to_precision(symbol, trigger_price))
            rounded_amount = float(self.exchange.amount_to_precision(symbol, amount))
            if rounded_amount <= 0:
                logger.error(f"FEHLER: Berechneter Trigger-Order-Betrag ist Null ({rounded_amount}).")
                return None

            # Dies ist die exakte JaegerBot Parameterstruktur
            order_params = {
                'triggerPrice': rounded_price,
                'reduceOnly': params.get('reduceOnly', False)
            }
            order_params.update(params)

            logger.info(f"Sende Trigger Order: Side={side}, Amount={rounded_amount}, Params={order_params}")
            return self.exchange.create_order(symbol, 'market', side, rounded_amount, params=order_params)

        except Exception as e:
            logger.error(f"FEHLER beim Platzieren der Trigger Order ({symbol}, {side}, Params={order_params}): {e}", exc_info=True)
            return None

    def fetch_open_positions(self, symbol):
        if not self.markets: return []
        try:
            params = {'productType': 'USDT-FUTURES'}
            positions = self.exchange.fetch_positions([symbol], params=params)
            open_positions = []
            for p in positions:
                try:
                    contracts_str = p.get('contracts')
                    if contracts_str is not None and abs(float(contracts_str)) > 1e-9:
                        open_positions.append(p)
                except (ValueError, TypeError) as e:
                    logger.warning(f"Konnte 'contracts' für Position nicht in float umwandeln: {contracts_str}. Fehler: {e}.")
                    continue
            return open_positions
        except Exception as e:
            logger.error(f"Fehler bei fetch_open_positions für {symbol}: {e}", exc_info=True)
            return []

    def fetch_open_trigger_orders(self, symbol):
        if not self.markets: return []
        try:
            params = {'productType': 'USDT-FUTURES', 'stop': True}
            orders = self.exchange.fetch_open_orders(symbol, params=params)
            return orders
        except Exception as e:
            logger.error(f"Fehler bei fetch_open_trigger_orders für {symbol}: {e}")
            return []

    def fetch_balance_usdt(self):
        if not self.markets: return 0
        try:
            params = {'productType': 'USDT-FUTURES'}
            balance = self.exchange.fetch_balance(params=params)
            if 'USDT' in balance:
                if 'free' in balance['USDT'] and balance['USDT']['free'] is not None:
                    return float(balance['USDT']['free'])
                elif 'available' in balance['USDT'] and balance['USDT']['available'] is not None:
                    return float(balance['USDT']['available'])
                elif 'total' in balance['USDT'] and balance['USDT']['total'] is not None:
                    return float(balance['USDT']['total'])
            elif 'info' in balance and 'data' in balance['info'] and isinstance(balance['info']['data'], list):
                for asset_info in balance['info']['data']:
                    if asset_info.get('marginCoin') == 'USDT':
                        if 'available' in asset_info and asset_info['available'] is not None:
                            return float(asset_info['available'])
                        elif 'equity' in asset_info and asset_info['equity'] is not None:
                            return float(asset_info['equity'])
            logger.warning(f"Konnte freien USDT-Saldo nicht eindeutig bestimmen. Struktur: {balance}")
            return 0
        except Exception as e:
            logger.error(f"FEHLER beim Abrufen des USDT-Kontostandes: {e}", exc_info=True)
            return 0

    # *** KORRIGIERTE CANCEL ORDERS FUNKTION - 1:1 WIE JAEGERBOT ***
    def cancel_all_orders_for_symbol(self, symbol):
        """Storniert alle offenen Orders (normal und trigger) für ein Symbol."""
        if not self.markets: return 0
        cancelled_count = 0

        # 1. Normale Orders stornieren
        try:
            logger.info(f"Sende Befehl 'cancelAllOrders' (Normal) für {symbol}...")
            self.exchange.cancel_all_orders(symbol, params={'productType': 'USDT-FUTURES', 'stop': False})
            cancelled_count = 1
            time.sleep(0.5)
        except ccxt.ExchangeError as e:
            if 'Order not found' in str(e) or 'no order to cancel' in str(e).lower() or '22001' in str(e):
                logger.info("Keine normalen Orders zum Stornieren gefunden.")
                cancelled_count = 1
            else:
                logger.error(f"Fehler beim Stornieren normaler Orders: {e}")
                cancelled_count = 0
        except Exception as e:
            logger.error(f"Unerwarteter Fehler beim Stornieren normaler Orders: {e}")
            cancelled_count = 0

        # 2. Trigger Orders stornieren
        try:
            logger.info(f"Sende Befehl 'cancelAllOrders' (Trigger/Stop) für {symbol}...")
            self.exchange.cancel_all_orders(symbol, params={'productType': 'USDT-FUTURES', 'stop': True})
            cancelled_count = 1
            time.sleep(0.5)
        except ccxt.ExchangeError as e:
            if 'Order not found' in str(e) or 'no order to cancel' in str(e).lower() or '22001' in str(e):
                logger.info("Keine Trigger-Orders zum Stornieren gefunden.")
                cancelled_count = 1
            else:
                logger.error(f"Fehler beim Stornieren von Trigger-Orders: {e}")
                cancelled_count = 0
        except Exception as e:
            logger.error(f"Unerwarteter Fehler beim Stornieren von Trigger-Orders: {e}")
            cancelled_count = 0

        return cancelled_count

    def cleanup_all_open_orders(self, symbol):
        return self.cancel_all_orders_for_symbol(symbol)

    # *** KORRIGIERTE TRAILING STOP FUNKTION - NUTZT BITGET SPEZIFISCHE PARAMETER (WIE BEI ERFOLG) ***
    def place_trailing_stop_order(self, symbol, side, amount, activation_price, callback_rate_decimal, params={}):
        """
        Platziert eine Trailing Stop Market Order (Stop-Loss) über ccxt für Bitget.
        Nutzt die Bitget-spezifischen 'trailingTriggerPrice' und 'trailingPercent'-Parameter.
        :param callback_rate_decimal: Die Callback-Rate als Dezimalzahl (z.B. 0.01 für 1%)
        """
        if not self.markets: return None
        try:
            # Runden der Werte
            rounded_activation = float(self.exchange.price_to_precision(symbol, activation_price))
            rounded_amount = float(self.exchange.amount_to_precision(symbol, amount))
            if rounded_amount <= 0:
                logger.error(f"FEHLER: Berechneter TSL-Betrag ist Null ({rounded_amount}).")
                return None

            # Bitget TrailingPercent erwartet eine Zahl (float oder int), CCXT konvertiert sie intern
            callback_rate_float = callback_rate_decimal * 100 # In Prozent umwandeln (z.B. 0.5%)

            # Verwende die Bitget-spezifischen Parameter in den `params`
            order_params = {
                **params,
                'trailingTriggerPrice': rounded_activation, 
                'trailingPercent': callback_rate_float, 
                'productType': 'USDT-FUTURES'
            }

            logger.info(f"Sende TSL Order (MARKET): Side={side}, Amount={rounded_amount}, Activation={rounded_activation}, Callback={callback_rate_float}%")
            
            # Wichtig: Der Order-Typ ist 'market', da Trailing Stop in Bitget über spezielle Parameter definiert wird
            return self.exchange.create_order(symbol, 'market', side, rounded_amount, params=order_params)

        except Exception as e:
            logger.error(f"FEHLER beim Platzieren des Trailing Stop ({symbol}, {side}): {e} | Params: {order_params}", exc_info=True)
            return None
