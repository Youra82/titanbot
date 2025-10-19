# src/jaegerbot/utils/exchange.py
import ccxt
import pandas as pd
from datetime import datetime, timezone

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
        })
        self.markets = self.exchange.load_markets()

    def fetch_recent_ohlcv(self, symbol, timeframe, limit=100):
        since = None
        data = self.exchange.fetch_ohlcv(symbol, timeframe, since, limit)
        df = pd.DataFrame(data, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms', utc=True)
        df.set_index('timestamp', inplace=True)
        df.sort_index(inplace=True)
        return df

    def fetch_historical_ohlcv(self, symbol, timeframe, start_date_str, end_date_str):
        start_ts = int(self.exchange.parse8601(start_date_str + 'T00:00:00Z'))
        end_ts = int(self.exchange.parse8601(end_date_str + 'T00:00:00Z'))
        all_ohlcv = []
        
        while start_ts < end_ts:
            ohlcv = self.exchange.fetch_ohlcv(symbol, timeframe, since=start_ts, limit=1000)
            if not ohlcv:
                break
            all_ohlcv.extend(ohlcv)
            start_ts = ohlcv[-1][0] + 1
        
        if not all_ohlcv:
            return pd.DataFrame()
            
        df = pd.DataFrame(all_ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms', utc=True)
        df.set_index('timestamp', inplace=True)
        return df[~df.index.duplicated(keep='first')].sort_index()

    def fetch_ticker(self, symbol):
        return self.exchange.fetch_ticker(symbol)

    def set_margin_mode(self, symbol, mode='isolated'):
        try:
            self.exchange.set_margin_mode(mode, symbol)
        except Exception as e:
            if 'Margin mode is the same' not in str(e): print(f"Warnung: Margin-Modus konnte nicht gesetzt werden: {e}")

    def set_leverage(self, symbol, level=10):
        try:
            self.exchange.set_leverage(level, symbol)
        except Exception as e:
            if 'Leverage not changed' not in str(e): print(f"Warnung: Set leverage failed: {e}")

    def create_market_order(self, symbol, side, amount, params={}):
        return self.exchange.create_order(symbol, 'market', side, amount, params=params)
    
    def place_trigger_market_order(self, symbol, side, amount, trigger_price, params={}):
        rounded_price = float(self.exchange.price_to_precision(symbol, trigger_price))
        order_params = {
            'triggerPrice': rounded_price,
            'reduceOnly': params.get('reduceOnly', False)
        }
        return self.exchange.create_order(symbol, 'market', side, amount, params=order_params)

    def fetch_open_positions(self, symbol):
        positions = self.exchange.fetch_positions([symbol])
        open_positions = [p for p in positions if p.get('contracts', 0.0) > 0.0]
        return open_positions

    def fetch_open_trigger_orders(self, symbol):
        return self.exchange.fetch_open_orders(symbol, params={'stop': True})

    def fetch_balance_usdt(self):
        try:
            balance = self.exchange.fetch_balance()
            if 'USDT' in balance:
                return balance['USDT']['free']
            elif 'total' in balance and 'USDT' in balance['total']:
                 return balance['total']['USDT']
            else:
                return 0
        except Exception as e:
            print(f"Fehler beim Abrufen des Kontostandes: {e}")
            return 0
            
    def cleanup_all_open_orders(self, symbol):
        cancelled_count = 0
        try:
            trigger_orders = self.exchange.fetch_open_orders(symbol, params={'stop': True})
            if trigger_orders:
                for order in trigger_orders:
                    try:
                        self.exchange.cancel_order(order['id'], symbol, params={'stop': True})
                        cancelled_count += 1
                    except ccxt.OrderNotFound:
                        pass 
                    except Exception as e:
                        print(f"Konnte Trigger-Order {order['id']} nicht stornieren: {e}")
        except Exception as e:
            print(f"Fehler beim Abrufen von Trigger-Orders: {e}")
        try:
            normal_orders = self.exchange.fetch_open_orders(symbol, params={'stop': False})
            if normal_orders:
                for order in normal_orders:
                    try:
                        self.exchange.cancel_order(order['id'], symbol)
                        cancelled_count += 1
                    except ccxt.OrderNotFound:
                        pass
                    except Exception as e:
                        print(f"Konnte normale Order {order['id']} nicht stornieren: {e}")
        except Exception as e:
            print(f"Fehler beim Abrufen normaler Orders: {e}")
            
        return cancelled_count
