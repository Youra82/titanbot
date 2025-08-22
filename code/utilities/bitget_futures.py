# code/utilities/bitget_futures.py
import ccxt
import time
import pandas as pd
import json
from typing import Any, Optional, Dict, List

class BitgetFutures():
    def __init__(self, api_setup: Optional[Dict[str, Any]] = None) -> None:
        api_setup = api_setup or {}
        # HIER IST DIE KORREKTUR: 'future' wurde zu 'swap' geändert
        api_setup.setdefault("options", {"defaultType": "swap"})
        self.session = ccxt.bitget(api_setup)
        self.markets = self.session.load_markets()
    
    def _handle_exception(self, operation: str, e: Exception):
        # Versuche, eine spezifischere Fehlermeldung von der Börse zu extrahieren
        error_message = str(e)
        if hasattr(e, 'body'):
            try:
                response_body = json.loads(e.body)
                if 'msg' in response_body:
                    error_message = response_body['msg']
            except:
                pass
        raise Exception(f"Fehler bei '{operation}': {error_message}") from e

    def set_margin_mode(self, symbol: str, margin_mode: str):
        try:
            return self.session.set_margin_mode(margin_mode, symbol)
        except Exception as e:
            # Ignoriere den Fehler, wenn der Modus bereits korrekt eingestellt ist
            if 'Margin mode is the same' in str(e):
                pass
            else:
                self._handle_exception(f"set_margin_mode für {symbol}", e)

    def set_leverage(self, symbol: str, leverage: int, margin_mode: str = 'isolated'):
        try:
            if margin_mode == 'isolated':
                # Bei 'isolated' muss der Hebel für Long und Short getrennt gesetzt werden
                self.session.set_leverage(leverage, symbol, {'holdSide': 'long'})
                self.session.set_leverage(leverage, symbol, {'holdSide': 'short'})
            else: # cross
                self.session.set_leverage(leverage, symbol)
        except Exception as e:
            # Ignoriere den Fehler, wenn der Hebel bereits korrekt eingestellt ist
            if 'Leverage is the same' in str(e):
                pass
            else:
                self._handle_exception(f"set_leverage für {symbol}", e)

    def fetch_ticker(self, symbol: str) -> Dict[str, Any]:
        try:
            return self.session.fetch_ticker(symbol)
        except Exception as e:
            self._handle_exception(f"fetch_ticker für {symbol}", e)

    def fetch_balance(self) -> Dict[str, Any]:
        try:
            return self.session.fetch_balance()
        except Exception as e:
            self._handle_exception("fetch_balance", e)

    def fetch_open_orders(self, symbol: str) -> List[Dict[str, Any]]:
        try:
            return self.session.fetch_open_orders(symbol)
        except Exception as e:
            self._handle_exception(f"fetch_open_orders für {symbol}", e)

    def fetch_open_trigger_orders(self, symbol: str) -> List[Dict[str, Any]]:
        try:
            return self.session.fetch_open_orders(symbol, params={'stop': True})
        except Exception as e:
            self._handle_exception(f"fetch_open_trigger_orders für {symbol}", e)

    def cancel_trigger_order(self, order_id: str, symbol: str) -> Dict[str, Any]:
        try:
            return self.session.cancel_order(order_id, symbol, params={'stop': True})
        except Exception as e:
            self._handle_exception(f"cancel_trigger_order {order_id} für {symbol}", e)

    def fetch_open_positions(self, symbol: str) -> List[Dict[str, Any]]:
        try:
            positions = self.session.fetch_positions([symbol])
            return [p for p in positions if p and p.get('contracts') and float(p['contracts']) > 0]
        except Exception as e:
            self._handle_exception(f"fetch_open_positions für {symbol}", e)

    def flash_close_position(self, symbol: str) -> Dict[str, Any]:
        try:
            positions = self.fetch_open_positions(symbol)
            if not positions:
                raise Exception("Keine offene Position zum Schließen gefunden.")
            
            position_info = positions[0]
            amount = float(position_info['contracts'])
            side_to_close = 'buy' if position_info['side'] == 'short' else 'sell'
            return self.place_market_order(symbol, side_to_close, amount, reduce=True)
        except Exception as e:
            self._handle_exception(f"flash_close_position für {symbol}", e)
    
    def fetch_recent_ohlcv(self, symbol: str, timeframe: str, limit: int = 1000) -> pd.DataFrame:
        try:
            ohlcv = self.session.fetch_ohlcv(symbol, timeframe, limit=limit)
            df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms', utc=True)
            df.set_index('timestamp', inplace=True)
            return df.sort_index()
        except Exception as e:
            self._handle_exception(f"fetch_recent_ohlcv für {symbol}", e)

    def fetch_historical_ohlcv(self, symbol: str, timeframe: str, start_date: str, end_date: str) -> pd.DataFrame:
        try:
            since = self.session.parse8601(f"{start_date}T00:00:00Z")
            end_ts = self.session.parse8601(f"{end_date}T23:59:59Z")
            
            all_ohlcv = []
            while since < end_ts:
                ohlcv = self.session.fetch_ohlcv(symbol, timeframe, since, limit=1000)
                if not ohlcv: break
                all_ohlcv.extend(ohlcv)
                since = ohlcv[-1][0] + self.session.parse_timeframe(timeframe) * 1000
                time.sleep(self.session.rateLimit / 1000)

            if not all_ohlcv: return pd.DataFrame()

            df = pd.DataFrame(all_ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms', utc=True)
            df.drop_duplicates(subset='timestamp', inplace=True)
            df.set_index('timestamp', inplace=True)
            return df.loc[start_date:end_date].sort_index()
        except Exception as e:
            self._handle_exception(f"fetch_historical_ohlcv für {symbol}", e)

    def place_market_order(self, symbol: str, side: str, amount: float, reduce: bool = False) -> Dict[str, Any]:
        try:
            return self.session.create_order(symbol, 'market', side, amount, params={'reduceOnly': reduce})
        except Exception as e:
            self._handle_exception(f"place_market_order ({side}, {amount}) für {symbol}", e)

    def place_limit_order(self, symbol: str, side: str, amount: float, price: float, reduce: bool = False) -> Dict[str, Any]:
        try:
            return self.session.create_order(symbol, 'limit', side, amount, price, params={'reduceOnly': reduce})
        except Exception as e:
            self._handle_exception(f"place_limit_order ({side}, {amount}, {price}) für {symbol}", e)
            
    def place_trigger_market_order(self, symbol: str, side: str, amount: float, trigger_price: float, reduce: bool = False) -> Dict[str, Any]:
        try:
            return self.session.create_order(symbol, 'market', side, amount, params={'stopPrice': trigger_price, 'reduceOnly': reduce})
        except Exception as e:
            self._handle_exception(f"place_trigger_market_order bei {trigger_price} für {symbol}", e)
