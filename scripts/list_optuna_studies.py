import sqlite3
import sys
DB = 'artifacts/db/optuna_studies_smc.db'
con = sqlite3.connect(DB)
cur = con.cursor()
cur.execute('SELECT name FROM studies;')
rows = cur.fetchall()
for r in rows:
    print(r[0])
con.close()