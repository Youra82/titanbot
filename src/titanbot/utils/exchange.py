# src/titanbot/utils/exchange.py
import ccxt
import pandas as pd
from datetime import datetime, timezone, timedelta
import time # Für Rate Limiting
import logging # Logging hinzugefügt

# Verwende das Standard-Logging, um Fehler hier sichtbar zu machen
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
                 # Optional: User-Agent setzen, um Identifizierung zu erleichtern
                 # 'user-agent': 'TitanBot/1.0 (+https://github.com/YourRepo)',
            },
            'enableRateLimit': True, # CCXT internes Rate Limiting aktivieren
        })
        try:
            self.markets = self.exchange.load_markets()
            logger.info("Bitget Märkte erfolgreich geladen.") # Erfolgsmeldung
        except ccxt.AuthenticationError as e:
            logger.critical(f"FATAL: Bitget Authentifizierungsfehler: {e}. Bitte API-Schlüssel prüfen.")
            self.markets = None # Setze auf None, um spätere Fehler zu signalisieren
        except ccxt.NetworkError as e:
            logger.warning(f"WARNUNG: Netzwerkfehler beim Laden der Märkte: {e}. Versuche es später erneut.")
            self.markets = None
        except Exception as e:
            logger.warning(f"WARNUNG: Unerwarteter Fehler beim Laden der Märkte: {e}")
            self.markets = None


    def fetch_recent_ohlcv(self, symbol, timeframe, limit=100):
        if not self.markets: return pd.DataFrame() # Frühzeitiger Ausstieg, wenn Märkte nicht geladen
        try:
            # Stelle sicher, dass limit nicht zu groß ist (Bitget Limit oft 1000)
            effective_limit = min(limit, 1000)
            data = self.exchange.fetch_ohlcv(symbol, timeframe, limit=effective_limit)
            if not data: return pd.DataFrame() # Leere Liste zurückgeben, wenn keine Daten
            df = pd.DataFrame(data, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms', utc=True)
            df.set_index('timestamp', inplace=True)
            df.sort_index(inplace=True)
            return df
        except ccxt.NetworkError as e:
            logger.error(f"Netzwerkfehler bei fetch_recent_ohlcv für {symbol}: {e}") # Geändert zu error
            time.sleep(5) # Kurze Pause vor erneutem Versuch (falls im Loop aufgerufen)
            return pd.DataFrame()
        except ccxt.BadSymbol as e:
             logger.error(f"FEHLER: Ungültiges Symbol bei fetch_recent_ohlcv: {symbol}. {e}")
             return pd.DataFrame()
        except Exception as e:
            logger.error(f"Unerwarteter Fehler bei fetch_recent_ohlcv für {symbol}: {e}")
            return pd.DataFrame()

    def fetch_historical_ohlcv(self, symbol, timeframe, start_date_str, end_date_str, max_retries=3):
        if not self.markets: return pd.DataFrame()

        # Umwandlung und Validierung der Daten
        try:
            start_dt = pd.to_datetime(start_date_str + 'T00:00:00Z', utc=True)
            end_dt = pd.to_datetime(end_date_str + 'T23:59:59Z', utc=True) # Ende des Tages einschließen
            start_ts = int(start_dt.timestamp() * 1000)
            end_ts = int(end_dt.timestamp() * 1000)
        except ValueError as e:
            logger.error(f"FEHLER: Ungültiges Datumsformat: {e}")
            return pd.DataFrame()

        all_ohlcv = []
        current_ts = start_ts
        retries = 0
        limit = 1000 # Max Limit für Bitget

        while current_ts < end_ts and retries < max_retries:
            try:
                # logger.debug(f"Fetching {symbol} {timeframe} from {pd.to_datetime(current_ts, unit='ms', utc=True)}") # Debugging
                ohlcv = self.exchange.fetch_ohlcv(symbol, timeframe, since=current_ts, limit=limit)

                if not ohlcv:
                    # Keine weiteren Daten verfügbar für diesen Zeitraum
                    break

                # Filtere Daten, die außerhalb des gewünschten Bereichs liegen (passiert manchmal)
                ohlcv = [candle for candle in ohlcv if candle[0] <= end_ts]
                if not ohlcv: break # Alle gefilterten Kerzen lagen nach dem Enddatum

                all_ohlcv.extend(ohlcv)

                # Nächsten Startpunkt setzen: Zeitstempel der letzten Kerze + 1ms
                last_ts = ohlcv[-1][0]
                if last_ts >= current_ts: # Sicherstellen, dass wir Fortschritt machen
                     current_ts = last_ts + self.exchange.parse_timeframe(timeframe) * 1000 # Korrekter nächster Timestamp
                else:
                    # Sollte nicht passieren, aber als Sicherheitsnetz
                    logger.warning("WARNUNG: Kein Zeitfortschritt beim Datenabruf, breche ab.")
                    break

                retries = 0 # Reset retries on success

            except ccxt.RateLimitExceeded as e:
                logger.warning(f"RateLimitExceeded bei fetch_historical_ohlcv: {e}. Warte...")
                time.sleep(self.exchange.rateLimit / 1000 * 2) # Wartezeit erhöhen
                retries += 1
            except ccxt.NetworkError as e:
                logger.warning(f"Netzwerkfehler bei fetch_historical_ohlcv: {e}. Versuch {retries+1}/{max_retries}. Warte...")
                time.sleep(5 * (retries + 1)) # Längere Wartezeit bei Netzwerkfehlern
                retries += 1
            except ccxt.BadSymbol as e:
                 logger.error(f"FEHLER: Ungültiges Symbol bei fetch_historical_ohlcv: {symbol}. {e}")
                 return pd.DataFrame() # Kein Sinn, es erneut zu versuchen
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
        # Duplikate entfernen und nach Index sortieren
        df = df[~df.index.duplicated(keep='first')].sort_index()
        # Nur den ursprünglich angeforderten Datumsbereich zurückgeben
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
            # Bitget erfordert oft 'productType': 'USDT-FUTURES' oder 'MIX-FUTURES'
            params = {'productType': 'USDT-FUTURES'} # Annahme für USDT-M Futures
            response = self.exchange.set_margin_mode(mode, symbol, params=params)
            # logger.debug(f"Set margin mode response: {response}") # Debugging
            return True
        except ccxt.ExchangeError as e:
            # Ignoriere Fehler, wenn der Modus bereits gesetzt ist
            if 'Margin mode is the same' in str(e) or '45115' in str(e): # 45115 ist Bitget Code für gleichen Modus
                 # logger.info(f"Info: Margin-Modus für {symbol} ist bereits '{mode}'.")
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
            # Bitget erfordert 'marginCoin' und oft 'holdSide' (long/short)
            params = {'marginCoin': 'USDT'}
            # Versuche für beide Seiten zu setzen (Bitget unterscheidet oft)
            response_long = self.exchange.set_leverage(level, symbol, params={'holdSide': 'long', **params})
            response_short = self.exchange.set_leverage(level, symbol, params={'holdSide': 'short', **params})
            # logger.debug(f"Set leverage response long: {response_long}, short: {response_short}") # Debugging
            return True
        except ccxt.ExchangeError as e:
            # Ignoriere Fehler, wenn Hebel bereits gesetzt ist
            if 'Leverage not changed' in str(e) or '45116' in str(e): # 45116 ist Bitget Code
                 # logger.info(f"Info: Leverage für {symbol} ist bereits {level}x.")
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
            # Füge Standardparameter hinzu, die Bitget benötigt (falls ccxt es nicht automatisch macht)
            order_params = {**params}
            if 'productType' not in order_params: # Nur hinzufügen, wenn nicht schon vorhanden
                 order_params['productType'] = 'USDT-FUTURES'

            # Betrag auf Markt-Genauigkeit runden
            rounded_amount = float(self.exchange.amount_to_precision(symbol, amount))
            if rounded_amount <= 0:
                 logger.error(f"FEHLER: Berechneter Order-Betrag ist Null oder negativ ({rounded_amount}). Order wird nicht platziert.")
                 return None

            order = self.exchange.create_order(symbol, 'market', side, rounded_amount, params=order_params)
            return order
        except ccxt.InsufficientFunds as e:
            logger.error(f"FEHLER: Nicht genügend Guthaben für Market Order ({symbol}, {side}, {amount}): {e}")
            raise e # Erneutes Auslösen, damit der Trade Manager es fängt
        except Exception as e:
            logger.error(f"FEHLER beim Erstellen der Market Order ({symbol}, {side}, {amount}): {e}")
            return None


    def place_trigger_market_order(self, symbol, side, amount, trigger_price, params={}):
        """ Platziert Stop-Loss oder Take-Profit Trigger Orders, die als Market-Orders ausgeführt werden. """
        if not self.markets: return None
        try:
            # Preise und Beträge auf Markt-Genauigkeit runden
            market = self.exchange.market(symbol)
            rounded_price = float(self.exchange.price_to_precision(symbol, trigger_price))
            rounded_amount = float(self.exchange.amount_to_precision(symbol, amount))
            if rounded_amount <= 0:
                 logger.error(f"FEHLER: Berechneter Trigger-Order-Betrag ist Null oder negativ ({rounded_amount}). Order wird nicht platziert.")
                 return None

            # --- KORRIGIERTE BITGET TRIGGER MARKET ORDER PARAMETER ---
            order_params = {
                'stopPrice': rounded_price,        # Preis, bei dem die Market Order ausgelöst wird
                'triggerType': 'market_price',     # Auslösen basierend auf dem letzten Marktpreis
                'reduceOnly': params.get('reduceOnly', False), # Wichtig für SL/TP
                # 'productType': 'USDT-FUTURES', # Wird oft von ccxt automatisch gesetzt
                # 'planType': '...', # ENTFERNT, da dies den Fehler verursacht hat
            }
            order_params.update(params) # Fügt zusätzliche übergebene Parameter hinzu

            # Verwende 'market' Typ mit 'stopPrice' Parameter, um eine Stop-Market-Order zu erstellen
            # ccxt übersetzt dies normalerweise korrekt für Bitget.
            order = self.exchange.create_order(symbol, 'market', side, rounded_amount, params=order_params)
            logger.info(f"Trigger Order gesendet: Side={side}, Amount={rounded_amount}, StopPrice={rounded_price}, Params={order_params}") # Mehr Details loggen
            return order
        except ccxt.ExchangeError as e:
             # Logge spezifische Börsenfehler detaillierter
             logger.error(f"BÖRSEN-FEHLER beim Platzieren der Trigger Order ({symbol}, {side}, Amount={amount}, Trigger={trigger_price}): {e} | Params: {order_params}")
             return None
        except Exception as e:
            # Logge allgemeine Fehler detaillierter
            logger.error(f"ALLGEMEINER FEHLER beim Platzieren der Trigger Order ({symbol}, {side}, Amount={amount}, Trigger={trigger_price}): {e} | Params: {order_params}", exc_info=True)
            return None

    def fetch_open_positions(self, symbol):
         if not self.markets: return []
         try:
             # Bitget benötigt productType
             params = {'productType': 'USDT-FUTURES'}
             positions = self.exchange.fetch_positions([symbol], params=params)
             # Filtere nach Positionen mit tatsächlicher Größe (contracts > 0)
             # Konvertiere 'contracts' sicher zu float
             open_positions = []
             for p in positions:
                  try:
                       contracts_str = p.get('contracts')
                       if contracts_str is not None and abs(float(contracts_str)) > 1e-9: # Toleranz für sehr kleine Werte
                            open_positions.append(p)
                  except (ValueError, TypeError) as e:
                       logger.warning(f"Konnte 'contracts' für Position nicht in float umwandeln: {contracts_str}. Fehler: {e}. Position: {p}")
                       continue # Ignoriere diese Position
             return open_positions
         except Exception as e:
             logger.error(f"Fehler bei fetch_open_positions für {symbol}: {e}", exc_info=True) # Mehr Details loggen
             return []

    def fetch_open_trigger_orders(self, symbol):
         if not self.markets: return []
         try:
             # Bitget: Trigger Orders sind 'Stop' Orders in CCXT Jargon
             # und erfordern oft productType
             params = {'productType': 'USDT-FUTURES', 'stop': True} # 'stop': True ist wichtig für Trigger
             orders = self.exchange.fetch_open_orders(symbol, params=params)
             return orders
         except Exception as e:
             logger.error(f"Fehler bei fetch_open_trigger_orders für {symbol}: {e}")
             return []

    def fetch_balance_usdt(self):
        if not self.markets: return 0
        try:
            # Bitget fetch_balance benötigt oft productType für Futures
            params = {'productType': 'USDT-FUTURES'}
            balance = self.exchange.fetch_balance(params=params)

            # Suche nach USDT in der Balance-Struktur
            if 'USDT' in balance:
                # Klassisches Konto oder Spot-Teil könnte 'free' haben
                if 'free' in balance['USDT'] and balance['USDT']['free'] is not None:
                    return float(balance['USDT']['free'])
                 # Futures-Teil (Unified?) hat oft nur 'total' oder 'available'
                elif 'available' in balance['USDT'] and balance['USDT']['available'] is not None: # Unified hat oft 'available'
                    return float(balance['USDT']['available'])
                elif 'total' in balance['USDT'] and balance['USDT']['total'] is not None:
                     return float(balance['USDT']['total']) # Fallback

            # Manchmal ist es verschachtelt unter 'info' -> 'data' (neue API Versionen?)
            elif 'info' in balance and 'data' in balance['info'] and isinstance(balance['info']['data'], list):
                for asset_info in balance['info']['data']:
                     if asset_info.get('marginCoin') == 'USDT':
                         # Suche nach 'available' oder 'equity'
                         if 'available' in asset_info and asset_info['available'] is not None:
                              return float(asset_info['available'])
                         elif 'equity' in asset_info and asset_info['equity'] is not None:
                              return float(asset_info['equity']) # Equity als Annäherung

            # Fallback, wenn USDT nirgends direkt gefunden wird
            logger.warning(f"Konnte freien USDT-Saldo nicht eindeutig bestimmen. Struktur: {balance}")
            return 0

        except Exception as e:
            logger.error(f"FEHLER beim Abrufen des USDT-Kontostandes: {e}", exc_info=True)
            return 0

    def cancel_all_orders_for_symbol(self, symbol):
        """Storniert ALLE Order-Typen (Limit, Market if cancellable, Trigger) für ein Symbol."""
        if not self.markets: return 0
        cancelled_count = 0 # Zähler für tatsächlich versuchte Stornierungen
        try:
            # Bitget cancel_all_orders benötigt productType
            params = {'productType': 'USDT-FUTURES'}
            response = self.exchange.cancel_all_orders(symbol, params=params)
            # Logge die Antwort der Börse (kann nützlich sein)
            logger.info(f"Befehl 'cancel_all_orders' für {symbol} gesendet. Antwort: {response}")
            # Annahme: Wenn keine Exception auftritt, war der Befehl erfolgreich
            # Genaue Zählung ist schwierig, wir geben 1 zurück, wenn erfolgreich
            cancelled_count = 1 # Signalisiert, dass der Befehl erfolgreich war

            # Kurze Pause geben, damit Orders verarbeitet werden
            time.sleep(1)

        except ccxt.ExchangeError as e:
             # Ignoriere Fehler, wenn keine Orders zum Stornieren da sind
            if 'Order not found' in str(e) or 'no order to cancel' in str(e).lower() or '22001' in str(e): # 22001 Bitget: no orders
                logger.info(f"Info: Keine offenen Orders für {symbol} zum Stornieren gefunden.")
                cancelled_count = 1 # Befehl war technisch erfolgreich (es gab nichts zu tun)
            else:
                logger.error(f"FEHLER bei cancel_all_orders für {symbol}: {e}")
                cancelled_count = 0 # Signalisiert Fehler
        except Exception as e:
            logger.error(f"Unerwarteter FEHLER bei cancel_all_orders für {symbol}: {e}")
            cancelled_count = 0 # Signalisiert Fehler
        return cancelled_count # Gibt 1 zurück, wenn Befehl erfolgreich oder nichts zu tun war, 0 bei Fehler

    def cleanup_all_open_orders(self, symbol):
        # Nutzt jetzt die robustere cancel_all_orders Funktion
        return self.cancel_all_orders_for_symbol(symbol)

    # --- Trailing Stop Funktion ---
    def place_trailing_stop_order(self, symbol, side, amount, activation_price, callback_rate_decimal, params={}):
        """
        Platziert eine Trailing Stop Market Order (Stop-Loss) über ccxt für Bitget.
        Bitget erfordert 'triggerPrice' (Aktivierung) und 'callbackRate'.

        :param callback_rate_decimal: Die Callback-Rate als Dezimalzahl (z.B. 0.01 für 1%)
        """
        if not self.markets: return None
        try:
            rounded_activation = float(self.exchange.price_to_precision(symbol, activation_price))
            rounded_amount = float(self.exchange.amount_to_precision(symbol, amount))
            if rounded_amount <= 0:
                 logger.error(f"FEHLER: Berechneter Trailing-Stop-Betrag ist Null oder negativ ({rounded_amount}). Order wird nicht platziert.")
                 return None

            # ccxt für bitget braucht die Callback-Rate als String in Prozent, z.B. 0.01 -> "1"
            # Stelle sicher, dass es korrekt formatiert ist (z.B. 0.5 für 0.5%)
            callback_rate_str = "{:.2f}".format(callback_rate_decimal * 100).rstrip('0').rstrip('.')
            if not callback_rate_str: callback_rate_str = "0" # Fallback für 0%

            order_params = {
                **params, # Übernimmt z.B. reduceOnly
                'stopPrice': rounded_activation, # Aktivierungspreis wird als stopPrice übergeben
                'trailingPercent': callback_rate_str, # Callback Rate in Prozent als String
                'type': 'trailing_stop_market', # Expliziter Typ für Trailing Stop Market Order
                'triggerPriceType': 'market_price', # Auslösen basierend auf dem Marktpreis
                # 'productType': 'USDT-FUTURES', # Oft implizit
            }

            logger.info(f"Sende Trailing-Stop-Order: Side={side}, Amount={rounded_amount}, Params={order_params}")
            # Trailing Stop ist ein spezieller Order-Typ
            order = self.exchange.create_order(symbol, 'trailing_stop_market', side, rounded_amount, params=order_params)
            return order
        except ccxt.NotSupported as e:
             logger.error(f"FEHLER: Trailing Stop Orders werden von ccxt für Bitget möglicherweise nicht (oder nicht so) unterstützt: {e}")
             # Fallback auf fixen Stop?
             # Hier könnte man stattdessen place_trigger_market_order mit dem initialen SL aufrufen
             initial_sl_price = activation_price # Annahme: Aktivierungspreis ist der initiale SL? Besser: separaten SL übergeben.
             logger.warning(f"Versuche stattdessen, einen fixen Stop bei {initial_sl_price} zu setzen.")
             # return self.place_trigger_market_order(symbol, side, amount, initial_sl_price, params) # Braucht initialen SL
             return None # Oder einfach fehlschlagen
        except ccxt.ExchangeError as e:
            logger.error(f"BÖRSEN-FEHLER beim Platzieren des Trailing Stop ({symbol}, {side}, Amount={amount}): {e} | Params: {order_params}")
            return None
        except Exception as e:
            logger.error(f"ALLGEMEINER FEHLER beim Platzieren des Trailing Stop ({symbol}, {side}, Amount={amount}): {e} | Params: {order_params}", exc_info=True)
            return None
