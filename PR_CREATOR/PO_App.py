import os
from flask import Flask, render_template, request
import requests
import xml.etree.ElementTree as ET
import base64
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from apscheduler.schedulers.background import BackgroundScheduler
from datetime import datetime
import pymysql
import socket
from CSV_Param import CSV_Proj_Params # Import the new config loader

Curr_Proj_Name = 'SAP_PR' 

# ----------------------- Flask App ----------------------- #
app = Flask(__name__, static_folder='templates/static')
app.config['SECRET_KEY'] = os.urandom(24)

# ----------------------- Configuration ----------------------- #
proj_params = CSV_Proj_Params(Curr_Proj_Name) # Load DB config for this app
DB_HOST = proj_params.get('MYSQL_HOST')
DB_PORT = proj_params.get('MYSQL_PORT')
DB_USER = proj_params.get('MYSQL_USER')
DB_PASSWORD = proj_params.get('MYSQL_PASSWORD')
DB_NAME = proj_params.get('MYSQL_DB')
DB_CURSORCLASS = proj_params.get('MYSQL_CURSORCLASS') # Add cursor class

# ----------------------- Helper Functions ----------------------- #
def format_doc_type(doc_type):
    if doc_type == 'SuplrDwnPaytReqToBeVerified':
        return 'Supplier DPR'
    import re
    return re.sub(r'(?<!^)(?=[A-Z])', ' ', doc_type)

def format_doc_number(doc_number, doc_type):
    if doc_type == 'SuplrDwnPaytReqToBeVerified':
        return doc_number[:-8]
    return doc_number.lstrip('0')

def send_email(recipient_email, data):
    grouped_data = {}
    for item in data:
        doc_type = item.get('SAPObjectNodeRepresentation')
        grouped_data.setdefault(doc_type, []).append(item.get('SAPBusinessObjectNodeKey1'))

    total_docs = sum(len(docs) for docs in grouped_data.values())
    msg = MIMEMultipart('alternative')
    msg['Subject'] = f"Your Approval Needed: SAP Documents in Queue ({total_docs})"
    msg['From'] = EMAIL_ADDRESS
    msg['To'] = RECIPIENT_EMAIL
    msg['Cc'] = 'keerthana.u@violintec.com'
    msg['X-Priority'] = '1 (Highest)'
    msg['X-MSMail-Priority'] = 'High'
    msg['Importance'] = 'High'

    approver_name = "Approver"
    if data:
        first_name = data[0].get('FirstName', '')
        last_name = data[0].get('LastName', '')
        if first_name or last_name:
            approver_name = f"{first_name} {last_name}".strip()

    html = f"""
    <html>
      <body>
        <p>Dear {approver_name},</p>
        <p>The following SAP documents are pending for your approval. Kindly review and approve them at the earliest:</p>
    """
    for doc_type, doc_numbers in sorted(grouped_data.items()):
        html += f"<p><strong>{format_doc_type(doc_type)} ({len(doc_numbers)}):</strong></p><ul>"
        for doc_number in sorted(doc_numbers):
            html += f"<li>{format_doc_number(doc_number, doc_type)}</li>"
        html += "</ul>"

    html += """
        <p><strong>SAP Link:</strong> https://my426081.s4hana.cloud.sap/ui#WorkflowTask-displayInbox</p>
        <p>Regards,<br>SAP Automation</p>
        <i>This is an automated email. Please do not reply.</i>
      </body>
    </html>
    """
    msg.attach(MIMEText(html, 'html'))

    try:
        recipients = [RECIPIENT_EMAIL] + msg['Cc'].split(',')
        server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
        server.starttls()
        server.login(EMAIL_ADDRESS, EMAIL_PASSWORD)
        server.sendmail(EMAIL_ADDRESS, recipients, msg.as_string())
        server.quit()
        print(f"Email sent successfully at {datetime.now()}")
    except Exception as e:
        print(f"Failed to send email: {e}")

def fetch_and_send(api_url, username, password):
    with app.app_context():
        print(f"fetch_and_send called at {datetime.now()} for API: {api_url}")
        try:
            auth_string = f"{username}:{password}"
            encoded_auth = base64.b64encode(auth_string.encode()).decode()
            headers = {"Authorization": f"Basic {encoded_auth}"}

            response = requests.get(api_url + "?$format=xml", headers=headers, verify=False)
            response.raise_for_status()

            root = ET.fromstring(response.content)
            entries = root.findall('.//{http://www.w3.org/2005/Atom}entry')
            all_data = []
            for entry in entries:
                props = {prop.tag.split('}')[-1]: prop.text for prop in entry.findall('.//{http://schemas.microsoft.com/ado/2007/08/dataservices/metadata}properties/*')}
                all_data.append(props)

            if all_data:
                grouped_by_email = {}
                for item in all_data:
                    email = item.get('EmailAddress')
                    if email:
                        grouped_by_email.setdefault(email, []).append(item)
                for email, user_data in grouped_by_email.items():
                    send_email(email, user_data)
            else:
                print("No data fetched.")
        except Exception as e:
            print(f"Error fetching data: {e}")

