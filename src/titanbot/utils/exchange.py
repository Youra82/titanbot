# /root/titanbot/src/titanbot/utils/exchange.py
import ccxt
import pandas as pd
from datetime import datetime, timezone, timedelta
import time
import logging

# Verwende das Standard-Logging
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

    def fetch_recent_ohlcv(self, symbol, timeframe, limit=100):
        if not self.markets: return pd.DataFrame()
        try:
            effective_limit = min(limit, 1000)
            data = self.exchange.fetch_ohlcv(symbol, timeframe, limit=effective_limit)
            if not data: return pd.DataFrame()
            df = pd.DataFrame(data, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms', utc=True)
            df.set_index('timestamp', inplace=True)
            df.sort_index(inplace=True)
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

    def set_margin_mode(self, symbol, mode='isolated'):
        if not self.markets: return False
        try:
            params = {'productType': 'USDT-FUTURES'}
            self.exchange.set_margin_mode(mode, symbol, params=params)
            return True
        except ccxt.ExchangeError as e:
            if 'Margin mode is the same' in str(e) or '45115' in str(e):
                 return True
            else:
                logger.error(f"FEHLER: Margin-Modus konnte für {symbol} nicht auf '{mode}' gesetzt werden: {e}")
                return False
        except Exception as e:
            logger.error(f"Unerwarteter Fehler bei set_margin_mode für {symbol}: {e}")
            return False

    def set_leverage(self, symbol, level=10):
        if not self.markets: return False
        try:
            params = {'marginCoin': 'USDT'}
            self.exchange.set_leverage(level, symbol, params={'holdSide': 'long', **params})
            self.exchange.set_leverage(level, symbol, params={'holdSide': 'short', **params})
            return True
        except ccxt.ExchangeError as e:
            if 'Leverage not changed' in str(e) or '45116' in str(e):
                 return True
            else:
                 logger.error(f"FEHLER: Leverage konnte für {symbol} nicht auf {level}x gesetzt werden: {e}")
                 return False
        except Exception as e:
            logger.error(f"Unerwarteter Fehler bei set_leverage für {symbol}: {e}")
            return False

    def create_market_order(self, symbol, side, amount, params={}):
        if not self.markets: return None
        try:
            order_params = {**params}
            if 'productType' not in order_params:
                 order_params['productType'] = 'USDT-FUTURES'
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

    # *** KORRIGIERTE TRIGGER ORDER FUNKTION ***
    def place_trigger_market_order(self, symbol, side, amount, trigger_price, params={}, plan_type='normal_plan'):
        """ 
        Platziert eine Standard Trigger-Order (Stop-Loss oder Take-Profit).
        'plan_type' wird hinzugefügt, um 'tp_plan' oder 'sl_plan' zu übergeben.
        """
        if not self.markets: return None
        try:
            rounded_price = float(self.exchange.price_to_precision(symbol, trigger_price))
            rounded_amount = float(self.exchange.amount_to_precision(symbol, amount))
            if rounded_amount <= 0:
                 logger.error(f"FEHLER: Berechneter Trigger-Order-Betrag ist Null ({rounded_amount}).")
                 return None
            
            # Parameter gemäß Bitget-Doku für "Plan Orders"
            order_params = {
                **params, # Übernimmt z.B. reduceOnly
                'planType': plan_type,             # NEU: 'tp_plan' oder 'sl_plan'
                'triggerPrice': rounded_price,
                'triggerPriceType': 'market_price' # Löst basierend auf dem Marktpreis aus (oder 'last_price')
            }
            
            # 'market' Typ signalisiert ccxt, eine Market-Order auszulösen, wenn der Trigger erreicht wird
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
             # 'stop': True ist der ccxt-Weg, um nach Trigger-Orders zu filtern
             # Bitget API unterscheidet Plan-Orders. Wir müssen evtl. 'fetchOrders' mit Status 'not_triggered'
             # ODER 'fetchOpenOrders' mit 'stop': True verwenden. 'stop':True ist der ccxt-Standard.
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

    def cancel_all_orders_for_symbol(self, symbol):
        if not self.markets: return 0
        cancelled_count = 0
        try:
            # Bitget API: 'cancelAllOrders' storniert normale Orders.
            # Wir brauchen 'cancel_all_plan_orders' für SL/TP.
            # ccxt's `cancelAllOrders` sollte dies *eigentlich* handhaben, aber wir machen es explizit.
            
            # 1. Normale Orders stornieren (falls `cancelAllOrders` nur die macht)
            # response_normal = self.exchange.cancel_all_orders(symbol, params={'productType': 'USDT-FUTURES'})
            # logger.info(f"Befehl 'cancel_all_orders' (normal) gesendet. Antwort: {response_normal}")
            
            # 2. Plan-Orders (SL/TP/TSL) stornieren
            # Dies ist ein privater API-Call, den wir über ccxt's `privatePost` machen
            logger.info("Sende Befehl zum Stornieren aller Plan-Orders (SL/TP/TSL)...")
            response_plan = self.exchange.privatePost('/api/v2/spot/trade/cancel-all-plan-orders', {
                'symbol': self.exchange.market_id(symbol),
                'productType': 'USDT-FUTURES' # Oder 'umcbl' je nach API-Version von ccxt
            })
            logger.info(f"Antwort von 'cancel-all-plan-orders': {response_plan}")
            
            cancelled_count = 1 # Signalisiert, dass Befehle gesendet wurden
            time.sleep(1)

        except ccxt.ExchangeError as e:
            if 'Order not found' in str(e) or 'no order to cancel' in str(e).lower() or '22001' in str(e) or '40411' in str(e):
                logger.info(f"Info: Keine offenen Orders für {symbol} zum Stornieren gefunden.")
                cancelled_count = 1
            else:
                logger.error(f"FEHLER bei cancel_all_orders (oder Plan-Orders): {e}")
                cancelled_count = 0
        except Exception as e:
            logger.error(f"Unerwarteter FEHLER bei cancel_all_orders: {e}")
            cancelled_count = 0
        return cancelled_count

    def cleanup_all_open_orders(self, symbol):
        # Ruft die robustere Funktion auf, die (hoffentlich) auch Plan-Orders storniert
        return self.cancel_all_orders_for_symbol(symbol)

    # *** KORRIGIERTE TRAILING STOP FUNKTION ***
    def place_trailing_stop_order(self, symbol, side, amount, activation_price, callback_rate_decimal, params={}):
        """
        Platziert eine Trailing Stop Market Order (Stop-Loss) über ccxt für Bitget.
        :param callback_rate_decimal: Die Callback-Rate als Dezimalzahl (z.B. 0.01 für 1%)
        """
        if not self.markets: return None
        try:
            rounded_activation = float(self.exchange.price_to_precision(symbol, activation_price))
            rounded_amount = float(self.exchange.amount_to_precision(symbol, amount))
            if rounded_amount <= 0:
                 logger.error(f"FEHLER: Berechneter TSL-Betrag ist Null ({rounded_amount}).")
                 return None

            callback_rate_str = "{:.2f}".format(callback_rate_decimal * 100).rstrip('0').rstrip('.')
            if not callback_rate_str: callback_rate_str = "0"

            # Parameter gemäß Bitget-Doku für "Plan Orders"
            order_params = {
                **params, # Übernimmt z.B. reduceOnly
                'planType': 'moving_sl_plan',       # ***** KORRIGIERT: 'moving_sl_plan' statt 'trailing_stop' *****
                'triggerPrice': rounded_activation,   # Aktivierungspreis
                'callbackRate': callback_rate_str,    # Callback in Prozent (z.B. "0.5")
                'triggerPriceType': 'market_price'    # Löst basierend auf dem Marktpreis aus (oder 'last_price')
            }

            logger.info(f"Sende Trailing-Stop-Order: Side={side}, Amount={rounded_amount}, Params={order_params}")
            # 'market' Typ signalisiert ccxt, eine Market-Order auszulösen
            return self.exchange.create_order(symbol, 'market', side, rounded_amount, params=order_params)
        
        except Exception as e:
            logger.error(f"FEHLER beim Platzieren des Trailing Stop ({symbol}, {side}): {e} | Params: {order_params}", exc_info=True)
            return None # Gibt None zurück, um den Fallback auszulösen
