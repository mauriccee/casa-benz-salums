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
        # Render and other providers often use postgres:// prefix, but psycopg2 requires postgresql://
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
        # PostgreSQL DDL
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
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
    else:
        # SQLite DDL
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
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
    
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
            d['created_at'] = d['created_at'].strftime("%Y-%m-%d %H-%M-%S")
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
def add_expense(item_description, amount, paid_by, receipt_path):
    db_type = get_db_type()
    conn = get_db_connection()
    cursor = get_cursor(conn, db_type)
    
    query = '''
        INSERT INTO expenses (item_description, amount, paid_by, receipt_path)
        VALUES (?, ?, ?, ?)
    '''
    
    if db_type == 'postgres':
        query = query.replace('?', '%s') + " RETURNING id"
        cursor.execute(query, (item_description, amount, paid_by, receipt_path))
        expense_id = cursor.fetchone()['id']
    else:
        cursor.execute(query, (item_description, amount, paid_by, receipt_path))
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

# Initialize database on import
init_db()