def fetch_data(api_url, username, password):
    print(f"fetch_data called at {datetime.now()} for API: {api_url}")
    try:
        auth_string = f"{username}:{password}"
        encoded_auth = base64.b64encode(auth_string.encode()).decode()
        headers = {"Authorization": f"Basic {encoded_auth}"}

        response = requests.get(api_url + "?$format=xml", headers=headers, verify=False)
        response.raise_for_status()

        root = ET.fromstring(response.content)
        entries = root.findall('.//{http://www.w3.org/2005/Atom}entry')
        all_data = []
        for entry in entries:
            props = {prop.tag.split('}')[-1]: prop.text for prop in entry.findall('.//{http://schemas.microsoft.com/ado/2007/08/dataservices/metadata}properties/*')}
            all_data.append(props)
        return all_data
    except Exception as e:
        print(f"Error fetching data: {e}")
        return None

def send_immediate_mail(api_url, username, password):
    print("Immediate mail sending process started.")
    fetch_and_send(api_url, username, password)
    print("Immediate mail sending process finished.")

# ----------------------- Routes ----------------------- #
@app.route(f'/', methods=['GET', 'POST'])
def index():
    data = None
    error = None
    success_message = None

    if request.method == 'POST':
        action = request.form.get('action')
        api_url = request.form.get('odata_url')
        username = request.form.get('username')
        password = request.form.get('password')

        if action == 'fetch' and api_url and username and password:
            data = fetch_data(api_url, username, password)
            if data:
                success_message = "Data fetched successfully."
            else:
                error = "Failed to fetch data."
        elif action == 'send_mail' and api_url and username and password:
            send_immediate_mail(api_url, username, password)
            success_message = "Immediate mail sent successfully."
        elif action == 'add_schedule' and api_url and username and password:
            minute = request.form.get('minute')
            hour = request.form.get('hour')
            day_of_month = request.form.get('day_of_month')
            month = request.form.get('month')
            day_of_week = request.form.get('day_of_week')
            try:
                conn = get_db_connection()
                with conn.cursor() as cursor:
                    cursor.execute(
                        "INSERT INTO schedules (minute, hour, day_of_month, month, day_of_week, api_url, username, password) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                        (minute, hour, day_of_month, month, day_of_week, api_url, username, password)
                    )
                conn.commit()
                conn.close()
                configure_scheduler()
                success_message = "Schedule added successfully."
            except Exception as e:
                error = f"Error adding schedule: {e}"
        elif action == 'delete_schedule':
            schedule_id = request.form.get('schedule_id')
            try:
                connection = get_db_connection()
                with connection.cursor() as cursor:
                    cursor.execute("DELETE FROM schedules WHERE id = %s", (schedule_id,))
                connection.commit()
                connection.close()
                configure_scheduler()
                success_message = "Schedule deleted successfully."
            except Exception as e:
                error = f"Error deleting schedule: {e}"
        else:
            error = "API URL, username, and password are required."

    schedules = []
    try:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            cursor.execute("SELECT * FROM schedules")
            schedules = cursor.fetchall()
        conn.close()
    except Exception as e:
        print(f"Could not fetch schedules: {e}")

    return render_template('index.html', data=data, error=error, success_message=success_message, schedules=schedules)

# ----------------------- Database & Scheduler ----------------------- #
def get_db_connection():
    return pymysql.connect(host=DB_HOST, user=DB_USER, password=DB_PASSWORD, database=DB_NAME, cursorclass=DB_CURSORCLASS)

def init_db():
    try:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS schedules (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    minute VARCHAR(255),
                    hour VARCHAR(255),
                    day_of_month VARCHAR(255),
                    month VARCHAR(255),
                    day_of_week VARCHAR(255),
                    api_url VARCHAR(255),
                    username VARCHAR(255),
                    password VARCHAR(255)
                )
            """)
        conn.commit()
        conn.close()
        print("Database initialized successfully.")
    except Exception as e:
        print(f"Database init failed: {e}")

scheduler = None
def configure_scheduler():
    global scheduler
    if scheduler and scheduler.running:
        scheduler.shutdown()
    scheduler = BackgroundScheduler()
    try:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            cursor.execute("SELECT * FROM schedules")
            schedules = cursor.fetchall()
        conn.close()
        for s in schedules:
            scheduler.add_job(fetch_and_send, 'cron',
                              minute=s['minute'], hour=s['hour'],
                              day=s['day_of_month'], month=s['month'],
                              day_of_week=s['day_of_week'],
                              args=[s['api_url'], s['username'], s['password']])
        if schedules:
            scheduler.start()
    except Exception as e:
        print(f"Scheduler configuration error: {e}")

# ----------------------- Run Server ----------------------- #
