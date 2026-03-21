import sqlite3

def init_db(db_path):
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS assets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            filename TEXT NOT NULL,
            phash TEXT NOT NULL,
            dhash TEXT NOT NULL DEFAULT '',
            ahash TEXT NOT NULL DEFAULT '',
            uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # Add dhash and ahash columns if they don't exist yet
    try:
        cursor.execute('ALTER TABLE assets ADD COLUMN dhash TEXT NOT NULL DEFAULT ""')
    except:
        pass
    try:
        cursor.execute('ALTER TABLE assets ADD COLUMN ahash TEXT NOT NULL DEFAULT ""')
    except:
        pass
    try:
        cursor.execute('ALTER TABLE assets ADD COLUMN watermark_file TEXT DEFAULT ""')
    except:
        pass

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS violations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            asset_id INTEGER,
            found_url TEXT,
            similarity REAL,
            screenshot TEXT,
            detected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (asset_id) REFERENCES assets(id)
        )
    ''')

    conn.commit()
    conn.close()

def get_db(db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn