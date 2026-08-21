import json
import logging
import os
import requests
import smtplib
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from flask import Flask, render_template, jsonify, request
from werkzeug.utils import secure_filename

import database

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Constants & Configurations
UPLOAD_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static', 'uploads')
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16 MB max limit
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'pdf'}

# Ensure uploads directory exists
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def add_one_day(date_str):
    """Adds 1 day to YYYY-MM-DD date string for FullCalendar exclusive end date."""
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        return (dt + timedelta(days=1)).strftime("%Y-%m-%d")
    except Exception as e:
        logger.error(f"Error adding one day to {date_str}: {e}")
        return date_str

def get_name_in_german(name_list):
    """Finds the German name in the OpenHolidays name list, defaults to first available."""
    if not name_list:
        return "Feiertag/Ferien"
    for name_entry in name_list:
        if name_entry.get('language') == 'DE':
            return name_entry.get('text')
    for name_entry in name_list:
        if name_entry.get('language') == 'EN':
            return name_entry.get('text')
    return name_list[0].get('text', 'Feiertag/Ferien')

def fetch_holidays_from_api(holiday_type, year):
    """Fetches school or public holidays from OpenHolidays API for a specific year."""
    url = f"https://openholidaysapi.org/{holiday_type}"
    params = {
        "countryIsoCode": "CH",
        "subdivisionCode": "CH-ZH",
        "validFrom": f"{year}-01-01",
        "validTo": f"{year}-12-31"
    }
    try:
        logger.info(f"Fetching {holiday_type} for {year} from API: {url} with {params}")
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        logger.error(f"API Error fetching {holiday_type} for {year}: {e}")
        return None

def get_holidays_for_years(years):
    """Gets and merges public and school holidays for the requested years, utilizing cache."""
    cache_key = f"holidays_{'_'.join(map(str, sorted(years)))}"
    
    # Check cache first
    cached_data_str, cached_time_str = database.get_cached_holidays(cache_key)
    use_cache = False
    
    if cached_data_str and cached_time_str:
        try:
            cached_time = datetime.strptime(cached_time_str, "%Y-%m-%d %H:%M:%S")
            if datetime.utcnow() - cached_time < timedelta(hours=24):
                use_cache = True
                logger.info("Using fresh cached holidays.")
        except Exception as e:
            logger.error(f"Error parsing cache time: {e}")
            
    if use_cache:
        return json.loads(cached_data_str)
        
    all_events = []
    
    for year in years:
        # 1. Public Holidays
        public_data = fetch_holidays_from_api("PublicHolidays", year)
        if public_data is None:
            if cached_data_str:
                logger.warning("API failed. Falling back to expired cache.")
                return json.loads(cached_data_str)
            public_data = []
            
        for h in public_data:
            name = get_name_in_german(h.get('name', []))
            all_events.append({
                'id': f"public_{h.get('id', name)}_{h.get('startDate')}",
                'title': name,
                'start': h.get('startDate'),
                'end': add_one_day(h.get('endDate')),
                'type': 'public_holiday',
                'color': '#fee2e2',  # light-red
                'textColor': '#991b1b',  # dark-red
                'borderColor': '#fca5a5'
            })
            
        # 2. School Holidays
        school_data = fetch_holidays_from_api("SchoolHolidays", year)
        if school_data is None:
            if cached_data_str:
                logger.warning("API failed. Falling back to expired cache.")
                return json.loads(cached_data_str)
            school_data = []
            
        seen_school_holidays = set()
        for s in school_data:
            name = get_name_in_german(s.get('name', []))
            start = s.get('startDate')
            end = s.get('endDate')
            dup_key = (start, end, name)
            if dup_key in seen_school_holidays:
                continue
            seen_school_holidays.add(dup_key)
            
            all_events.append({
                'id': f"school_{s.get('id', name)}_{start}",
                'title': name,
                'start': start,
                'end': add_one_day(end),
                'type': 'school_holiday',
                'color': '#dbeafe',  # light-blue
                'textColor': '#1e40af',  # dark-blue
                'borderColor': '#bfdbfe'
            })
            
    if all_events:
        try:
            database.set_cached_holidays(cache_key, json.dumps(all_events))
            logger.info("Successfully updated holiday cache.")
        except Exception as e:
            logger.error(f"Error saving to cache: {e}")
            
    return all_events

def get_family_emails():
    """Gets the active emails dictionary for the Benz family, including custom settings."""
    emails = {
        "Chiara Benz": "chiarabenz@gmx.net",
        "Alex Benz": "alex.benz@gmx.ch",
        "Seraina Benz": database.get_setting('seraina_email', 'seraina.benz@gmx.ch')
    }
    return emails

def get_smtp_config():
    """Gets the SMTP parameters from settings database with environment variables as fallbacks."""
    server = database.get_setting('smtp_server')
    port = database.get_setting('smtp_port')
    user = database.get_setting('smtp_user')
    password = database.get_setting('smtp_password')
    
    # Fallback
    if not server:
        server = os.environ.get('SMTP_SERVER')
    if not port:
        port = os.environ.get('SMTP_PORT', '587')
    if not user:
        user = os.environ.get('SMTP_USER')
    if not password:
        password = os.environ.get('SMTP_PASSWORD')
        
    return server, port, user, password

def format_date_swiss_py(date_str):
    if not date_str:
        return ""
    try:
        dt = datetime.strptime(date_str.split(' ')[0], "%Y-%m-%d")
        return dt.strftime("%d.%m.%Y")
    except Exception:
        return date_str

def send_email_notifications(sender_name, start_date, end_date, message_text):
    """Sends notification emails to all family members EXCEPT the sender."""
    emails = get_family_emails()
    recipients = {name: email for name, email in emails.items() if name != sender_name}
    
    start_swiss = format_date_swiss_py(start_date)
    end_swiss = format_date_swiss_py(end_date)
    
    subject = f"Neue Buchungsanfrage Ferienhaus Laax: {sender_name}"
    body = (
        f"Hallo,\n\n"
        f"{sender_name} hat eine Buchungsanfrage für das Ferienhaus in Laax eingetragen:\n\n"
        f"Zeitraum: {start_swiss} bis {end_swiss}\n"
        f"Notiz: {message_text or 'Keine Notiz'}\n\n"
        f"Das Buchungstool ist unter https://casa-benz-salums.ch erreichbar.\n\n"
        f"Bitte stimme dich bei Bedarf ab.\n"
    )
    
    # 1. Print visual log in console (Always done)
    logger.info("=" * 60)
    logger.info(f"EMAIL NOTIFICATION TRIGGERED BY: {sender_name}")
    logger.info(f"Recipients: {', '.join(recipients.values())}")
    logger.info(f"Subject: {subject}")
    logger.info(f"Body:\n{body}")
    logger.info("=" * 60)

    # 2. SMTP config
    smtp_server, smtp_port, smtp_user, smtp_password = get_smtp_config()
    
    if smtp_server and smtp_user and smtp_password:
        try:
            port = int(smtp_port)
        except ValueError:
            port = 587
            
        try:
            for recipient_name, recipient_email in recipients.items():
                msg = MIMEMultipart()
                msg['From'] = f"Casa Benz Salums <{smtp_user}>"
                msg['To'] = recipient_email
                msg['Subject'] = subject
                msg.attach(MIMEText(body, 'plain', 'utf-8'))
                
                if port == 465:
                    with smtplib.SMTP_SSL(smtp_server, port, timeout=10) as server:
                        server.login(smtp_user, smtp_password)
                        server.sendmail(smtp_user, recipient_email, msg.as_string())
                else:
                    with smtplib.SMTP(smtp_server, port, timeout=10) as server:
                        server.starttls()
                        server.login(smtp_user, smtp_password)
                        server.sendmail(smtp_user, recipient_email, msg.as_string())
            logger.info("SMTP emails sent successfully.")
        except Exception as e:
            logger.error(f"Failed to send SMTP emails: {e}")
    else:
        logger.info("SMTP configuration not complete. Emails logged to console only.")

