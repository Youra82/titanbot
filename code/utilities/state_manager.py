# code/utilities/state_manager.py
import sqlite3
import os
import json

class StateManager:
    def __init__(self, db_path):
        self.db_path = db_path
        self._initialize_db()

    def _initialize_db(self):
        """Stellt sicher, dass die Datenbank und die Tabelle existieren."""
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
        cursor.execute("INSERT OR IGNORE INTO state (key, value) VALUES (?, ?)", 
                       ('trade_status', json.dumps({
                           "status": "ok_to_trade", 
                           "last_side": None, 
                           "stop_loss_ids": []
                       })))
        conn.commit()
        conn.close()

    def get_state(self):
        """Liest den aktuellen Handelsstatus aus der Datenbank."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM state WHERE key = 'trade_status'")
        result = cursor.fetchone()
        conn.close()
        if result:
            return json.loads(result[0])
        return None

    def set_state(self, status, last_side=None, stop_loss_ids=None):
        """
        Aktualisiert den Handelsstatus in der Datenbank.
        stop_loss_ids sollte immer eine Liste sein.
        """
        if stop_loss_ids is None:
            stop_loss_ids = []
        
        new_state = {
            "status": status,
            "last_side": last_side,
            "stop_loss_ids": stop_loss_ids
        }
        
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("UPDATE state SET value = ? WHERE key = 'trade_status'", 
                       (json.dumps(new_state),))
        conn.commit()
        conn.close()
