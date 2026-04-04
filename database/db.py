import sqlite3

def init_db(db_path):
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # ── ASSETS ──────────────────────────────────────────────
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS assets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            filename TEXT NOT NULL,
            phash TEXT NOT NULL,
            dhash TEXT NOT NULL DEFAULT '',
            ahash TEXT NOT NULL DEFAULT '',
            watermark_file TEXT DEFAULT '',
            license_start DATE DEFAULT NULL,
            license_end DATE DEFAULT NULL,
            license_owner TEXT DEFAULT NULL,
            uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # Add columns if upgrading from old DB
    for col, definition in [
        ('dhash', 'TEXT NOT NULL DEFAULT ""'),
        ('ahash', 'TEXT NOT NULL DEFAULT ""'),
        ('watermark_file', 'TEXT DEFAULT ""'),
        ('license_start', 'DATE DEFAULT NULL'),
        ('license_end', 'DATE DEFAULT NULL'),
        ('license_owner', 'TEXT DEFAULT NULL'),
    ]:
        try:
            cursor.execute(f'ALTER TABLE assets ADD COLUMN {col} {definition}')
        except:
            pass

    # ── VIOLATIONS ──────────────────────────────────────────
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

    # ── EXCEPTIONS ──────────────────────────────────────────
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS exceptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            platform TEXT NOT NULL,
            account_id TEXT NOT NULL,
            account_name TEXT NOT NULL,
            exception_type TEXT DEFAULT 'ACCOUNT',
            reason TEXT,
            added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # ── SOCIAL POSTS ────────────────────────────────────────
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS social_posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            platform TEXT NOT NULL,
            post_id TEXT NOT NULL,
            account_id TEXT NOT NULL,
            account_name TEXT NOT NULL,
            post_url TEXT,
            media_url TEXT,
            caption TEXT,
            post_type TEXT DEFAULT 'IMAGE',
            scan_status TEXT DEFAULT 'PENDING',
            violation_found INTEGER DEFAULT 0,
            similarity REAL DEFAULT 0,
            scanned_at TIMESTAMP,
            detected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # ── MONITORED ACCOUNTS ──────────────────────────────────
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS monitored_accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            platform TEXT NOT NULL,
            account_id TEXT NOT NULL,
            account_name TEXT NOT NULL,
            account_type TEXT DEFAULT 'MONITOR',
            is_active INTEGER DEFAULT 1,
            added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # ── API KEYS (NEW) ──────────────────────────────────────
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS api_keys (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            api_key TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL DEFAULT 'Unnamed App',
            is_active INTEGER DEFAULT 1,
            total_requests INTEGER DEFAULT 0,
            last_used TIMESTAMP DEFAULT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # ── BATCH SCANS (NEW) ───────────────────────────────────
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS batch_scans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            batch_id TEXT NOT NULL,
            filename TEXT NOT NULL,
            status TEXT DEFAULT 'PENDING',
            highest_similarity REAL DEFAULT 0,
            matched_asset TEXT DEFAULT NULL,
            risk_level TEXT DEFAULT 'LOW',
            scanned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    conn.commit()
    conn.close()


def get_db(db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn
