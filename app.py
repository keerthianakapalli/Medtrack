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


# ---------------------------------------
# Load Environment Variables
# ---------------------------------------
if not load_dotenv():
    print("Warning: .env file not found. Using default configurations.")

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)


# ---------------------------------------
# Flask App Initialization
# ---------------------------------------
app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', os.urandom(24))
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(hours=1)

# ---------------------------------------
# App Configuration
# ---------------------------------------
AWS_REGION_NAME = os.environ.get('AWS_REGION_NAME', 'ap-south-1')


# Email Configuration
SMTP_SERVER = os.environ.get('SMTP_SERVER', 'smtp.gmail.com')
SMTP_PORT = int(os.environ.get('SMTP_PORT', 587))
SENDER_EMAIL = os.environ.get('SENDER_EMAIL')
SENDER_PASSWORD = os.environ.get('SENDER_PASSWORD')
ENABLE_EMAIL = os.environ.get('ENABLE_EMAIL', 'False').lower() == 'true'


# DynamoDB Table Names aligned with ER diagram
USERS_TABLE_NAME = os.environ.get('USERS_TABLE_NAME', 'MedTrackUsers')
DOCTORS_TABLE_NAME = os.environ.get('DOCTORS_TABLE_NAME', 'MedTrackDoctors')
PATIENTS_TABLE_NAME = os.environ.get('PATIENTS_TABLE_NAME', 'MedTrackPatients')
APPOINTMENTS_TABLE_NAME = os.environ.get('APPOINTMENTS_TABLE_NAME', 'MedTrackAppointments')
DIAGNOSIS_TABLE_NAME = os.environ.get('DIAGNOSIS_TABLE_NAME', 'MedTrackDiagnosis')
NOTIFICATIONS_TABLE_NAME = os.environ.get('NOTIFICATIONS_TABLE_NAME', 'MedTrackNotifications')
#what each table contains
# ---------------------------------------------------------------------------------------------------------------
#| Table Name              | Key Fields (Based on ER Diagram)                                                    |
#| ----------------------- | ----------------------------------------------------------------------------------- |
#| `MedTrackUsers`         | `UserID (PK)`, `Name`, `Email`, `Role (doctor/patient)`, `Password`, `Phone`        |
#| `MedTrackDoctors`       | `DoctorID (PK)`, `UserID (FK)`, `Specialization`, `Experience`                      |
#| `MedTrackPatients`      | `PatientID (PK)`, `UserID (FK)`, `Age`, `MedicalHistory`                            |
#| `MedTrackAppointments`  | `AppointmentID (PK)`, `PatientID (FK)`, `DoctorID (FK)`, `Date`, `Time`, `Status`   |
#| `MedTrackDiagnosis`     | `DiagnosisID (PK)`, `AppointmentID (FK)`, `DoctorID`, `PatientID`, `Report`, `Date` |
#| `MedTrackNotifications` | `NotificationID (PK)`, `UserID (FK)`, `Message`, `Timestamp`                        |
# ---------------------------------------------------------------------------------------------------------------


# SNS Configuration
SNS_TOPIC_ARN = os.environ.get('SNS_TOPIC_ARN')
ENABLE_SNS = os.environ.get('ENABLE_SNS', 'False').lower() == 'true'

# Login attempt tracking
login_attempts = {}


# ---------------------------------------
# AWS Resources Initialization
# ---------------------------------------

# AWS region and environment setup
AWS_REGION_NAME = os.environ.get('AWS_REGION_NAME', 'ap-south-1')
try:
    # Use local DynamoDB for development if AWS credentials not available
    if os.environ.get('AWS_ACCESS_KEY_ID'):
        dynamodb = boto3.resource('dynamodb', region_name=AWS_REGION_NAME)
        sns = boto3.client('sns', region_name=AWS_REGION_NAME) if ENABLE_SNS else None
    else:
        # Mock DynamoDB for local development
        dynamodb = None
        sns = None
        logger.warning("AWS credentials not found. Running in local mode.")
        
except Exception as e:
    logger.error(f"Error initializing AWS resources: {e}")
    dynamodb = None
    sns = None

# ---------------------------------------
# Mock Database for MedTrack (Local Dev)
# ---------------------------------------

# In-memory "tables" to simulate DynamoDB
local_db = {
    'users': {},
    'doctors': {},
    'patients': {},
    'appointments': [],
    'diagnosis': [],
    'notifications': []
}

# ---------------------------------------
# Database Helper Functions for MedTrack
# ---------------------------------------

def get_users_table():
    return dynamodb.Table(os.getenv('USERS_TABLE_NAME')) if dynamodb else None

def get_doctors_table():
    return dynamodb.Table(os.getenv('DOCTORS_TABLE_NAME')) if dynamodb else None

def get_patients_table():
    return dynamodb.Table(os.getenv('PATIENTS_TABLE_NAME')) if dynamodb else None

def get_appointments_table():
    return dynamodb.Table(os.getenv('APPOINTMENTS_TABLE_NAME')) if dynamodb else None

def get_diagnosis_table():
    return dynamodb.Table(os.getenv('DIAGNOSIS_TABLE_NAME')) if dynamodb else None

def get_notifications_table():
    return dynamodb.Table(os.getenv('NOTIFICATIONS_TABLE_NAME')) if dynamodb else None


# ---------------------------------------
# Authentication Decorator
# ---------------------------------------

def login_required(role=None, api=False):
    """
    Decorator to enforce login. Optionally restrict to a role (e.g., 'doctor' or 'patient').
    Set api=True to return JSON instead of redirect.
    """
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if 'user' not in session:
                if api:
                    return jsonify({'error': 'Authentication required'}), 401
                flash('Please log in to continue.', 'warning')
                return redirect(url_for('index'))

            if role and session.get('role') != role:
                if api:
                    return jsonify({'error': 'Unauthorized'}), 403
                flash('Access denied.', 'danger')
                return redirect(url_for('index'))

            return f(*args, **kwargs)
        return decorated_function
    return decorator


# ---------------------------------------
# Utility Functions
# ---------------------------------------
def send_email_notification(to_email, subject, body):
    if not ENABLE_EMAIL or not SENDER_EMAIL:
        logger.info(f"Email notification would be sent: {subject}")
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
        
        logger.info(f"Email sent successfully to {to_email}")
        return True
    except Exception as e:
        logger.error(f"Failed to send email: {e}")
        return False

def send_sns_notification(message):
    if not ENABLE_SNS or not sns or not SNS_TOPIC_ARN:
        logger.info(f"SNS notification would be sent: {message}")
        return True
    
    try:
        sns.publish(TopicArn=SNS_TOPIC_ARN, Message=message)
        logger.info("SNS notification sent successfully")
        return True
    except Exception as e:
        logger.error(f"Failed to send SNS notification: {e}")
        return False

# ---------------------------------------
# Authentication Routes
# ---------------------------------------

@app.route('/signup/<role>', methods=['GET', 'POST'])
def signup(role):
    if role not in ('patient', 'doctor'):
        flash('Invalid role selected.', 'danger')
        return redirect(url_for('index'))

    if request.method == 'POST':
        name = request.form.get('name')
        email = request.form.get('email')
        password = request.form.get('password')

        if not name or not email or not password:
            flash('All fields are required.', 'warning')
            return render_template('signup.html', role=role)

        # Check if user already exists
        if dynamodb:
            user_table = get_user_table()
            response = user_table.get_item(Key={'email': email})
            if 'Item' in response:
                flash('User already exists.', 'danger')
                return render_template('signup.html', role=role)
        else:
            if email in local_db['users']:
                flash('User already exists.', 'danger')
                return render_template('signup.html', role=role)

        # Create user
        user_id = str(uuid.uuid4())
        hashed_password = generate_password_hash(password)
        user_data = {
            'user_id': user_id,
            'name': name,
            'email': email,
            'password_hash': hashed_password,
            'role': role,
            'created_at': datetime.now().isoformat(),
            'is_active': True
        }

        if dynamodb:
            user_table.put_item(Item=user_data)
        else:
            local_db['users'][email] = user_data

        flash('Signup successful! Please log in.', 'success')
        return redirect(url_for('login', role=role))

    return render_template('signup.html', role=role)


login_attempts = {}  # Rate-limiting support
@app.route('/login/<role>', methods=['GET', 'POST'])
def login(role):
    if role not in ('patient', 'doctor'):
        flash("Invalid role.", "danger")
        return redirect(url_for('index'))

    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')

        if not email or not password:
            flash('Email and password are required.', 'warning')
            return render_template('login.html', role=role)

        # Rate-limiting check
        client_ip = request.remote_addr
        if client_ip in login_attempts:
            attempt_data = login_attempts[client_ip]
            if attempt_data['count'] >= 5:
                if datetime.now() - attempt_data['last_attempt'] < timedelta(minutes=15):
                    flash('Too many login attempts. Try again later.', 'danger')
                    return render_template('login.html', role=role)
                else:
                    login_attempts[client_ip] = {'count': 0, 'last_attempt': datetime.now()}

        # Fetch user data
        user_data = None
        if dynamodb:
            user_table = get_user_table()
            try:
                response = user_table.get_item(Key={'email': email})
                if 'Item' in response:
                    user_data = response['Item']
            except Exception as e:
                logger.error(f"Error fetching user from DynamoDB: {e}")
        else:
            user_data = local_db['users'].get(email)

        # Validate user
        if not user_data or user_data.get('role') != role:
            login_attempts.setdefault(client_ip, {'count': 0, 'last_attempt': datetime.now()})
            login_attempts[client_ip]['count'] += 1
            login_attempts[client_ip]['last_attempt'] = datetime.now()
            flash('Invalid email or role.', 'danger')
            return render_template('login.html', role=role)

        if not check_password_hash(user_data.get('password_hash', ''), password):
            login_attempts.setdefault(client_ip, {'count': 0, 'last_attempt': datetime.now()})
            login_attempts[client_ip]['count'] += 1
            login_attempts[client_ip]['last_attempt'] = datetime.now()
            flash('Incorrect password.', 'danger')
            return render_template('login.html', role=role)
          # Reset rate-limiting counter
        login_attempts.pop(client_ip, None)

        session['user'] = user_data['email']   # This is what your @login_required checks!
        session['user_id'] = user_data['user_id']
        session['email'] = user_data['email']
        session['name'] = user_data['name']
        session['role'] = user_data['role']
        session.permanent = True

        logger.info(f"{role.capitalize()} logged in: {email}")
        flash('Login successful!', 'success')

        # Redirect to appropriate dashboard
        print("Redirecting to:", url_for(f"{role}_dashboard"))

        return redirect(url_for(f"{role}_dashboard"))

    return render_template('login.html', role=role)


@app.route('/logout')
def logout_web():
    try:
        user_email = session.get('email')
        user_name = session.get('name')

        session.clear()

        logger.info(f"User logged out (web): {user_email}")
        
        # Optional: notify via SNS or email
        message = f"{user_name} has logged out from MediTrack."
        send_sns_notification(message)

        flash("You have been logged out.", "success")
        return redirect(url_for('login', role='doctor'))  # or just `index` if you have one
    except Exception as e:
        logger.error(f"Logout failed: {e}")
        flash("Logout failed. Please try again.", "danger")
        return redirect(url_for('doctor_dashboard'))




def get_patient_dashboard_data(user_email):
    user = local_db['users'].get(user_email)

    # NEW: Get medications list (required for rendering)
    medications = user.get('medications', [])

    active_medications = user.get('active_medications', 5)
    upcoming_appointments = [a for a in local_db['appointments'] if a.get('patient') == user_email]
    prescriptions = user.get('prescriptions', 4)
    health_score = user.get('health_score', 85)

    notifications = [
        {
            'icon': 'fa-calendar-check',
            'title': 'Upcoming Appointment',
            'text': 'You have an appointment tomorrow.',
            'time': '2 hours ago',
            'unread': True
        }
    ]

    messages = [
        {
            'sender': 'Dr. Smith',
            'time': 'Yesterday',
            'preview': 'Your test results are ready.',
            'unread': True
        }
    ]

    appointments = [
        a for a in local_db['appointments']
        if a.get('patient') == user_email
    ]

    prescriptions_list = [
        {
            'title': 'Hypertension Treatment',
            'issued_date': '2025-05-15',
            'doctor_name': 'Dr. Smith',
            'status': 'Active',
            'medications': [
                {'name': 'Lisinopril', 'dosage': '10mg once daily'},
                {'name': 'Hydrochlorothiazide', 'dosage': '12.5mg once daily'}
            ]
        }
    ]

    return {
        'name': user.get('name'),
        'role': user.get('role'),
        'notifications': notifications,
        'messages': messages,
        'medications': medications,  # Include it here
        'active_medications': active_medications,
        'upcoming_appointments': len(upcoming_appointments),
        'next_appointment': upcoming_appointments[0] if upcoming_appointments else None,
        'prescriptions': prescriptions,
        'appointments': appointments,
        'health_score': health_score,
        'prescriptions_list': prescriptions_list,
    }
@app.route('/add_medication', methods=['POST'])
@login_required(role='patient')
def add_medication():
    user_email = session['user']
    name = request.form.get('name')
    dosage = request.form.get('dosage')
    frequency = request.form.get('frequency')
    start_date = request.form.get('start_date')
    end_date = request.form.get('end_date')

    new_med = {
        'name': name,
        'dosage': dosage,
        'frequency': frequency,
        'start_date': start_date,
        'end_date': end_date,
        'status': 'Active',
        'prescribed_by': 'Self'
    }

    if user_email in local_db['users']:
        local_db['users'][user_email].setdefault('medications', []).append(new_med)

    flash('Medication added successfully!', 'success')
    return redirect(url_for('patient_dashboard'))

def get_doctor_dashboard_data(user_email):
    user = local_db['users'].get(user_email)
    # Example: get all patients assigned to this doctor
    patient_emails = list(user.get('patients', []))
    patients = [local_db['users'][email] for email in patient_emails if email in local_db['users']]

    # Get all appointments for this doctor
    appointments = [
        a for a in local_db.get('appointments', [])
        if a.get('doctor') == user_email
    ]

    # Example: appointments for today
    today_str = datetime.now().strftime('%Y-%m-%d')
    todays_appointments = [
        a for a in local_db.get('appointments', [])
        if a.get('doctor') == user_email and a.get('date') == today_str
    ]
    # Next appointment (if any)
    next_appointment = todays_appointments[0] if todays_appointments else None

    # Example: video consultations for today
    video_consultations = [
        v for v in local_db.get('video_consultations', [])
        if v.get('doctor') == user_email and v.get('date') == today_str
    ]
    next_video_consult = video_consultations[0] if video_consultations else None

    # Example: prescriptions issued this week
    prescriptions_this_week = [
        p for p in local_db.get('prescriptions', [])
        if p.get('doctor') == user_email and
           datetime.strptime(p.get('date'), '%Y-%m-%d').isocalendar()[1] == datetime.now().isocalendar()[1]
    ]

    # Example: notifications and messages count
    notifications_count = user.get('notifications_count', 4)
    messages_count = user.get('messages_count', 7)

    today_str = datetime.now().strftime('%Y-%m-%d')
    todays_appointments_list = [
        {
            'title': a.get('title', 'Consultation'),
            'patient': a.get('patient_name', a.get('patient')),
            'time': a.get('time'),
            'reason': a.get('reason'),
            'location': a.get('location', 'Office 203'),
            'color': a.get('color', '#3498db')
        }
        for a in local_db.get('appointments', [])
        if a.get('doctor') == user_email and a.get('date') == today_str
    ]

    today_str = datetime.now().strftime('%Y-%m-%d')
    video_consultations_list = [
        {
            'patient_name': v.get('patient_name'),
            'patient_avatar': v.get('patient_avatar', 'https://randomuser.me/api/portraits/men/32.jpg'),
            'title': v.get('title', 'Upcoming Call'),
            'status': v.get('status', '10:00 AM Today'),
            'date': v.get('date', today_str),
            'time_range': v.get('time_range', '10:00 AM - 10:30 AM'),
            'reason': v.get('reason', ''),
            'notes': v.get('notes', ''),
        }
        for v in local_db.get('video_consultations', [])
        if v.get('doctor') == user_email and v.get('date') == today_str
    ]

    prescriptions_list = [
        {
        'title': p.get('diagnosis', 'Prescription'),
        'issued_date': p.get('date', ''),
        'patient_name': local_db['users'].get(p.get('patient'), {}).get('name', p.get('patient')),
        'description': p.get('notes', ''),
        'medications': p.get('medications', []),
        'status': p.get('status', 'Active')
    }
    for p in local_db.get('prescriptions', [])
    if p.get('doctor') == user_email
    ]

    # For the prescription form (empty by default)
    medications = []

    current_date = datetime.now().strftime('%Y-%m-%d')


    analytics = {
    'patient_visits': {
        'total': len(patients),
        'new': 45,  # Replace with real count
        'avg_daily': 8.5  # Replace with real calculation
    },
    'appointment_types': [
        {'label': 'Follow-ups', 'value': 42},
        {'label': 'New Patients', 'value': 28},
        {'label': 'Consultations', 'value': 30}
    ],
    'patient_satisfaction': 92,  # Example percentage
    'treatment_outcomes': 87     # Example percentage
    }

    settings = {
        'dark_mode': user.get('dark_mode', False),
        'language': user.get('language', 'en'),
        'timezone': user.get('timezone', 'utc-5'),
        'email_notifications': user.get('email_notifications', True),
        'digest_frequency': user.get('digest_frequency', 'weekly'),
    }

    messages = [
    {
        'sender': 'John Doe',
        'time': '10:30 AM',
        'preview': 'Hello Dr. Johnson, I wanted to ask about my recent blood work results...',
        'unread': True
    },
    {
        'sender': 'Sarah Johnson',
        'time': 'Yesterday',
        'preview': "Thank you for the prescription. I've started the new medication and...",
        'unread': False
    },
    # Add more messages as needed
    ]

    notifications = [
    {
        'icon': 'fa-calendar-check',
        'title': 'Upcoming Appointment',
        'text': 'You have an appointment with John Doe at 10:00 AM today.',
        'time': '30 minutes ago',
        'unread': True
    },
    {
        'icon': 'fa-flask',
        'title': 'New Lab Results',
        'text': "Sarah Johnson's blood work results are now available.",
        'time': '1 hour ago',
        'unread': True
    },
    {
        'icon': 'fa-pills',
        'title': 'Prescription Refill Request',
        'text': 'Robert Williams has requested a refill for Metformin.',
        'time': '3 hours ago',
        'unread': False
    },
    {
        'icon': 'fa-envelope',
        'title': 'New Message',
        'text': 'You have a new message from John Doe.',
        'time': '5 hours ago',
        'unread': True
    },
    {
        'icon': 'fa-user-plus',
        'title': 'New Patient',
        'text': 'Emma Thompson has been added to your patient list.',
        'time': 'Yesterday',
        'unread': False
    },
    {
        'icon': 'fa-calendar-alt',
        'title': 'Appointment Rescheduled',
        'text': 'Michael Brown rescheduled his appointment to July 5.',
        'time': 'Yesterday',
        'unread': False
    }
    ]


    return {
        'name': user.get('name'),
        'role': user.get('role'),
        'specialization': user.get('specialization', 'Doctor'),
        'email': user.get('email'),
        'total_patients': len(patients),
        'patients': patients,
        'appointments': appointments,
        'patients_new_this_month': 8,  # Example static value, make dynamic as needed
        'todays_appointments': len(todays_appointments),
        'next_appointment': next_appointment,
        'video_consultations': len(video_consultations),
        'next_video_consult': next_video_consult,
        'todays_appointments_list': todays_appointments_list,
        'prescriptions': len([p for p in local_db.get('prescriptions', []) if p.get('doctor') == user_email]),
        'prescriptions_issued_this_week': len(prescriptions_this_week),
        'video_consultations_list': video_consultations_list,
        'prescriptions_list': prescriptions_list,
        'medications': medications,
        'analytics': analytics,
        'current_date': current_date,
        'messages': messages,
        'notifications_count': notifications_count,
        'messages_count': messages_count,
        'avatar_url': user.get('avatar_url', 'https://randomuser.me/api/portraits/women/65.jpg'),
        'phone': user.get('phone', '+1 (555) 789-0123'),
        'settings': settings,
        'hospital': user.get('hospital', 'City Medical Center'),
        'experience': user.get('experience', 12),
        'about': user.get('about', 'Dr. ' + user.get('name', '') + ' is a board-certified ' + user.get('specialization', '') + '.'),
        'specialties': user.get('specialties', [
            'Diabetes Management',
            'Thyroid Disorders',
            'Adrenal Disorders',
            'Pituitary Disorders',
            'Metabolic Disorders'
        ]),
        'languages': user.get('languages', ['English (Native)', 'Spanish (Fluent)', 'French (Basic)']),
        'notifications': notifications,
        'video_call_patient_name': 'John Doe',  # Or set dynamically based on context

    }
