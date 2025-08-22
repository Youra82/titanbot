import sqlite3
import os
import json

class StateManager:
    def __init__(self, db_path):
        self.db_path = db_path
        self._initialize_db()

    def _initialize_db(self):
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS state (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        ''')
        # Initialzustand setzen, falls nicht vorhanden
        initial_state = {
            "status": "ok_to_trade",
            "last_side": None,
            "stop_loss_order_id": None,
            "entry_price": 0.0,
            "position_amount": 0.0,
            "verlust_vortrag": 0.0,
            "consecutive_loss_count": 0
        }
        cursor.execute("INSERT OR IGNORE INTO state (key, value) VALUES (?, ?)",
                       ('trade_status', json.dumps(initial_state)))
        conn.commit()
        conn.close()

    def get_state(self):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM state WHERE key = 'trade_status'")
        result = cursor.fetchone()
        conn.close()
        if result:
            return json.loads(result[0])
        # Fallback auf einen sauberen Zustand
        return {
            "status": "ok_to_trade", "last_side": None, "stop_loss_order_id": None,
            "entry_price": 0.0, "position_amount": 0.0,
            "verlust_vortrag": 0.0, "consecutive_loss_count": 0
        }

    def set_state(self, **kwargs):
        """
        Aktualisiert den Zustand in der Datenbank.
        Beispiel: set_state(status="in_trade", verlust_vortrag=10.5)
        """
        current_state = self.get_state()
        current_state.update(kwargs)

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("UPDATE state SET value = ? WHERE key = 'trade_status'",
                       (json.dumps(current_state),))
        conn.commit()
        conn.close()

    def reset_trade_state(self):
        """Setzt den Handelsstatus zurück, behält aber das Verlust-Konto."""
        state = self.get_state()
        self.set_state(
            status="ok_to_trade",
            last_side=None,
            stop_loss_order_id=None,
            entry_price=0.0,
            position_amount=0.0,
            verlust_vortrag=state.get('verlust_vortrag', 0.0),
            consecutive_loss_count=state.get('consecutive_loss_count', 0)
        )
