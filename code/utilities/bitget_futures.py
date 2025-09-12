# code/utilities/bitget_futures.py

import ccxt
import pandas as pd
import logging
from typing import Any, Optional, Dict, List

logger = logging.getLogger(__name__)

class BitgetFutures():
    def __init__(self, api_setup: Optional[Dict[str, Any]] = None) -> None:
        api_setup = api_setup or {}
        api_setup.setdefault("options", {"defaultType": "swap"})
        self.session = ccxt.bitget(api_setup)
        self.markets = self.session.load_markets()
     
    def get_market_info(self, symbol: str) -> Dict[str, Any]:
        if symbol not in self.markets:
            self.markets = self.session.load_markets(True)
        return self.markets.get(symbol, {})

    def fetch_ticker(self, symbol: str) -> Dict[str, Any]:
        try:
            return self.session.fetch_ticker(symbol)
        except Exception as e:
            raise Exception(f"Failed to fetch ticker for {symbol}: {e}")
         
    def fetch_balance(self) -> Dict[str, Any]:
        try:
            return self.session.fetch_balance()
        except Exception as e:
            raise Exception(f"Failed to fetch balance: {e}")

    def fetch_open_positions(self, symbol: str) -> List[Dict[str, Any]]:
        try:
            positions = self.session.fetch_positions([symbol])
            return [p for p in positions if p.get('contracts') and float(p['contracts']) > 0]
        except Exception as e:
            raise Exception(f"Failed to fetch open positions: {e}")
            
    def fetch_recent_ohlcv(self, symbol: str, timeframe: str, limit: int = 500) -> pd.DataFrame:
        try:
            ohlcv = self.session.fetch_ohlcv(symbol, timeframe, limit=limit)
            df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms', utc=True)
            df.set_index('timestamp', inplace=True)
            return df.sort_index()
        except Exception as e:
            raise Exception(f"Failed to fetch ohlcv data for {symbol}: {e}")

    def cancel_all_orders(self, symbol: str):
        try:
            self.session.cancel_all_orders(symbol)
        except Exception as e:
            logger.warning(f"Konnte nicht alle Limit-Orders stornieren: {e}")
    
    def cancel_all_trigger_orders(self, symbol: str):
        try:
            self.session.cancel_all_orders(symbol, params={'planType': 'normal_plan'})
            self.session.cancel_all_orders(symbol, params={'planType': 'track_plan'})
        except Exception as e:
            logger.warning(f"Konnte nicht alle Trigger-Orders stornieren: {e}")

    def create_market_order(self, symbol: str, side: str, amount: float, params: dict = None) -> Dict[str, Any]:
        try:
            return self.session.create_order(symbol, 'market', side, amount, None, params)
        except Exception as e:
            raise Exception(f"Failed to create market order: {e}")

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
        except Exception as e:
            raise Exception(f"Failed to place trigger market order: {e}")

    def place_trailing_stop_order(self, symbol: str, side: str, amount: float, callback_rate: float, activation_price: float, params: dict = None) -> Dict[str, Any]:
        try:
            order_params = {
                'planType': 'track_plan', 'triggerPrice': activation_price,
                'callbackRate': str(callback_rate / 100),
            }
            if params:
                order_params.update(params)
            return self.session.create_order(symbol, 'market', side, amount, None, params=order_params)
        except Exception as e:
            raise Exception(f"Failed to place trailing stop order: {e}")
