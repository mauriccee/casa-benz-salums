import json
import logging
import os
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

# Family details
FAMILY_MEMBERS = {
    "Chiara Benz": "chiarabenz@gmx.net",
    "Alex Benz": "alex.benz@gmx.ch",
    "Seraina Benz": "seraina.benz@gmx.ch"  # Temporary placeholder email
}

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

# Import requests inside function or globally to avoid scope errors
import requests

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

def send_email_notifications(sender_name, start_date, end_date, message_text):
    """Sends notification emails to all family members EXCEPT the sender."""
    recipients = {name: email for name, email in FAMILY_MEMBERS.items() if name != sender_name}
    
    subject = f"Neue Buchungsanfrage Ferienhaus Laax: {sender_name}"
    body = (
        f"Hallo,\n\n"
        f"{sender_name} hat eine Buchungsanfrage für das Ferienhaus in Laax eingetragen:\n\n"
        f"Zeitraum: {start_date} bis {end_date}\n"
        f"Notiz: {message_text or 'Keine Notiz hinterlassen'}\n\n"
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

    # 2. SMTP config (can be configured via env vars, otherwise falls back gracefully)
    smtp_server = os.environ.get('SMTP_SERVER')
    smtp_port = int(os.environ.get('SMTP_PORT', '587'))
    smtp_user = os.environ.get('SMTP_USER')
    smtp_password = os.environ.get('SMTP_PASSWORD')
    
    if smtp_server and smtp_user and smtp_password:
        try:
            for recipient_name, recipient_email in recipients.items():
                msg = MIMEMultipart()
                msg['From'] = f"Casa Pendas Booking <{smtp_user}>"
                msg['To'] = recipient_email
                msg['Subject'] = subject
                msg.attach(MIMEText(body, 'plain', 'utf-8'))
                
                with smtplib.SMTP(smtp_server, smtp_port) as server:
                    server.starttls()
                    server.login(smtp_user, smtp_password)
                    server.sendmail(smtp_user, recipient_email, msg.as_string())
            logger.info("SMTP emails sent successfully.")
        except Exception as e:
            logger.error(f"Failed to send SMTP emails: {e}")
    else:
        logger.info("SMTP environment variables not configured. Emails were logged to stdout only.")

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
        # Check approved only
        if b['status'] != 'approved':
            continue
            
        b_start = datetime.strptime(b['start_date'], "%Y-%m-%d")
        b_end = datetime.strptime(b['end_date'], "%Y-%m-%d")
        
        # Must be in a different year
        if b_start.year == target_year:
            continue
            
        # Project booking dates onto the target year to compare months and days
        # Project start date
        try:
            proj_start = datetime(target_year, b_start.month, b_start.day)
        except ValueError:
            # Leap year adjustment (Feb 29 -> Feb 28)
            proj_start = datetime(target_year, b_start.month, 28)
            
        # Project end date
        try:
            proj_end = datetime(target_year, b_end.month, b_end.day)
        except ValueError:
            proj_end = datetime(target_year, b_end.month, 28)
            
        # If the booking crossed a year boundary, project end date to the next year
        if proj_end < proj_start:
            proj_end = proj_end.replace(year=target_year + 1)
            
        # Check if projected booking overlaps with target period
        # Overlap holds if: proj_start < end_dt and proj_end > start_dt
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
    return render_template('index.html')

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
    if request.method == 'GET':
        bookings = database.get_all_bookings()
        events = []
        for b in bookings:
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
                'color': '#dcfce7' if is_approved else '#ffedd5',  # green / orange
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
                    'end_display': b['end_date']
                }
            })
        return jsonify(events)
        
    elif request.method == 'POST':
        data = request.json or request.form
        
        guest_name = data.get('guest_name')
        guest_email = FAMILY_MEMBERS.get(guest_name, data.get('guest_email', ''))
        guest_phone = data.get('guest_phone', '')
        start_date = data.get('start_date')
        end_date = data.get('end_date')
        message = data.get('message', '')
        contact_person = data.get('contact_person')
        
        if not all([guest_name, start_date, end_date, contact_person]):
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
        
        # Trigger email notifications
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
    pin = request.headers.get('Admin-PIN') or request.json.get('pin') if request.json else None
    if pin != '1234':
        return jsonify({'success': False, 'message': 'Ungültige Admin-PIN.'}), 403
        
    success = database.approve_booking(booking_id)
    if success:
        return jsonify({'success': True, 'message': 'Eintrag erfolgreich freigegeben.'})
    return jsonify({'success': False, 'message': 'Eintrag nicht gefunden.'}), 404

@app.route('/api/bookings/<int:booking_id>/delete', methods=['POST'])
def api_delete_booking(booking_id):
    pin = request.headers.get('Admin-PIN') or request.json.get('pin') if request.json else None
    if pin != '1234':
        return jsonify({'success': False, 'message': 'Ungültige Admin-PIN.'}), 403
        
    success = database.delete_booking(booking_id)
    if success:
        return jsonify({'success': True, 'message': 'Eintrag gelöscht.'})
    return jsonify({'success': False, 'message': 'Eintrag nicht gefunden.'}), 404

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
                # Prefix with timestamp to avoid duplicates
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
    pin = request.headers.get('Admin-PIN') or request.json.get('pin') if request.json else None
    if pin != '1234':
        return jsonify({'success': False, 'message': 'Ungültige Admin-PIN.'}), 403
        
    success = database.delete_expense(expense_id)
    if success:
        return jsonify({'success': True, 'message': 'Ausgabe gelöscht.'})
    return jsonify({'success': False, 'message': 'Ausgabe nicht gefunden.'}), 404

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5001, debug=True)
