import json
import logging
import os
import requests
import smtplib
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from flask import Flask, render_template, render_template_string, jsonify, request
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

def send_email_notifications(booking_id, sender_name, start_date, end_date, message_text):
    """Sends notification HTML emails with Approve/Decline buttons to all family members EXCEPT the sender."""
    emails = get_family_emails()
    recipients = {name: email for name, email in emails.items() if name != sender_name}
    
    start_swiss = format_date_swiss_py(start_date)
    end_swiss = format_date_swiss_py(end_date)
    auto_confirm = datetime.utcnow() + timedelta(days=10)
    auto_confirm_swiss = auto_confirm.strftime("%d.%m.%Y")
    
    subject = f"Neue Buchungsanfrage Ferienhaus Laax: {sender_name}"
    
    # Try getting host URL from request context, fallback to production domain
    try:
        base_url = request.host_url.rstrip('/')
    except RuntimeError:
        base_url = "https://casa-benz-salums.ch"
        
    # 1. SMTP config
    smtp_server, smtp_port, smtp_user, smtp_password = get_smtp_config()
    
    logger.info("=" * 60)
    logger.info(f"EMAIL NOTIFICATION TRIGGERED BY: {sender_name} for ID {booking_id}")
    logger.info(f"Recipients: {', '.join(recipients.values())}")
    logger.info("=" * 60)

    try:
        port = int(smtp_port) if smtp_port else 587
    except ValueError:
        port = 587
        
    for recipient_name, recipient_email in recipients.items():
        recipient_first_name = recipient_name.split(' ')[0]
        approve_url = f"{base_url}/api/bookings/email-action?action=approve&id={booking_id}&user={recipient_name}"
        reject_url = f"{base_url}/api/bookings/email-action?action=reject_prompt&id={booking_id}&user={recipient_name}"
        
        # HTML template
        html_body = f"""
        <!DOCTYPE html>
        <html lang="de">
        <body style="font-family: Arial, sans-serif; background-color: #f8fafc; padding: 20px; color: #334155;">
            <div style="max-width: 600px; margin: 0 auto; background: white; border: 1px solid #e2e8f0; border-radius: 8px; overflow: hidden; box-shadow: 0 1px 3px rgba(0,0,0,0.1);">
                <div style="background: #1e293b; color: white; padding: 20px; text-align: center;">
                    <h2 style="margin: 0; font-size: 18px;">Neue Buchungsanfrage – Ferienhaus Laax</h2>
                </div>
                <div style="padding: 25px; line-height: 1.6; font-size: 14px;">
                    <p>Hallo {recipient_first_name},</p>
                    <p><strong>{sender_name}</strong> hat eine Buchungsanfrage für das Ferienhaus eingetragen:</p>
                    
                    <table style="width: 100%; border-collapse: collapse; margin: 20px 0; background: #f1f5f9; border-radius: 6px; overflow: hidden;">
                        <tr>
                            <td style="padding: 12px; font-weight: bold; border-bottom: 1px solid #e2e8f0; width: 120px;">Zeitraum:</td>
                            <td style="padding: 12px; border-bottom: 1px solid #e2e8f0;">{start_swiss} bis {end_swiss}</td>
                        </tr>
                        <tr>
                            <td style="padding: 12px; font-weight: bold;">Notiz:</td>
                            <td style="padding: 12px;">{message_text or '—'}</td>
                        </tr>
                    </table>
                    
                    <p style="margin-bottom: 25px;">Bitte stimme dich bei Bedarf ab oder nutze die folgenden Buttons, um die Buchung direkt freizugeben oder abzulehnen:</p>
                    
                    <div style="text-align: center; margin: 30px 0;">
                        <a href="{approve_url}" style="background-color: #16a34a; color: white; padding: 12px 24px; text-decoration: none; border-radius: 6px; font-weight: bold; margin-right: 15px; display: inline-block;">Bestätigen</a>
                        <a href="{reject_url}" style="background-color: #dc2626; color: white; padding: 12px 24px; text-decoration: none; border-radius: 6px; font-weight: bold; display: inline-block;">Ablehnen</a>
                    </div>
                    
                    <hr style="border: 0; border-top: 1px solid #e2e8f0; margin: 30px 0;">
                    <p style="font-size: 11px; color: #64748b; text-align: center;">
                        * Diese Anfrage wird am {auto_confirm_swiss} automatisch freigegeben, falls keine Rückmeldung erfolgt.
                    </p>
                </div>
            </div>
        </body>
        </html>
        """
        
        plain_body = (
            f"Hallo {recipient_first_name},\n\n"
            f"{sender_name} hat eine Buchungsanfrage eingetragen:\n"
            f"Zeitraum: {start_swiss} bis {end_swiss}\n"
            f"Notiz: {message_text or '—'}\n\n"
            f"Zum Bestätigen: {approve_url}\n"
            f"Zum Ablehnen: {reject_url}\n"
        )
        
        if smtp_server and smtp_user and smtp_password:
            try:
                msg = MIMEMultipart('alternative')
                msg['From'] = f"Casa Benz Salums <{smtp_user}>"
                msg['To'] = recipient_email
                msg['Subject'] = subject
                
                msg.attach(MIMEText(plain_body, 'plain', 'utf-8'))
                msg.attach(MIMEText(html_body, 'html', 'utf-8'))
                
                if port == 465:
                    with smtplib.SMTP_SSL(smtp_server, port, timeout=10) as server:
                        server.login(smtp_user, smtp_password)
                        server.sendmail(smtp_user, recipient_email, msg.as_string())
                else:
                    with smtplib.SMTP(smtp_server, port, timeout=10) as server:
                        server.starttls()
                        server.login(smtp_user, smtp_password)
                        server.sendmail(smtp_user, recipient_email, msg.as_string())
                logger.info(f"SMTP email sent successfully to {recipient_name}.")
            except Exception as e:
                logger.error(f"Failed to send SMTP email to {recipient_name}: {e}")
        else:
            logger.info(f"[NO SMTP] Email log for {recipient_name}:\nApprove: {approve_url}\nReject: {reject_url}")

def send_rejection_notification(guest_name, approver_name, start_date, end_date, reason_text):
    """Sends a notification email to the booking creator informing them of the rejection and the reason."""
    emails = get_family_emails()
    recipient_email = emails.get(guest_name)
    
    start_swiss = format_date_swiss_py(start_date)
    end_swiss = format_date_swiss_py(end_date)
    
    # 1. Email Rejection
    if recipient_email:
        subject = f"Buchungsanfrage abgelehnt: {guest_name}"
        body = (
            f"Hallo {guest_name.split(' ')[0]},\n\n"
            f"Deine Buchungsanfrage für das Ferienhaus in Laax im Zeitraum {start_swiss} bis {end_swiss} "
            f"wurde von {approver_name} abgelehnt.\n\n"
            f"Begründung:\n\"{reason_text}\"\n\n"
            f"Das Buchungstool ist unter https://casa-benz-salums.ch erreichbar.\n"
        )
        
        logger.info("=" * 60)
        logger.info(f"REJECTION NOTIFICATION SENT TO: {guest_name} ({recipient_email})")
        logger.info(f"Reason: {reason_text}")
        logger.info("=" * 60)
        
        smtp_server, smtp_port, smtp_user, smtp_password = get_smtp_config()
        if smtp_server and smtp_user and smtp_password:
            try:
                port = int(smtp_port) if smtp_port else 587
            except ValueError:
                port = 587
                
            msg = MIMEMultipart()
            msg['From'] = f"Casa Benz Salums <{smtp_user}>"
            msg['To'] = recipient_email
            msg['Subject'] = subject
            msg.attach(MIMEText(body, 'plain', 'utf-8'))
            
            try:
                if port == 465:
                    with smtplib.SMTP_SSL(smtp_server, port, timeout=10) as server:
                        server.login(smtp_user, smtp_password)
                        server.sendmail(smtp_user, recipient_email, msg.as_string())
                else:
                    with smtplib.SMTP(smtp_server, port, timeout=10) as server:
                        server.starttls()
                        server.login(smtp_user, smtp_password)
                        server.sendmail(smtp_user, recipient_email, msg.as_string())
                logger.info("Rejection email sent successfully.")
            except Exception as e:
                logger.error(f"Failed to send rejection SMTP email: {e}")

    # 2. WhatsApp Rejection
    phone_key = f"phone_{guest_name.split(' ')[0].lower()}"
    apikey_key = f"wa_apikey_{guest_name.split(' ')[0].lower()}"
    phone = database.get_setting(phone_key, '')
    apikey = database.get_setting(apikey_key, '')
    
    if phone and apikey:
        text = (
            f"Hallo {guest_name.split(' ')[0]}!\n\n"
            f"Deine Buchungsanfrage fuer das Ferienhaus im Zeitraum {start_swiss} bis {end_swiss} "
            f"wurde von *{approver_name}* abgelehnt.\n\n"
            f"Begruendung:\n\"{reason_text}\""
        )
        try:
            res = requests.get(
                "https://api.callmebot.com/whatsapp.php",
                params={"phone": phone, "text": text, "apikey": apikey},
                timeout=10
            )
            logger.info(f"Rejection WhatsApp sent to {guest_name} (Status: {res.status_code})")
        except Exception as e:
            logger.error(f"Failed to send rejection WhatsApp to {guest_name}: {e}")

