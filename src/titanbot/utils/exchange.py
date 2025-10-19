# src/titanbot/utils/exchange.py # <-- Kommentar geändert
import ccxt
import pandas as pd
from datetime import datetime, timezone, timedelta
import time # Für Rate Limiting

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
        except ccxt.AuthenticationError as e:
             print(f"FATAL: Bitget Authentifizierungsfehler: {e}. Bitte API-Schlüssel prüfen.")
             # Hier könnte man sys.exit(1) aufrufen, wenn der Fehler kritisch ist
             self.markets = None # Setze auf None, um spätere Fehler zu signalisieren
        except ccxt.NetworkError as e:
             print(f"WARNUNG: Netzwerkfehler beim Laden der Märkte: {e}. Versuche es später erneut.")
             self.markets = None
        except Exception as e:
             print(f"WARNUNG: Unerwarteter Fehler beim Laden der Märkte: {e}")
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
            print(f"Netzwerkfehler bei fetch_recent_ohlcv für {symbol}: {e}")
            time.sleep(5) # Kurze Pause vor erneutem Versuch (falls im Loop aufgerufen)
            return pd.DataFrame()
        except ccxt.BadSymbol as e:
             print(f"FEHLER: Ungültiges Symbol bei fetch_recent_ohlcv: {symbol}. {e}")
             return pd.DataFrame()
        except Exception as e:
            print(f"Unerwarteter Fehler bei fetch_recent_ohlcv für {symbol}: {e}")
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
            print(f"FEHLER: Ungültiges Datumsformat: {e}")
            return pd.DataFrame()

        all_ohlcv = []
        current_ts = start_ts
        retries = 0
        limit = 1000 # Max Limit für Bitget

        while current_ts < end_ts and retries < max_retries:
            try:
                # print(f"Fetching {symbol} {timeframe} from {pd.to_datetime(current_ts, unit='ms', utc=True)}") # Debugging
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
                     current_ts = last_ts + 1 
                else:
                     # Sollte nicht passieren, aber als Sicherheitsnetz
                     print("WARNUNG: Kein Zeitfortschritt beim Datenabruf, breche ab.")
                     break 

                retries = 0 # Reset retries on success

            except ccxt.RateLimitExceeded as e:
                print(f"RateLimitExceeded bei fetch_historical_ohlcv: {e}. Warte...")
                time.sleep(self.exchange.rateLimit / 1000 * 2) # Wartezeit erhöhen
                retries += 1
            except ccxt.NetworkError as e:
                print(f"Netzwerkfehler bei fetch_historical_ohlcv: {e}. Versuch {retries+1}/{max_retries}. Warte...")
                time.sleep(5 * (retries + 1)) # Längere Wartezeit bei Netzwerkfehlern
                retries += 1
            except ccxt.BadSymbol as e:
                 print(f"FEHLER: Ungültiges Symbol bei fetch_historical_ohlcv: {symbol}. {e}")
                 return pd.DataFrame() # Kein Sinn, es erneut zu versuchen
            except Exception as e:
                print(f"Unerwarteter Fehler bei fetch_historical_ohlcv: {e}. Versuch {retries+1}/{max_retries}.")
                time.sleep(5)
                retries += 1

        if not all_ohlcv:
            print(f"Keine historischen Daten für {symbol} ({timeframe}) im Zeitraum {start_date_str} - {end_date_str} gefunden.")
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
              print(f"Fehler bei fetch_ticker für {symbol}: {e}")
              return None

    def set_margin_mode(self, symbol, mode='isolated'):
        if not self.markets: return False
        try:
            # Bitget erfordert oft 'productType': 'USDT-FUTURES' oder 'MIX-FUTURES'
            params = {'productType': 'USDT-FUTURES'} # Annahme für USDT-M Futures
            response = self.exchange.set_margin_mode(mode, symbol, params=params)
            # print(f"Set margin mode response: {response}") # Debugging
            return True
        except ccxt.ExchangeError as e:
             # Ignoriere Fehler, wenn der Modus bereits gesetzt ist
            if 'Margin mode is the same' in str(e) or '45115' in str(e): # 45115 ist Bitget Code für gleichen Modus
                # print(f"Info: Margin-Modus für {symbol} ist bereits '{mode}'.")
                return True
            else:
                 print(f"FEHLER: Margin-Modus konnte für {symbol} nicht auf '{mode}' gesetzt werden: {e}")
                 return False
        except Exception as e:
            print(f"Unerwarteter Fehler bei set_margin_mode für {symbol}: {e}")
            return False

    def set_leverage(self, symbol, level=10):
         if not self.markets: return False
         try:
             # Bitget erfordert 'marginCoin' und oft 'holdSide' (long/short)
             params = {'marginCoin': 'USDT'} 
             # Versuche für beide Seiten zu setzen (Bitget unterscheidet oft)
             response_long = self.exchange.set_leverage(level, symbol, params={'holdSide': 'long', **params})
             response_short = self.exchange.set_leverage(level, symbol, params={'holdSide': 'short', **params})
             # print(f"Set leverage response long: {response_long}, short: {response_short}") # Debugging
             return True
         except ccxt.ExchangeError as e:
             # Ignoriere Fehler, wenn Hebel bereits gesetzt ist
             if 'Leverage not changed' in str(e) or '45116' in str(e): # 45116 ist Bitget Code
                 # print(f"Info: Leverage für {symbol} ist bereits {level}x.")
                 return True
             else:
                  print(f"FEHLER: Leverage konnte für {symbol} nicht auf {level}x gesetzt werden: {e}")
                  return False
         except Exception as e:
             print(f"Unerwarteter Fehler bei set_leverage für {symbol}: {e}")
             return False

    def create_market_order(self, symbol, side, amount, params={}):
         if not self.markets: return None
         try:
             # Füge Standardparameter hinzu, die Bitget benötigt
             order_params = {'productType': 'USDT-FUTURES', **params}
             order = self.exchange.create_order(symbol, 'market', side, amount, params=order_params)
             return order
         except ccxt.InsufficientFunds as e:
             print(f"FEHLER: Nicht genügend Guthaben für Market Order ({symbol}, {side}, {amount}): {e}")
             raise e # Erneutes Auslösen, damit der Trade Manager es fängt
         except Exception as e:
             print(f"FEHLER beim Erstellen der Market Order ({symbol}, {side}, {amount}): {e}")
             return None


    def place_trigger_market_order(self, symbol, side, amount, trigger_price, params={}):
         if not self.markets: return None
         try:
             # Preise und Beträge auf Markt-Genauigkeit runden
             market = self.exchange.market(symbol)
             rounded_price = float(self.exchange.price_to_precision(symbol, trigger_price))
             rounded_amount = float(self.exchange.amount_to_precision(symbol, amount))
             
             # Bitget Trigger Order Parameter
             order_params = {
                 'productType': 'USDT-FUTURES',
                 'triggerPrice': rounded_price,
                 'planType': 'moving_plan', # Oder 'normal_plan', je nach Bedarf für TP/SL
                 'triggerType': 'market_price', # Oder 'mark_price', 'index_price'
                 'reduceOnly': params.get('reduceOnly', False)
             }
             
             # Füge zusätzliche Params hinzu
             order_params.update(params)
             
             # Erstelle die Order ('market' Typ wird für Trigger-Market verwendet)
             order = self.exchange.create_order(symbol, 'market', side, rounded_amount, params=order_params)
             return order
         except Exception as e:
             print(f"FEHLER beim Platzieren der Trigger Order ({symbol}, {side}, Amount={amount}, Trigger={trigger_price}): {e}")
             return None

    def fetch_open_positions(self, symbol):
         if not self.markets: return []
         try:
             # Bitget benötigt productType
             params = {'productType': 'USDT-FUTURES'}
             positions = self.exchange.fetch_positions([symbol], params=params)
             # Filtere nach Positionen mit tatsächlicher Größe (contracts > 0)
             open_positions = [p for p in positions if p.get('contracts') is not None and abs(float(p.get('contracts', 0.0))) > 0.0]
             return open_positions
         except Exception as e:
             print(f"Fehler bei fetch_open_positions für {symbol}: {e}")
             return []

    def fetch_open_trigger_orders(self, symbol):
         if not self.markets: return []
         try:
             # Bitget: Trigger Orders sind 'Stop' Orders in CCXT Jargon
             # und erfordern oft productType
             params = {'productType': 'USDT-FUTURES', 'stop': True} 
             orders = self.exchange.fetch_open_orders(symbol, params=params)
             return orders
         except Exception as e:
             print(f"Fehler bei fetch_open_trigger_orders für {symbol}: {e}")
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
                  if 'free' in balance['USDT']:
                      return float(balance['USDT']['free'])
                  # Futures-Teil (Unified?) hat oft nur 'total' oder 'available'
                  elif 'available' in balance['USDT']: # Unified hat oft 'available'
                       return float(balance['USDT']['available'])
                  elif 'total' in balance['USDT']:
                       return float(balance['USDT']['total']) # Fallback
                       
             # Manchmal ist es verschachtelt unter 'info' -> 'data'
             elif 'info' in balance and 'data' in balance['info'] and isinstance(balance['info']['data'], list):
                  for asset_info in balance['info']['data']:
                       if asset_info.get('marginCoin') == 'USDT':
                            # Suche nach 'available' oder 'equity'
                            if 'available' in asset_info:
                                 return float(asset_info['available'])
                            elif 'equity' in asset_info:
                                  return float(asset_info['equity']) # Equity als Annäherung
                                  
             # Fallback, wenn USDT nirgends direkt gefunden wird
             print("WARNUNG: Konnte freien USDT-Saldo nicht eindeutig bestimmen. Struktur:", balance)
             return 0

         except Exception as e:
             print(f"FEHLER beim Abrufen des USDT-Kontostandes: {e}")
             return 0

    def cancel_all_orders_for_symbol(self, symbol):
         """Storniert ALLE Order-Typen (Limit, Market if cancellable, Trigger) für ein Symbol."""
         if not self.markets: return 0
         cancelled_count = 0
         try:
             # Bitget cancel_all_orders benötigt productType
             params = {'productType': 'USDT-FUTURES'}
             response = self.exchange.cancel_all_orders(symbol, params=params)
             # Die Antwort von Bitget ist hier nicht sehr aussagekräftig über die Anzahl
             # Wir loggen einfach, dass der Befehl gesendet wurde
             print(f"Befehl 'cancel_all_orders' für {symbol} gesendet. Antwort: {response}")
             # Annahme: Wenn keine Exception auftritt, waren potenziell Orders vorhanden
             # Genaue Zählung ist schwierig ohne fetch_open_orders vorher/nachher
             
             # Kurze Pause geben, damit Orders verarbeitet werden
             time.sleep(1)
             
             # Doppelte Prüfung mit fetch_open_orders (kann aber Race Conditions haben)
             # trigger_orders = self.fetch_open_trigger_orders(symbol)
             # normal_orders = self.exchange.fetch_open_orders(symbol, params={'stop': False, 'productType': 'USDT-FUTURES'})
             # if not trigger_orders and not normal_orders:
             #      print("Bestätigt: Keine offenen Orders mehr für", symbol)
             
             # Da die genaue Zahl schwer ist, geben wir 1 zurück, wenn erfolgreich
             cancelled_count = 1 # Signalisiert, dass der Befehl erfolgreich war

         except ccxt.ExchangeError as e:
              # Ignoriere Fehler, wenn keine Orders zum Stornieren da sind (variiert je nach Exchange)
              if 'Order not found' in str(e) or 'no orders to cancel' in str(e).lower() or '40411' in str(e): # 40411 Bitget: order not exists
                   print(f"Info: Keine offenen Orders für {symbol} zum Stornieren gefunden.")
              else:
                   print(f"FEHLER bei cancel_all_orders für {symbol}: {e}")
         except Exception as e:
             print(f"Unerwarteter FEHLER bei cancel_all_orders für {symbol}: {e}")
         return cancelled_count # Gibt 1 zurück, wenn Befehl erfolgreich, 0 bei Fehler


    def cleanup_all_open_orders(self, symbol):
         # Nutzt jetzt die robustere cancel_all_orders Funktion
         return self.cancel_all_orders_for_symbol(symbol)
