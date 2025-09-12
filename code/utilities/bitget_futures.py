# code/utilities/bitget_futures.py

import ccxt
import time
import pandas as pd
import logging
from typing import Any, Optional, Dict, List

logger = logging.getLogger(__name__)

class BitgetFutures():
    def __init__(self, api_setup: Optional[Dict[str, Any]] = None) -> None:
        api_setup = api_setup or {}
        # KORREKTUR: 'future' wird zu 'swap' geändert, was bei ccxt gängiger ist
        api_setup.setdefault("options", {"defaultType": "swap"})
        self.session = ccxt.bitget(api_setup)
        self.markets = self.session.load_markets()
     
    # Die alten, unzuverlässigen Funktionen werden entfernt, da die Parameter jetzt direkt übergeben werden
    def set_margin_mode(self, symbol: str, margin_mode: str = 'isolated') -> None:
        try:
            self.session.set_margin_mode(margin_mode, symbol)
            logger.info(f"Margin-Modus für {symbol} auf '{margin_mode}' gesetzt.")
        except Exception as e:
            if 'repeat submit' in str(e) or 'Margin mode is the same' in str(e):
                logger.info(f"Margin-Modus für {symbol} ist bereits auf '{margin_mode}' gesetzt.")
            else:
                raise Exception(f"Fehler beim Setzen des Margin-Modus: {e}")

    def set_leverage(self, symbol: str, leverage: int) -> None:
        try:
            self.session.set_leverage(leverage, symbol)
            logger.info(f"Hebel für {symbol} auf {leverage}x gesetzt.")
        except Exception as e:
            if 'repeat submit' in str(e) or 'Leverage is the same' in str(e):
                logger.info(f"Hebel für {symbol} ist bereits auf {leverage}x gesetzt.")
            else:
                raise Exception(f"Fehler beim Setzen des Hebels: {e}")
                
    # Alle 'fetch'-Funktionen bleiben wie zuvor...
    def fetch_ticker(self, symbol: str) -> Dict[str, Any]:
        try: return self.session.fetch_ticker(symbol)
        except Exception as e: raise Exception(f"Failed to fetch ticker for {symbol}: {e}")

    def fetch_balance(self, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        try: return self.session.fetch_balance(params or {})
        except Exception as e: raise Exception(f"Failed to fetch balance: {e}")

    def fetch_open_orders(self, symbol: str) -> List[Dict[str, Any]]:
        try: return self.session.fetch_open_orders(symbol)
        except Exception as e: raise Exception(f"Failed to fetch open orders: {e}")

    def fetch_open_positions(self, symbol: str) -> List[Dict[str, Any]]:
        try:
            positions = self.session.fetch_positions([symbol])
            return [p for p in positions if p.get('contracts') and float(p['contracts']) > 0]
        except Exception as e: raise Exception(f"Failed to fetch open positions: {e}")

    def get_market_info(self, symbol: str) -> Dict[str, Any]:
        if symbol not in self.markets: self.markets = self.session.load_markets(True)
        return self.markets.get(symbol, {})

    def fetch_recent_ohlcv(self, symbol: str, timeframe: str, limit: int = 1000) -> pd.DataFrame:
        try:
            timeframe_in_ms = self.session.parse_timeframe(timeframe) * 1000
            since = self.session.milliseconds() - limit * timeframe_in_ms
            all_ohlcv = self.session.fetch_ohlcv(symbol, timeframe, since=since, limit=limit)
            df = pd.DataFrame(all_ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms', utc=True)
            df.set_index('timestamp', inplace=True)
            return df.sort_index()
        except Exception as e: raise Exception(f"Failed to fetch OHLCV data for {symbol}: {e}")

    def cancel_all_orders(self, symbol: str) -> None:
        try: self.session.cancel_all_orders(symbol)
        except Exception as e: logger.warning(f"Konnte nicht alle Limit-Orders stornieren: {e}")
    
    def cancel_all_trigger_orders(self, symbol: str) -> None:
        try: self.session.cancel_all_orders(symbol, params={'stop': True})
        except Exception as e: logger.warning(f"Konnte nicht alle Trigger-Orders stornieren: {e}")

    def cancel_order(self, id: str, symbol: str) -> Dict[str, Any]:
        try: return self.session.cancel_order(id, symbol)
        except Exception as e: raise Exception(f"Failed to cancel the {symbol} order {id}", e)

    # --- START DER NEUEN ORDER-LOGIK ---
    def create_market_order(self, symbol: str, side: str, amount: float, params: dict = None) -> Dict[str, Any]:
        try:
            return self.session.create_order(symbol, 'market', side, amount, None, params)
        except Exception as e:
            raise Exception(f"Failed to create market order: {e}")
    
    def create_market_buy_order_with_cost(self, symbol: str, cost: float, params: dict = None) -> Dict[str, Any]:
        try:
            return self.session.create_market_buy_order_with_cost(symbol, cost, params)
        except Exception as e:
            raise Exception(f"Failed to create market buy order with cost: {e}")

    def place_limit_order(self, symbol: str, side: str, amount: float, price: float, params: dict = None) -> Dict[str, Any]:
        try:
            return self.session.create_order(symbol, 'limit', side, amount, price, params)
        except Exception as e:
            raise Exception(f"Failed to place limit order: {e}")

    def place_trigger_market_order(self, symbol: str, side: str, amount: float, trigger_price: float, params: dict = None) -> Dict[str, Any]:
        try:
            order_params = {'stopPrice': trigger_price}
            if params:
                order_params.update(params)
            return self.session.create_order(symbol, 'market', side, amount, None, order_params)
        except Exception as err:
            raise err

    def place_trailing_stop_order(self, symbol: str, side: str, amount: float, callback_rate: float, activation_price: float, params: dict = None) -> Dict[str, Any]:
        try:
            order_params = {
                'planType': 'track_plan',
                'triggerPrice': activation_price,
                'callbackRate': str(callback_rate / 100),
            }
            if params:
                order_params.update(params)
            return self.session.create_order(symbol, 'market', side, amount, None, params=order_params)
        except Exception as e:
            raise Exception(f"Fehler beim Platzieren der Trailing-Stop-Order: {e}")
    # --- ENDE DER NEUEN ORDER-LOGIK ---