@app.route('/create_prescription', methods=['POST'])
@login_required(role='doctor')
def create_prescription():
    doctor_email = session['user']

    patient_email = request.form.get('patient')
    date = request.form.get('date')
    diagnosis = request.form.get('diagnosis')
    notes = request.form.get('notes')
    med_name = request.form.get('med_name')
    med_dosage = request.form.get('med_dosage')

    if not all([patient_email, date, diagnosis, med_name, med_dosage]):
        flash('Please fill all required fields.', 'danger')
        return redirect(url_for('doctor_dashboard'))

    prescription = {
        'doctor': doctor_email,
        'patient': patient_email,
        'date': date,
        'diagnosis': diagnosis,
        'notes': notes,
        'status': 'Active',
        'medications': [
            {
                'name': med_name,
                'dosage': med_dosage
            }
        ]
    }

    # Store in mock DB
    if 'prescriptions' not in local_db:
        local_db['prescriptions'] = []
    local_db['prescriptions'].append(prescription)

    flash('Prescription created successfully!', 'success')
    return redirect(url_for('doctor_dashboard'))



@app.route('/patient_dashboard')
@login_required(role='patient')
def patient_dashboard():
    user_email = session['user']
    dashboard_data = get_patient_dashboard_data(user_email)
    doctors = [
        {'email': 'drsmith@example.com', 'name': 'Dr. Smith'},
        {'email': 'drjohnson@example.com', 'name': 'Dr. Johnson'},
        {'email': 'saikiran@gmail.com', 'name': 'Dr. Sai'},
        # ... more doctors ...
    ]
    return render_template('patient_dashboard.html', **dashboard_data, doctors=doctors)