def check_and_auto_approve():
    """Finds pending bookings that are older than 10 days and auto-approves them."""
    try:
        bookings = database.get_all_bookings()
        utcnow = datetime.utcnow()
        
        for b in bookings:
            if b['status'] == 'pending':
                created_at_str = b['created_at']
                try:
                    created_dt = datetime.strptime(created_at_str, "%Y-%m-%d %H:%M:%S")
                except Exception:
                    continue
                
                if utcnow - created_dt > timedelta(days=10):
                    database.approve_booking(b['id'])
                    logger.info(f"Auto-approved booking ID {b['id']} (guest: {b['guest_name']}) created on {created_at_str} (> 10 days ago).")
    except Exception as e:
        logger.error(f"Error in auto-approve execution: {e}")

def get_historical_bookings_for_period(start_date_str, end_date_str):
    """Finds approved bookings in previous years that overlap with the same day/month range."""
    try:
        start_dt = datetime.strptime(start_date_str, "%Y-%m-%d")
        end_dt = datetime.strptime(end_date_str, "%Y-%m-%d")
    except ValueError:
        return []
        
    target_year = start_dt.year
    bookings = database.get_all_bookings()
    
    historical = []
    for b in bookings:
        if b['status'] != 'approved':
            continue
            
        b_start = datetime.strptime(b['start_date'], "%Y-%m-%d")
        b_end = datetime.strptime(b['end_date'], "%Y-%m-%d")
        
        if b_start.year == target_year:
            continue
            
        try:
            proj_start = datetime(target_year, b_start.month, b_start.day)
        except ValueError:
            proj_start = datetime(target_year, b_start.month, 28)
            
        try:
            proj_end = datetime(target_year, b_end.month, b_end.day)
        except ValueError:
            proj_end = datetime(target_year, b_end.month, 28)
            
        if proj_end < proj_start:
            proj_end = proj_end.replace(year=target_year + 1)
            
        if proj_start < end_dt and proj_end > start_dt:
            years_ago = target_year - b_start.year
            historical.append({
                'guest_name': b['guest_name'],
                'start_date': b['start_date'],
                'end_date': b['end_date'],
                'year': b_start.year,
                'years_ago': years_ago
            })
            
    return historical

@app.route('/')
def index():
    check_and_auto_approve()
    return render_template('index.html')

@app.route('/api/login', methods=['POST'])
def api_login():
    data = request.json or request.form
    username = data.get('username')
    pin = data.get('pin')
    
    if not username or not pin:
        return jsonify({'success': False, 'message': 'Name und PIN erforderlich.'}), 400
        
    pin_key = None
    if username == "Chiara Benz":
        pin_key = "pin_chiara"
    elif username == "Seraina Benz":
        pin_key = "pin_seraina"
    elif username == "Alex Benz":
        pin_key = "pin_alex"
    elif username == "Admin":
        pin_key = "pin_admin"
        
    if not pin_key:
        return jsonify({'success': False, 'message': 'Ungültiger Benutzer.'}), 400
        
    stored_pin = database.get_setting(pin_key)
    # Default fallbacks if settings DB is empty
    if not stored_pin:
        if username == "Chiara Benz": stored_pin = "1111"
        elif username == "Seraina Benz": stored_pin = "2222"
        elif username == "Alex Benz": stored_pin = "3333"
        elif username == "Admin": stored_pin = "1234"
        
    if str(pin) == str(stored_pin):
        role = 'admin' if username == 'Admin' else 'family'
        return jsonify({
            'success': True,
            'message': f'Erfolgreich als {username} angemeldet.',
            'username': username,
            'role': role
        })
    else:
        return jsonify({'success': False, 'message': 'Falsche PIN.'}), 401

@app.route('/api/holidays', methods=['GET'])
def api_holidays():
    current_year = datetime.now().year
    years = [current_year - 1, current_year, current_year + 1]
    try:
        events = get_holidays_for_years(years)
        return jsonify(events)
    except Exception as e:
        logger.error(f"Error getting holidays: {e}")
        return jsonify([]), 500

