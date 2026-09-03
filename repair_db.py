import sqlite3
import os

path = os.path.join(os.getcwd(), 'instance', 'hmis.db')
print('DB path:', path)
conn = sqlite3.connect(path)
cur = conn.cursor()
cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='patient'")
print('table exists:', cur.fetchone() is not None)
cur.execute('PRAGMA table_info(patient)')
columns = [row[1] for row in cur.fetchall()]
print('columns before:', columns)
if 'national_id' not in columns:
    cur.execute("ALTER TABLE patient ADD COLUMN national_id VARCHAR(20)")
    cur.execute("UPDATE patient SET national_id = COALESCE(patient_id, CAST(id AS TEXT)) WHERE national_id IS NULL OR national_id = ''")
    conn.commit()
    print('added national_id')
else:
    print('national_id already exists')
cur.execute('PRAGMA table_info(patient)')
print('columns after:', [row[1] for row in cur.fetchall()])
conn.close()