@app.route('/doctor_dashboard')
@login_required(role='doctor')
def doctor_dashboard():
    user_email = session['user']
    dashboard_data = get_doctor_dashboard_data(user_email)
    return render_template('doctor_dashboard.html', **dashboard_data)
# ...existing code...



# Book Appointment Route (to prevent BuildError)
@app.route('/book_appointment', methods=['GET', 'POST'])
@login_required(role='patient')
def book_appointment():
    if request.method == 'POST':
        doctor_email = request.form['doctor']
        date = request.form['date']
        time = request.form['time']
        title = request.form.get('title', 'Consultation')
        location = request.form.get('location', 'Office 203')
        color = request.form.get('color', '#3498db')

        # Get doctor and patient names from local_db
        doctor_name = local_db['users'].get(doctor_email, {}).get('name', 'Doctor')
        patient_email = session['user']
        patient_name = local_db['users'].get(patient_email, {}).get('name', 'Patient')

        appointment = {
            'patient': patient_email,
            'patient_name': patient_name,
            'doctor': doctor_email,
            'doctor_name': doctor_name,
            'title': title,
            'date': date,
            'time': time,
            'location': location,
            'color': color
        }
        local_db['appointments'].append(appointment)
        flash('Appointment booked successfully!', 'success')
        return redirect(url_for('patient_dashboard'))
    return render_template('book_appointment.html', user_name=session['name'])

@app.route('/add_patient', methods=['POST'])
@login_required(role='doctor')
def add_patient():
    doctor_email = session['user']
    patient_email = request.form['patient_email']
    patient_name = request.form['patient_name']

    print("Patient form received:", patient_email, patient_name)  # Debug log

    doctor = local_db['users'].get(doctor_email)
    if doctor:
        doctor.setdefault('patients', set()).add(patient_email)

    patient = local_db['users'].get(patient_email)
    if patient:
        patient.setdefault('doctors', set()).add(doctor_email)
    else:
        local_db['users'][patient_email] = {
            'name': patient_name,
            'email': patient_email,
            'role': 'patient',
            'doctors': {doctor_email}
        }

    flash('Patient added successfully!', 'success')
    return redirect(url_for('doctor_dashboard'))



dynamodb = False  # Simulate DynamoDB off (toggle for actual integration)

def get_user_table():
    # Placeholder for actual DynamoDB logic
    return None

# Route: Select role
@app.route('/')
def index():
    return render_template('index.html')






if __name__ == '__main__':
    app.run(debug=True)