@app.route('/api/bookings', methods=['GET', 'POST'])
def api_bookings():
    check_and_auto_approve()
    
    if request.method == 'GET':
        bookings = database.get_all_bookings()
        events = []
        for b in bookings:
            if b['status'] == 'rejected':
                continue
            is_approved = b['status'] == 'approved'
            status_text = "Bestätigt" if is_approved else "Angefragt"
            
            title = f"{b['guest_name']} ({status_text})"
            end_date_exclusive = add_one_day(b['end_date'])
            
            events.append({
                'id': f"booking_{b['id']}",
                'title': title,
                'start': b['start_date'],
                'end': end_date_exclusive,
                'type': 'booking',
                'status': b['status'],
                'color': '#dcfce7' if is_approved else '#ffedd5',
                'textColor': '#166534' if is_approved else '#9a3412',
                'borderColor': '#bbf7d0' if is_approved else '#fed7aa',
                'extendedProps': {
                    'guest_name': b['guest_name'],
                    'guest_email': b['guest_email'],
                    'guest_phone': b['guest_phone'],
                    'message': b['message'],
                    'contact_person': b['contact_person'],
                    'status': b['status'],
                    'start_display': b['start_date'],
                    'end_display': b['end_date'],
                    'created_at': b['created_at'],
                    'id_raw': b['id']
                }
            })
        return jsonify(events)
        
    elif request.method == 'POST':
        data = request.json or request.form
        
        guest_name = data.get('guest_name')
        emails = get_family_emails()
        guest_email = emails.get(guest_name, '')
        guest_phone = data.get('guest_phone', '')
        start_date = data.get('start_date')
        end_date = data.get('end_date')
        message = data.get('message', '')
        contact_person = data.get('contact_person', 'Absprache in der Familie')
        
        if not all([guest_name, start_date, end_date]):
            return jsonify({'success': False, 'message': 'Bitte alle erforderlichen Felder ausfüllen.'}), 400
            
        try:
            start_dt = datetime.strptime(start_date, "%Y-%m-%d")
            end_dt = datetime.strptime(end_date, "%Y-%m-%d")
            if start_dt >= end_dt:
                return jsonify({'success': False, 'message': 'Das Abreisedatum muss nach dem Anreisedatum liegen.'}), 400
        except ValueError:
            return jsonify({'success': False, 'message': 'Ungültiges Datumsformat.'}), 400
            
        booking_id = database.add_booking(
            guest_name=guest_name,
            guest_email=guest_email,
            guest_phone=guest_phone,
            start_date=start_date,
            end_date=end_date,
            message=message,
            contact_person=contact_person
        )
        
        send_email_notifications(guest_name, start_date, end_date, message)
        
        return jsonify({
            'success': True,
            'message': 'Buchung erfolgreich eingetragen. Die Benachrichtigungen wurden versendet.',
            'booking_id': booking_id
        })

@app.route('/api/bookings/history', methods=['GET'])
def api_bookings_history():
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')
    if not start_date or not end_date:
        return jsonify({'success': False, 'message': 'Dates missing.'}), 400
        
    history = get_historical_bookings_for_period(start_date, end_date)
    return jsonify(history)

@app.route('/api/bookings/<int:booking_id>/approve', methods=['POST'])
def api_approve_booking(booking_id):
    req_data = request.get_json(silent=True) or {}
    pin = request.headers.get('Admin-PIN') or req_data.get('pin')
    current_user = request.headers.get('Current-User') or req_data.get('currentUser')
    
    # 1. Master admin override
    if pin == '1234':
        success = database.approve_booking(booking_id)
        if success:
            return jsonify({'success': True, 'message': 'Eintrag erfolgreich freigegeben (Admin).'})
        return jsonify({'success': False, 'message': 'Eintrag nicht gefunden.'}), 404
        
    # 2. Family user check (prevent self-approval)
    if not current_user:
        return jsonify({'success': False, 'message': 'Kein berechtigter Benutzer angemeldet.'}), 403
        
    bookings = database.get_all_bookings()
    booking = next((b for b in bookings if b['id'] == booking_id), None)
    if not booking:
        return jsonify({'success': False, 'message': 'Eintrag nicht gefunden.'}), 404
        
    if booking['guest_name'] == current_user:
        return jsonify({
            'success': False,
            'message': 'Du kannst deine eigene Buchung nicht selbst bestätigen. Das müssen die anderen beiden Familienmitglieder tun!'
        }), 403
        
    success = database.approve_booking(booking_id)
    if success:
        return jsonify({'success': True, 'message': f'Eintrag freigegeben durch {current_user}.'})
    return jsonify({'success': False, 'message': 'Datenbankfehler.'}), 500

