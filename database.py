import sqlite3
import os

# Import psycopg2 if available (for PostgreSQL support on Render)
try:
    import psycopg2
    import psycopg2.extras
except ImportError:
    psycopg2 = None

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'bookings.db')

def get_db_type():
    db_url = os.environ.get('DATABASE_URL')
    if db_url and (db_url.startswith("postgres://") or db_url.startswith("postgresql://")):
        return 'postgres'
    return 'sqlite'

def get_db_connection():
    db_type = get_db_type()
    if db_type == 'postgres':
        if not psycopg2:
            raise ImportError("psycopg2 is not installed but DATABASE_URL is set for PostgreSQL.")
        db_url = os.environ.get('DATABASE_URL')
        if db_url.startswith("postgres://"):
            db_url = db_url.replace("postgres://", "postgresql://", 1)
        conn = psycopg2.connect(db_url)
        return conn
    else:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        return conn

def get_cursor(conn, db_type):
    if db_type == 'postgres':
        return conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    return conn.cursor()

def init_db():
    db_type = get_db_type()
    conn = get_db_connection()
    cursor = get_cursor(conn, db_type)
    
    if db_type == 'postgres':
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS bookings (
                id SERIAL PRIMARY KEY,
                guest_name VARCHAR(255) NOT NULL,
                guest_email VARCHAR(255) NOT NULL,
                guest_phone VARCHAR(50),
                start_date VARCHAR(20) NOT NULL,
                end_date VARCHAR(20) NOT NULL,
                message TEXT,
                contact_person VARCHAR(255) NOT NULL,
                status VARCHAR(50) DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS holiday_cache (
                key VARCHAR(255) PRIMARY KEY,
                data TEXT NOT NULL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS defects (
                id SERIAL PRIMARY KEY,
                description TEXT NOT NULL,
                image_path TEXT,
                status VARCHAR(50) DEFAULT 'open',
                reported_by VARCHAR(255) NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS expenses (
                id SERIAL PRIMARY KEY,
                item_description VARCHAR(255) NOT NULL,
                amount DOUBLE PRECISION NOT NULL,
                paid_by VARCHAR(255) NOT NULL,
                receipt_path TEXT,
                shared_by TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS settings (
                key VARCHAR(255) PRIMARY KEY,
                value TEXT NOT NULL
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS shopping_list (
                id SERIAL PRIMARY KEY,
                item_name VARCHAR(255) NOT NULL,
                quantity VARCHAR(100),
                status VARCHAR(50) DEFAULT 'pending',
                added_by VARCHAR(255) NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
    else:
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS bookings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guest_name TEXT NOT NULL,
                guest_email TEXT NOT NULL,
                guest_phone TEXT,
                start_date TEXT NOT NULL,
                end_date TEXT NOT NULL,
                message TEXT,
                contact_person TEXT NOT NULL,
                status TEXT DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS holiday_cache (
                key TEXT PRIMARY KEY,
                data TEXT NOT NULL,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS defects (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                description TEXT NOT NULL,
                image_path TEXT,
                status TEXT DEFAULT 'open',
                reported_by TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS expenses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                item_description TEXT NOT NULL,
                amount REAL NOT NULL,
                paid_by TEXT NOT NULL,
                receipt_path TEXT,
                shared_by TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS shopping_list (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                item_name TEXT NOT NULL,
                quantity TEXT,
                status TEXT DEFAULT 'pending',
                added_by TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
    
    # Commit table creations first so they are saved
    conn.commit()
    
    # Run migration to add shared_by column to existing DB
    try:
        cursor.execute("ALTER TABLE expenses ADD COLUMN shared_by TEXT")
        conn.commit()
    except Exception:
        conn.rollback()
    
    # Seed default pins if settings is empty
    cursor.execute("SELECT COUNT(*) as count FROM settings")
    row = cursor.fetchone()
    if dict(row)['count'] == 0:
        if db_type == 'postgres':
            cursor.execute("INSERT INTO settings (key, value) VALUES ('pin_chiara', '1111')")
            cursor.execute("INSERT INTO settings (key, value) VALUES ('pin_seraina', '2222')")
            cursor.execute("INSERT INTO settings (key, value) VALUES ('pin_alex', '3333')")
            cursor.execute("INSERT INTO settings (key, value) VALUES ('pin_admin', '1234')")
            cursor.execute("INSERT INTO settings (key, value) VALUES ('phone_chiara', '+41797483754')")
            cursor.execute("INSERT INTO settings (key, value) VALUES ('phone_alex', '+41798620712')")
            cursor.execute("INSERT INTO settings (key, value) VALUES ('wa_apikey_chiara', '7183815')")
            cursor.execute("INSERT INTO settings (key, value) VALUES ('wa_apikey_alex', '2161300')")
        else:
            cursor.execute("INSERT INTO settings (key, value) VALUES ('pin_chiara', '1111')")
            cursor.execute("INSERT INTO settings (key, value) VALUES ('pin_seraina', '2222')")
            cursor.execute("INSERT INTO settings (key, value) VALUES ('pin_alex', '3333')")
            cursor.execute("INSERT INTO settings (key, value) VALUES ('pin_admin', '1234')")
            cursor.execute("INSERT INTO settings (key, value) VALUES ('phone_chiara', '+41797483754')")
            cursor.execute("INSERT INTO settings (key, value) VALUES ('phone_alex', '+41798620712')")
            cursor.execute("INSERT INTO settings (key, value) VALUES ('wa_apikey_chiara', '7183815')")
            cursor.execute("INSERT INTO settings (key, value) VALUES ('wa_apikey_alex', '2161300')")
        conn.commit()

    # Ensure WhatsApp keys are always present (migration for existing DBs)
    wa_defaults = [
        ('phone_chiara',    '+41797483754'),
        ('phone_alex',      '+41798620712'),
        ('wa_apikey_chiara','7183815'),
        ('wa_apikey_alex',  '2161300'),
    ]
    for key, value in wa_defaults:
        try:
            if db_type == 'postgres':
                cursor.execute(
                    "INSERT INTO settings (key, value) VALUES (%s, %s) ON CONFLICT (key) DO NOTHING",
                    (key, value)
                )
            else:
                cursor.execute(
                    "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
                    (key, value)
                )
        except Exception:
            pass
    conn.commit()

    conn.close()

def add_booking(guest_name, guest_email, guest_phone, start_date, end_date, message, contact_person):
    db_type = get_db_type()
    conn = get_db_connection()
    cursor = get_cursor(conn, db_type)
    
    query = '''
        INSERT INTO bookings (guest_name, guest_email, guest_phone, start_date, end_date, message, contact_person)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    '''
    
    if db_type == 'postgres':
        query = query.replace('?', '%s') + " RETURNING id"
        cursor.execute(query, (guest_name, guest_email, guest_phone, start_date, end_date, message, contact_person))
        booking_id = cursor.fetchone()['id']
    else:
        cursor.execute(query, (guest_name, guest_email, guest_phone, start_date, end_date, message, contact_person))
        booking_id = cursor.lastrowid
        
    conn.commit()
    conn.close()
    return booking_id

def get_all_bookings():
    db_type = get_db_type()
    conn = get_db_connection()
    cursor = get_cursor(conn, db_type)
    
    cursor.execute('SELECT * FROM bookings ORDER BY start_date ASC')
    rows = cursor.fetchall()
    
    result = []
    for row in rows:
        d = dict(row)
        if 'created_at' in d and not isinstance(d['created_at'], str) and d['created_at'] is not None:
            d['created_at'] = d['created_at'].strftime("%Y-%m-%d %H:%M:%S")
        result.append(d)
        
    conn.close()
    return result

def approve_booking(booking_id):
    db_type = get_db_type()
    conn = get_db_connection()
    cursor = get_cursor(conn, db_type)
    
    query = "UPDATE bookings SET status = 'approved' WHERE id = ?"
    if db_type == 'postgres':
        query = query.replace('?', '%s')
        
    cursor.execute(query, (booking_id,))
    conn.commit()
    success = cursor.rowcount > 0
    conn.close()
    return success

def reject_booking(booking_id):
    db_type = get_db_type()
    conn = get_db_connection()
    cursor = get_cursor(conn, db_type)
    
    query = "UPDATE bookings SET status = 'rejected' WHERE id = ?"
    if db_type == 'postgres':
        query = query.replace('?', '%s')
        
    cursor.execute(query, (booking_id,))
    conn.commit()
    success = cursor.rowcount > 0
    conn.close()
    return success

def delete_booking(booking_id):
    db_type = get_db_type()
    conn = get_db_connection()
    cursor = get_cursor(conn, db_type)
    
    query = "DELETE FROM bookings WHERE id = ?"
    if db_type == 'postgres':
        query = query.replace('?', '%s')
        
    cursor.execute(query, (booking_id,))
    conn.commit()
    success = cursor.rowcount > 0
    conn.close()
    return success

def clear_all_bookings():
    db_type = get_db_type()
    conn = get_db_connection()
    cursor = get_cursor(conn, db_type)
    cursor.execute('DELETE FROM bookings')
    conn.commit()
    conn.close()

def get_cached_holidays(key):
    db_type = get_db_type()
    conn = get_db_connection()
    cursor = get_cursor(conn, db_type)
    
    query = 'SELECT data, updated_at FROM holiday_cache WHERE key = ?'
    if db_type == 'postgres':
        query = query.replace('?', '%s')
        
    cursor.execute(query, (key,))
    row = cursor.fetchone()
    
    result = None
    if row:
        d = dict(row)
        updated_at = d['updated_at']
        if not isinstance(updated_at, str) and updated_at is not None:
            updated_at = updated_at.strftime("%Y-%m-%d %H:%M:%S")
        result = (d['data'], updated_at)
        
    conn.close()
    return result if result else (None, None)

def set_cached_holidays(key, data):
    db_type = get_db_type()
    conn = get_db_connection()
    cursor = get_cursor(conn, db_type)
    
    if db_type == 'postgres':
        query = '''
            INSERT INTO holiday_cache (key, data, updated_at)
            VALUES (%s, %s, CURRENT_TIMESTAMP)
            ON CONFLICT (key) DO UPDATE SET data = EXCLUDED.data, updated_at = CURRENT_TIMESTAMP
        '''
        cursor.execute(query, (key, data))
    else:
        query = '''
            INSERT OR REPLACE INTO holiday_cache (key, data, updated_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
        '''
        cursor.execute(query, (key, data))
        
    conn.commit()
    conn.close()

# Defect tracking helpers
def add_defect(description, image_path, reported_by):
    db_type = get_db_type()
    conn = get_db_connection()
    cursor = get_cursor(conn, db_type)
    
    query = '''
        INSERT INTO defects (description, image_path, reported_by, status)
        VALUES (?, ?, ?, 'open')
    '''
    
    if db_type == 'postgres':
        query = query.replace('?', '%s') + " RETURNING id"
        cursor.execute(query, (description, image_path, reported_by))
        defect_id = cursor.fetchone()['id']
    else:
        cursor.execute(query, (description, image_path, reported_by))
        defect_id = cursor.lastrowid
        
    conn.commit()
    conn.close()
    return defect_id

def get_all_defects():
    db_type = get_db_type()
    conn = get_db_connection()
    cursor = get_cursor(conn, db_type)
    
    cursor.execute('SELECT * FROM defects ORDER BY created_at DESC')
    rows = cursor.fetchall()
    
    result = []
    for row in rows:
        d = dict(row)
        if 'created_at' in d and not isinstance(d['created_at'], str) and d['created_at'] is not None:
            d['created_at'] = d['created_at'].strftime("%Y-%m-%d %H:%M:%S")
        result.append(d)
        
    conn.close()
    return result

def resolve_defect(defect_id):
    db_type = get_db_type()
    conn = get_db_connection()
    cursor = get_cursor(conn, db_type)
    
    query = "UPDATE defects SET status = 'resolved' WHERE id = ?"
    if db_type == 'postgres':
        query = query.replace('?', '%s')
        
    cursor.execute(query, (defect_id,))
    conn.commit()
    success = cursor.rowcount > 0
    conn.close()
    return success

# Expense tracking helpers
def add_expense(item_description, amount, paid_by, receipt_path, shared_by):
    db_type = get_db_type()
    conn = get_db_connection()
    cursor = get_cursor(conn, db_type)
    
    query = '''
        INSERT INTO expenses (item_description, amount, paid_by, receipt_path, shared_by)
        VALUES (?, ?, ?, ?, ?)
    '''
    
    if db_type == 'postgres':
        query = query.replace('?', '%s') + " RETURNING id"
        cursor.execute(query, (item_description, amount, paid_by, receipt_path, shared_by))
        expense_id = cursor.fetchone()['id']
    else:
        cursor.execute(query, (item_description, amount, paid_by, receipt_path, shared_by))
        expense_id = cursor.lastrowid
        
    conn.commit()
    conn.close()
    return expense_id

def get_all_expenses():
    db_type = get_db_type()
    conn = get_db_connection()
    cursor = get_cursor(conn, db_type)
    
    cursor.execute('SELECT * FROM expenses ORDER BY created_at DESC')
    rows = cursor.fetchall()
    
    result = []
    for row in rows:
        d = dict(row)
        if 'created_at' in d and not isinstance(d['created_at'], str) and d['created_at'] is not None:
            d['created_at'] = d['created_at'].strftime("%Y-%m-%d %H:%M:%S")
        result.append(d)
        
    conn.close()
    return result

def delete_expense(expense_id):
    db_type = get_db_type()
    conn = get_db_connection()
    cursor = get_cursor(conn, db_type)
    
    query = "DELETE FROM expenses WHERE id = ?"
    if db_type == 'postgres':
        query = query.replace('?', '%s')
        
    cursor.execute(query, (expense_id,))
    conn.commit()
    success = cursor.rowcount > 0
    conn.close()
    return success

# Settings helpers
def get_setting(key, default=None):
    db_type = get_db_type()
    conn = get_db_connection()
    cursor = get_cursor(conn, db_type)
    
    query = "SELECT value FROM settings WHERE key = ?"
    if db_type == 'postgres':
        query = query.replace('?', '%s')
        
    cursor.execute(query, (key,))
    row = cursor.fetchone()
    conn.close()
    
    if row:
        return row['value']
        
    # Hardcoded defaults as requested by the user
    hardcoded_defaults = {
        'phone_chiara': '+41797483754',
        'wa_apikey_chiara': '7183815',
        'phone_alex': '+41798620712',
        'wa_apikey_alex': '2161300'
    }
    if key in hardcoded_defaults:
        return hardcoded_defaults[key]
        
    return default

def set_setting(key, value):
    db_type = get_db_type()
    conn = get_db_connection()
    cursor = get_cursor(conn, db_type)
    
    if db_type == 'postgres':
        query = '''
            INSERT INTO settings (key, value)
            VALUES (%s, %s)
            ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
        '''
        cursor.execute(query, (key, value))
    else:
        query = '''
            INSERT OR REPLACE INTO settings (key, value)
            VALUES (?, ?)
        '''
        cursor.execute(query, (key, value))
        
    conn.commit()
    conn.close()

# Shopping List Helpers
def add_shopping_item(item_name, quantity, added_by):
    db_type = get_db_type()
    conn = get_db_connection()
    cursor = get_cursor(conn, db_type)
    
    query = '''
        INSERT INTO shopping_list (item_name, quantity, added_by, status)
        VALUES (?, ?, ?, 'pending')
    '''
    
    if db_type == 'postgres':
        query = query.replace('?', '%s') + " RETURNING id"
        cursor.execute(query, (item_name, quantity, added_by))
        item_id = cursor.fetchone()['id']
    else:
        cursor.execute(query, (item_name, quantity, added_by))
        item_id = cursor.lastrowid
        
    conn.commit()
    conn.close()
    return item_id

def get_all_shopping_items():
    db_type = get_db_type()
    conn = get_db_connection()
    cursor = get_cursor(conn, db_type)
    
    cursor.execute('SELECT * FROM shopping_list ORDER BY status DESC, created_at DESC')
    rows = cursor.fetchall()
    
    result = []
    for row in rows:
        d = dict(row)
        if 'created_at' in d and not isinstance(d['created_at'], str) and d['created_at'] is not None:
            d['created_at'] = d['created_at'].strftime("%Y-%m-%d %H:%M:%S")
        result.append(d)
        
    conn.close()
    return result

def toggle_shopping_item(item_id, status):
    db_type = get_db_type()
    conn = get_db_connection()
    cursor = get_cursor(conn, db_type)
    
    query = "UPDATE shopping_list SET status = ? WHERE id = ?"
    if db_type == 'postgres':
        query = query.replace('?', '%s')
        
    cursor.execute(query, (status, item_id))
    conn.commit()
    success = cursor.rowcount > 0
    conn.close()
    return success

def delete_shopping_item(item_id):
    db_type = get_db_type()
    conn = get_db_connection()
    cursor = get_cursor(conn, db_type)
    
    query = "DELETE FROM shopping_list WHERE id = ?"
    if db_type == 'postgres':
        query = query.replace('?', '%s')
        
    cursor.execute(query, (item_id,))
    conn.commit()
    success = cursor.rowcount > 0
    conn.close()
    return success

def clear_completed_shopping_items():
    db_type = get_db_type()
    conn = get_db_connection()
    cursor = get_cursor(conn, db_type)
    
    query = "DELETE FROM shopping_list WHERE status = 'completed'"
    cursor.execute(query)
    conn.commit()
    success = cursor.rowcount > 0
    conn.close()
    return success

# Initialize database on import
init_db()