def send_whatsapp_notifications(booking_id, sender_name, start_date, end_date, message_text):
    """Sends WhatsApp messages using CallMeBot to all family members EXCEPT the sender."""
    emails = get_family_emails()
    recipients = [name for name in emails.keys() if name != sender_name]
    
    start_swiss = format_date_swiss_py(start_date)
    end_swiss = format_date_swiss_py(end_date)
    
    try:
        base_url = request.host_url.rstrip('/')
    except RuntimeError:
        base_url = "https://casa-benz-salums.ch"
        
    for recipient_name in recipients:
        phone_key = f"phone_{recipient_name.split(' ')[0].lower()}"
        apikey_key = f"wa_apikey_{recipient_name.split(' ')[0].lower()}"
        
        phone = database.get_setting(phone_key, '')
        apikey = database.get_setting(apikey_key, '')
        
        if phone and apikey:
            recipient_first_name = recipient_name.split(' ')[0]
            approve_url = f"{base_url}/api/bookings/email-action?action=approve&id={booking_id}&user={recipient_name}"
            reject_url = f"{base_url}/api/bookings/email-action?action=reject_prompt&id={booking_id}&user={recipient_name}"
            
            text = (
                f"Hallo {recipient_first_name}!\n\n"
                f"*{sender_name}* hat eine Buchungsanfrage fuer das Ferienhaus eingetragen:\n"
                f"Zeitraum: {start_swiss} bis {end_swiss}\n"
                f"Notiz: {message_text or '—'}\n\n"
                f"Direkt Bestaetigen:\n{approve_url}\n\n"
                f"Direkt Ablehnen:\n{reject_url}"
            )
            
            try:
                res = requests.get(
                    "https://api.callmebot.com/whatsapp.php",
                    params={"phone": phone, "text": text, "apikey": apikey},
                    timeout=10
                )
                logger.info(f"WhatsApp sent to {recipient_name} (Status: {res.status_code})")
            except Exception as e:
                logger.error(f"Failed to send WhatsApp to {recipient_name}: {e}")

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
        
    # Always succeed (no password required for users)
    if True:
        role = 'admin' if username == 'Admin' else 'family'
        return jsonify({
            'success': True,
            'message': f'Erfolgreich als {username} angemeldet.',
            'username': username,
            'role': role
        })

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
        
        send_email_notifications(booking_id, guest_name, start_date, end_date, message)
        send_whatsapp_notifications(booking_id, guest_name, start_date, end_date, message)
        
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
                
        shared_by_list = request.form.getlist('shared_by')
        shared_by = ",".join(shared_by_list) if shared_by_list else "Chiara Benz,Seraina Benz,Alex Benz"
        
        expense_id = database.add_expense(
            item_description=item_description,
            amount=amount,
            paid_by=paid_by,
            receipt_path=receipt_path,
            shared_by=shared_by
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
            'pin_admin': database.get_setting('pin_admin', '1234'),
            'phone_chiara': database.get_setting('phone_chiara', ''),
            'wa_apikey_chiara': database.get_setting('wa_apikey_chiara', ''),
            'phone_seraina': database.get_setting('phone_seraina', ''),
            'wa_apikey_seraina': database.get_setting('wa_apikey_seraina', ''),
            'phone_alex': database.get_setting('phone_alex', ''),
            'wa_apikey_alex': database.get_setting('wa_apikey_alex', '')
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
        
        # WhatsApp CallMeBot Settings
        if 'phone_chiara' in data: database.set_setting('phone_chiara', data.get('phone_chiara', ''))
        if 'wa_apikey_chiara' in data: database.set_setting('wa_apikey_chiara', data.get('wa_apikey_chiara', ''))
        if 'phone_seraina' in data: database.set_setting('phone_seraina', data.get('phone_seraina', ''))
        if 'wa_apikey_seraina' in data: database.set_setting('wa_apikey_seraina', data.get('wa_apikey_seraina', ''))
        if 'phone_alex' in data: database.set_setting('phone_alex', data.get('phone_alex', ''))
        if 'wa_apikey_alex' in data: database.set_setting('wa_apikey_alex', data.get('wa_apikey_alex', ''))
        
        return jsonify({'success': True, 'message': 'Einstellungen gespeichert.'})

@app.route('/api/bookings/email-action', methods=['GET', 'POST'])
def api_email_action():
    action = request.values.get('action')
    booking_id_str = request.values.get('id')
    user = request.values.get('user')
    
    if not action or not booking_id_str or not user:
        return "Fehlende Parameter (action, id, user).", 400
        
    try:
        booking_id = int(booking_id_str)
    except ValueError:
        return "Ungültige Buchungs-ID.", 400
        
    # Get booking details
    bookings = database.get_all_bookings()
    booking = next((b for b in bookings if b['id'] == booking_id), None)
    if not booking:
        return f"Die Buchungsanfrage (ID {booking_id}) existiert nicht oder wurde bereits gelöscht/abgelehnt.", 404
        
    # Prevent self-approval via email link
    if action == 'approve' and booking['guest_name'] == user:
        return f"<h3>Fehler:</h3> Du kannst deine eigene Buchung nicht selbst freigeben. Das müssen die anderen Familienmitglieder tun.", 403
        
    if request.method == 'GET':
        if action == 'approve':
            success = database.approve_booking(booking_id)
            if success:
                return render_template_string("""
                <!DOCTYPE html>
                <html lang="de">
                <head>
                    <meta charset="UTF-8">
                    <meta name="viewport" content="width=device-width, initial-scale=1.0">
                    <title>Buchung bestätigt</title>
                    <script src="https://cdn.tailwindcss.com"></script>
                </head>
                <body class="bg-slate-50 flex items-center justify-center min-h-screen p-4 text-center font-sans antialiased text-slate-700">
                    <div class="bg-white p-8 rounded-lg shadow border border-slate-200 max-w-md w-full">
                        <div class="text-green-600 text-5xl mb-4"><i class="fa-solid fa-circle-check"></i></div>
                        <h1 class="text-xl font-bold text-slate-800 mb-2">Erfolgreich bestätigt!</h1>
                        <p class="text-sm text-slate-500 mb-6">Vielen Dank, <strong>{{ user }}</strong>. Die Buchung von <strong>{{ guest }}</strong> wurde freigegeben und ist nun im Kalender eingetragen.</p>
                        <a href="/" class="inline-block bg-slate-800 text-white px-6 py-2 rounded text-sm font-semibold hover:bg-slate-900 transition">Zum Portal</a>
                    </div>
                </body>
                </html>
                """, user=user, guest=booking['guest_name'])
            return "Datenbankfehler bei Freigabe.", 500
            
        elif action == 'reject_prompt':
            return render_template_string("""
            <!DOCTYPE html>
            <html lang="de">
            <head>
                <meta charset="UTF-8">
                <meta name="viewport" content="width=device-width, initial-scale=1.0">
                <title>Buchung ablehnen</title>
                <script src="https://cdn.tailwindcss.com"></script>
            </head>
            <body class="bg-slate-50 flex items-center justify-center min-h-screen p-4 font-sans antialiased text-slate-700">
                <div class="bg-white p-8 rounded-lg shadow border border-slate-200 max-w-md w-full">
                    <h1 class="text-xl font-bold text-slate-800 mb-2 text-center">Buchung ablehnen</h1>
                    <p class="text-xs text-slate-500 mb-6 text-center">Bitte gib eine Begründung für die Ablehnung der Buchungsanfrage von <strong>{{ guest }}</strong> an.</p>
                    
                    <form action="/api/bookings/email-action" method="POST" class="space-y-4">
                        <input type="hidden" name="action" value="reject_submit">
                        <input type="hidden" name="id" value="{{ booking_id }}">
                        <input type="hidden" name="user" value="{{ user }}">
                        
                        <div>
                            <label for="reason" class="block text-xs font-semibold text-slate-500 uppercase tracking-wider mb-1">Begründung *</label>
                            <textarea id="reason" name="reason" required rows="3" class="w-full px-3 py-2 border border-slate-300 rounded text-sm focus:ring-slate-500 focus:border-slate-500" placeholder="Z.B. Terminkonflikt, bin selber dort, etc."></textarea>
                        </div>
                        
                        <button type="submit" class="w-full bg-red-600 hover:bg-red-700 text-white py-2 rounded text-sm font-semibold transition">
                            Buchung ablehnen
                        </button>
                    </form>
                </div>
            </body>
            </html>
            """, user=user, guest=booking['guest_name'], booking_id=booking_id)
            
    elif request.method == 'POST' and action == 'reject_submit':
        reason = request.form.get('reason')
        if not reason:
            return "Begründung fehlt.", 400
            
        success = database.reject_booking(booking_id)
        if success:
            send_rejection_notification(
                guest_name=booking['guest_name'],
                approver_name=user,
                start_date=booking['start_date'],
                end_date=booking['end_date'],
                reason_text=reason
            )
            return render_template_string("""
            <!DOCTYPE html>
            <html lang="de">
            <head>
                <meta charset="UTF-8">
                <meta name="viewport" content="width=device-width, initial-scale=1.0">
                <title>Buchung abgelehnt</title>
                <script src="https://cdn.tailwindcss.com"></script>
            </head>
            <body class="bg-slate-50 flex items-center justify-center min-h-screen p-4 text-center font-sans antialiased text-slate-700">
                <div class="bg-white p-8 rounded-lg shadow border border-slate-200 max-w-md w-full">
                    <div class="text-red-600 text-5xl mb-4"><i class="fa-solid fa-circle-xmark"></i></div>
                    <h1 class="text-xl font-bold text-slate-800 mb-2">Erfolgreich abgelehnt</h1>
                    <p class="text-sm text-slate-500 mb-6">Die Anfrage wurde abgelehnt und der Ersteller wurde per E-Mail informiert.</p>
                    <a href="/" class="inline-block bg-slate-800 text-white px-6 py-2 rounded text-sm font-semibold hover:bg-slate-900 transition">Zum Portal</a>
                </div>
            </body>
            </html>
            """)
        return "Datenbankfehler bei Ablehnung.", 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5001, debug=True)