@app.route('/api/bookings/<int:booking_id>/reject', methods=['POST'])
def api_reject_booking(booking_id):
    req_data = request.get_json(silent=True) or {}
    pin = request.headers.get('Admin-PIN') or req_data.get('pin')
    current_user = request.headers.get('Current-User') or req_data.get('currentUser')
    
    # 1. Master admin override
    if pin == '1234':
        success = database.reject_booking(booking_id)
        if success:
            return jsonify({'success': True, 'message': 'Eintrag erfolgreich abgelehnt (Admin).'})
        return jsonify({'success': False, 'message': 'Eintrag nicht gefunden.'}), 404
        
    # 2. Family user check (users CAN reject or cancel their own bookings, and can also reject conflicts)
    if not current_user:
        return jsonify({'success': False, 'message': 'Kein berechtigter Benutzer angemeldet.'}), 403
        
    success = database.reject_booking(booking_id)
    if success:
        return jsonify({'success': True, 'message': f'Eintrag erfolgreich abgelehnt von {current_user}.'})
    return jsonify({'success': False, 'message': 'Eintrag nicht gefunden.'}), 404

@app.route('/api/bookings/<int:booking_id>/delete', methods=['POST'])
def api_delete_booking(booking_id):
    req_data = request.get_json(silent=True) or {}
    pin = request.headers.get('Admin-PIN') or req_data.get('pin')
    
    if pin != '1234':
        return jsonify({'success': False, 'message': 'Nur der Master Admin darf Einträge löschen.'}), 403
        
    success = database.delete_booking(booking_id)
    if success:
        return jsonify({'success': True, 'message': 'Buchungsanfrage erfolgreich aus der Datenbank gelöscht.'})
    return jsonify({'success': False, 'message': 'Eintrag nicht gefunden.'}), 404

@app.route('/api/bookings/clear-test-data', methods=['POST'])
def api_clear_test_data():
    req_data = request.get_json(silent=True) or {}
    pin = request.headers.get('Admin-PIN') or req_data.get('pin')
    if pin != '1234':
        return jsonify({'success': False, 'message': 'Ungültige Admin-PIN.'}), 403
        
    database.clear_all_bookings()
    return jsonify({'success': True, 'message': 'Alle Belegungsdaten wurden gelöscht.'})

# Defect List API
@app.route('/api/defects', methods=['GET', 'POST'])
def api_defects():
    if request.method == 'GET':
        defects = database.get_all_defects()
        return jsonify(defects)
        
    elif request.method == 'POST':
        description = request.form.get('description')
        reported_by = request.form.get('reported_by')
        
        if not description or not reported_by:
            return jsonify({'success': False, 'message': 'Beschreibung und Name sind erforderlich.'}), 400
            
        image_path = None
        if 'image' in request.files:
            file = request.files['image']
            if file and file.filename != '' and allowed_file(file.filename):
                filename = secure_filename(file.filename)
                unique_filename = f"defect_{int(datetime.now().timestamp())}_{filename}"
                file.save(os.path.join(app.config['UPLOAD_FOLDER'], unique_filename))
                image_path = f"/static/uploads/{unique_filename}"
                
        defect_id = database.add_defect(
            description=description,
            image_path=image_path,
            reported_by=reported_by
        )
        return jsonify({'success': True, 'message': 'Mangel erfolgreich gemeldet.', 'defect_id': defect_id})

@app.route('/api/defects/<int:defect_id>/resolve', methods=['POST'])
def api_resolve_defect(defect_id):
    success = database.resolve_defect(defect_id)
    if success:
        return jsonify({'success': True, 'message': 'Mangel als behoben markiert.'})
    return jsonify({'success': False, 'message': 'Mangel nicht gefunden.'}), 404

