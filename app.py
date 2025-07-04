# Final Flask backend code for MedTrack using real DynamoDB

from flask import Flask, request, jsonify, session, render_template, redirect, url_for, flash
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, timedelta
from dotenv import load_dotenv
import boto3
import logging
import os
import uuid
from functools import wraps
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

# Load env variables
load_dotenv()

# Logger
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

# Flask app
app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', os.urandom(24))
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(hours=1)

# Configs
AWS_REGION_NAME = os.getenv('AWS_REGION_NAME', 'ap-south-1')

# Email
SMTP_SERVER = os.getenv('SMTP_SERVER', 'smtp.gmail.com')
SMTP_PORT = int(os.getenv('SMTP_PORT', 587))
SENDER_EMAIL = os.getenv('SENDER_EMAIL')
SENDER_PASSWORD = os.getenv('SENDER_PASSWORD')
ENABLE_EMAIL = os.getenv('ENABLE_EMAIL', 'false').lower() == 'true'

# SNS
SNS_TOPIC_ARN = os.getenv('SNS_TOPIC_ARN')
ENABLE_SNS = os.getenv('ENABLE_SNS', 'false').lower() == 'true'

# Table Names
USERS_TABLE_NAME = os.getenv('USERS_TABLE_NAME', 'MedTrackUsers')
APPOINTMENTS_TABLE_NAME = os.getenv('APPOINTMENTS_TABLE_NAME', 'MedTrackAppointments')

# AWS Resources
try:
    dynamodb = boto3.resource('dynamodb', region_name=AWS_REGION_NAME)
    sns = boto3.client('sns', region_name=AWS_REGION_NAME) if ENABLE_SNS else None
except Exception as e:
    logger.error(f"AWS init error: {e}")
    dynamodb = None
    sns = None

# Table Helpers
def get_user_table():
    return dynamodb.Table(USERS_TABLE_NAME) if dynamodb else None

def get_appointments_table():
    return dynamodb.Table(APPOINTMENTS_TABLE_NAME) if dynamodb else None

# Utils
def send_email_notification(to_email, subject, body):
    if not ENABLE_EMAIL or not SENDER_EMAIL:
        return True
    try:
        msg = MIMEMultipart()
        msg['From'] = SENDER_EMAIL
        msg['To'] = to_email
        msg['Subject'] = subject
        msg.attach(MIMEText(body, 'plain'))

        server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
        server.starttls()
        server.login(SENDER_EMAIL, SENDER_PASSWORD)
        server.send_message(msg)
        server.quit()
        return True
    except Exception as e:
        logger.error(f"Email error: {e}")
        return False

def send_sns_notification(message):
    if not ENABLE_SNS or not sns or not SNS_TOPIC_ARN:
        return True
    try:
        sns.publish(TopicArn=SNS_TOPIC_ARN, Message=message)
        return True
    except Exception as e:
        logger.error(f"SNS error: {e}")
        return False

# Auth Decorator
def login_required(role=None):
    def decorator(f):
        @wraps(f)
        def wrapped(*args, **kwargs):
            if 'user' not in session:
                flash("Login required", 'danger')
                return redirect(url_for('index'))
            if role and session.get('role') != role:
                flash("Unauthorized", 'danger')
                return redirect(url_for('index'))
            return f(*args, **kwargs)
        return wrapped
    return decorator

# Routes
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/signup/<role>', methods=['GET', 'POST'])
def signup(role):
    if request.method == 'POST':
        name = request.form['name']
        email = request.form['email']
        password = request.form['password']
        table = get_user_table()
        resp = table.get_item(Key={'email': email})
        if 'Item' in resp:
            flash('User exists.', 'danger')
            return render_template('signup.html', role=role)
        user_data = {
            'user_id': str(uuid.uuid4()),
            'name': name,
            'email': email,
            'role': role,
            'password_hash': generate_password_hash(password),
            'created_at': datetime.now().isoformat(),
        }
        table.put_item(Item=user_data)
        flash('Signup successful', 'success')
        return redirect(url_for('login', role=role))
    return render_template('signup.html', role=role)

@app.route('/login/<role>', methods=['GET', 'POST'])
def login(role):
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']
        table = get_user_table()
        resp = table.get_item(Key={'email': email})
        user = resp.get('Item')
        if not user or user['role'] != role:
            flash('Invalid credentials', 'danger')
            return render_template('login.html', role=role)
        if not check_password_hash(user['password_hash'], password):
            flash('Wrong password', 'danger')
            return render_template('login.html', role=role)
        session.update({
            'user': user['email'],
            'role': user['role'],
            'name': user['name'],
            'user_id': user['user_id'],
            'email': user['email']
        })
        return redirect(url_for(f'{role}_dashboard'))
    return render_template('login.html', role=role)

@app.route('/logout')
@login_required()
def logout():
    session.clear()
    flash('Logged out', 'success')
    return redirect(url_for('index'))

@app.route('/patient_dashboard')
@login_required(role='patient')
def patient_dashboard():
    email = session['email']
    # Fetch user and appointments
    user_table = get_user_table()
    appt_table = get_appointments_table()
    user = user_table.get_item(Key={'email': email}).get('Item')
    appointments = appt_table.scan()['Items']
    upcoming = [a for a in appointments if a['patient'] == email]
    return render_template('patient_dashboard.html', name=user['name'], appointments=upcoming)

@app.route('/doctor_dashboard')
@login_required(role='doctor')
def doctor_dashboard():
    email = session['email']
    appt_table = get_appointments_table()
    appointments = appt_table.scan()['Items']
    doctor_appts = [a for a in appointments if a['doctor'] == email]
    return render_template('doctor_dashboard.html', name=session['name'], appointments=doctor_appts)

@app.route('/book_appointment', methods=['GET', 'POST'])
@login_required(role='patient')
def book_appointment():
    if request.method == 'POST':
        data = request.form
        appt_id = str(uuid.uuid4())
        appt = {
            'appointment_id': appt_id,
            'title': data.get('title', 'Consultation'),
            'date': data['date'],
            'time': data['time'],
            'doctor': data['doctor'],
            'patient': session['email'],
            'created_at': datetime.now().isoformat(),
        }
        get_appointments_table().put_item(Item=appt)
        flash('Appointment booked', 'success')
        return redirect(url_for('patient_dashboard'))
    return render_template('book_appointment.html')

if __name__ == '__main__':
    app.run(debug=True)