# Expense List API
@app.route('/api/expenses', methods=['GET', 'POST'])
def api_expenses():
    if request.method == 'GET':
        expenses = database.get_all_expenses()
        return jsonify(expenses)
        
    elif request.method == 'POST':
        item_description = request.form.get('item_description')
        amount_str = request.form.get('amount')
        paid_by = request.form.get('paid_by')
        
        if not item_description or not amount_str or not paid_by:
            return jsonify({'success': False, 'message': 'Gegenstand, Betrag und Name sind erforderlich.'}), 400
            
        try:
            amount = float(amount_str)
        except ValueError:
            return jsonify({'success': False, 'message': 'Ungültiger Betrag.'}), 400
            
        receipt_path = None
        if 'receipt' in request.files:
            file = request.files['receipt']
            if file and file.filename != '' and allowed_file(file.filename):
                filename = secure_filename(file.filename)
                unique_filename = f"receipt_{int(datetime.now().timestamp())}_{filename}"
                file.save(os.path.join(app.config['UPLOAD_FOLDER'], unique_filename))
                receipt_path = f"/static/uploads/{unique_filename}"
                
        expense_id = database.add_expense(
            item_description=item_description,
            amount=amount,
            paid_by=paid_by,
            receipt_path=receipt_path
        )
        return jsonify({'success': True, 'message': 'Ausgabe erfolgreich hinzugefügt.', 'expense_id': expense_id})

@app.route('/api/expenses/<int:expense_id>/delete', methods=['POST'])
def api_delete_expense(expense_id):
    pin = request.headers.get('Admin-PIN')
    if not pin and request.is_json:
        pin = request.json.get('pin')
    if not pin:
        pin = request.form.get('pin')
        
    if pin != '1234':
        return jsonify({'success': False, 'message': 'Ungültige Admin-PIN.'}), 403
        
    success = database.delete_expense(expense_id)
    if success:
        return jsonify({'success': True, 'message': 'Ausgabe gelöscht.'})
    return jsonify({'success': False, 'message': 'Ausgabe nicht gefunden.'}), 404

# Settings API (SMTP and Seraina email config)
@app.route('/api/settings', methods=['GET', 'POST'])
def api_settings():
    pin = request.headers.get('Admin-PIN') or request.args.get('pin')
    if not pin and request.is_json:
        pin = request.json.get('pin')
    if not pin:
        pin = request.form.get('pin')
        
    if pin != '1234':
        return jsonify({'success': False, 'message': 'Ungültige Admin-PIN.'}), 403
        
    if request.method == 'GET':
        return jsonify({
            'smtp_server': database.get_setting('smtp_server', ''),
            'smtp_port': database.get_setting('smtp_port', '587'),
            'smtp_user': database.get_setting('smtp_user', ''),
            'smtp_password': '*****' if database.get_setting('smtp_password') else '',
            'seraina_email': database.get_setting('seraina_email', 'seraina.benz@gmx.ch'),
            'pin_chiara': database.get_setting('pin_chiara', '1111'),
            'pin_seraina': database.get_setting('pin_seraina', '2222'),
            'pin_alex': database.get_setting('pin_alex', '3333'),
            'pin_admin': database.get_setting('pin_admin', '1234')
        })
    else:
        data = request.json or request.form
        database.set_setting('smtp_server', data.get('smtp_server', ''))
        database.set_setting('smtp_port', data.get('smtp_port', '587'))
        database.set_setting('smtp_user', data.get('smtp_user', ''))
        
        pwd = data.get('smtp_password', '')
        if pwd and pwd != '*****':
            database.set_setting('smtp_password', pwd)
            
        database.set_setting('seraina_email', data.get('seraina_email', 'seraina.benz@gmx.ch'))
        
        # User PINs
        if 'pin_chiara' in data: database.set_setting('pin_chiara', data.get('pin_chiara', '1111'))
        if 'pin_seraina' in data: database.set_setting('pin_seraina', data.get('pin_seraina', '2222'))
        if 'pin_alex' in data: database.set_setting('pin_alex', data.get('pin_alex', '3333'))
        if 'pin_admin' in data: database.set_setting('pin_admin', data.get('pin_admin', '1234'))
        
        return jsonify({'success': True, 'message': 'Einstellungen gespeichert.'})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5001, debug=True)
