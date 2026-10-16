from flask import Flask, render_template, request, redirect, session, abort, make_response, send_file
import sqlite3
import re
import os
import smtplib
import calendar
from datetime import date, datetime
from dotenv import load_dotenv
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
import secrets
import hmac
from pathlib import Path
from werkzeug.utils import secure_filename
from flask import Flask, request, redirect, session
from email.message import EmailMessage
from twilio.rest import Client
from flask import Flask, render_template, request, redirect, url_for, session, flash, Response

load_dotenv()

ADMIN_EMAIL = os.getenv("ADMIN_EMAIL")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD")

app = Flask(__name__)
# Set SECRET_KEY to a long random value in your deployment environment.
app.secret_key = os.getenv("SECRET_KEY") or "dev-only-change-this-secret-key"
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.getenv("COOKIE_SECURE", "false").lower() == "true",
    MAX_CONTENT_LENGTH=5 * 1024 * 1024,
)

DATABASE = os.getenv("DATABASE_PATH") or str(Path(__file__).resolve().parent / "database.db")
CERTIFICATE_DIR = Path(__file__).resolve().parent / "private_uploads" / "blood_group_certificates"
ALLOWED_CERTIFICATE_EXTENSIONS = {
    "pdf", "jpg", "jpeg" , "png"
}


# =========================================================
# BLOOD GROUP COMPATIBILITY
# =========================================================

BLOOD_GROUPS = [
    "A+", "A-",
    "B+", "B-",
    "AB+", "AB-",
    "O+", "O-"
]

VALID_BLOOD_GROUPS = BLOOD_GROUPS.copy()

BLOOD_COMPATIBILITY = {
    "A+": ["A+", "O+"],
    "A-": ["A-", "O-"],
    "B+": ["B+", "O+"],
    "B-": ["B-", "O-"],
    "AB+": ["A+", "A-", "B+", "B-", "AB+", "AB-", "O+", "O-"],
    "AB-": ["A-", "B-", "AB-", "O-"],
    "O+": ["O+", "O-"],
    "O-": ["O-"]
}


# =========================================================
# DATABASE CONNECTION
# =========================================================

def get_db():
    Path(DATABASE).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn


# =========================================================
# DATABASE CREATION
# =========================================================

def create_database():

    conn = get_db()
    cursor = conn.cursor()

    # USERS
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            role TEXT NOT NULL
        )
    """)

    # DONORS
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS donors (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            name TEXT NOT NULL,
            blood_group TEXT NOT NULL,
            phone TEXT NOT NULL,
            city TEXT NOT NULL,
            last_donation_date TEXT,
            age INTEGER,
            weight_kg REAL,
            consent INTEGER NOT NULL DEFAULT 0,
            verification_status TEXT NOT NULL DEFAULT 'Pending',
            certificate_filename TEXT
        )
    """)

    # RECEIVERS
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS receivers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            name TEXT NOT NULL,
            blood_group TEXT NOT NULL,
            hospital TEXT NOT NULL,
            city TEXT NOT NULL,
            consent INTEGER NOT NULL DEFAULT 0
        )
    """)

    # BLOOD REQUESTS
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS blood_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            receiver_id INTEGER,
            patient_name TEXT NOT NULL,
            blood_group TEXT NOT NULL,
            hospital TEXT NOT NULL,
            city TEXT NOT NULL,
            status TEXT DEFAULT 'Pending',
            required_by TEXT,
            urgency TEXT NOT NULL DEFAULT 'Normal',
            units INTEGER NOT NULL DEFAULT 1
        )
    """)
    
    # NOtification to donor
    
    conn.execute("""
    CREATE TABLE IF NOT EXISTS notifications (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        message TEXT NOT NULL,
        is_read INTEGER DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users(id)
        )
    """)

    # -----------------------------------------------------
    # Add last_donation_date to old donor table if missing
    # -----------------------------------------------------

    cursor.execute("PRAGMA table_info(donors)")
    donor_columns = [row["name"] for row in cursor.fetchall()]

    if "last_donation_date" not in donor_columns:
        cursor.execute("""
            ALTER TABLE donors
            ADD COLUMN last_donation_date TEXT
        """)

    # Migrate donor privacy / verification fields without deleting existing data.
    cursor.execute("PRAGMA table_info(donors)")
    donor_columns = [row["name"] for row in cursor.fetchall()]
    for column, definition in [
        ("age", "INTEGER"),
        ("weight_kg", "REAL"),
        ("consent", "INTEGER NOT NULL DEFAULT 0"),
        ("verification_status", "TEXT NOT NULL DEFAULT 'Pending'"),
        ("certificate_filename", "TEXT"),
    ]:
        if column not in donor_columns:
            cursor.execute(f"ALTER TABLE donors ADD COLUMN {column} {definition}")

    cursor.execute("PRAGMA table_info(receivers)")
    receiver_columns = [row["name"] for row in cursor.fetchall()]
    if "consent" not in receiver_columns:
        cursor.execute("ALTER TABLE receivers ADD COLUMN consent INTEGER NOT NULL DEFAULT 0")

    # -----------------------------------------------------
    # Add status to old blood_requests table if missing
    # -----------------------------------------------------

    cursor.execute("PRAGMA table_info(blood_requests)")
    request_columns = [row["name"] for row in cursor.fetchall()]

    if "status" not in request_columns:
        cursor.execute("""
            ALTER TABLE blood_requests
            ADD COLUMN status TEXT DEFAULT 'Pending'
        """)
    for column, definition in [
        ("required_by", "TEXT"),
        ("urgency", "TEXT NOT NULL DEFAULT 'Normal'"),
        ("units", "INTEGER NOT NULL DEFAULT 1"),
    ]:
        if column not in request_columns:
            cursor.execute(f"ALTER TABLE blood_requests ADD COLUMN {column} {definition}")

    # -----------------------------------------------------
    # Create default admin
    # -----------------------------------------------------

    # Provision / rotate the single environment-managed admin account.
    # Updating ADMIN_EMAIL or ADMIN_PASSWORD in .env and restarting the app
    # changes the configured admin credentials. Back up the database first.
    if ADMIN_EMAIL and ADMIN_PASSWORD:
        admin_email = ADMIN_EMAIL.strip().lower()
        password_hash = generate_password_hash(ADMIN_PASSWORD)
        configured_admin = cursor.execute(
            "SELECT id FROM users WHERE email = ? AND role = 'admin'", (admin_email,)
        ).fetchone()
        if configured_admin:
            cursor.execute(
                "UPDATE users SET password = ? WHERE id = ?",
                (password_hash, configured_admin["id"])
            )
        else:
            legacy_admin = cursor.execute(
                "SELECT id FROM users WHERE role = 'admin' AND email = 'ADMIN_EMAIL'"
            ).fetchone()
            all_admins = cursor.execute(
                "SELECT id FROM users WHERE role = 'admin' ORDER BY id"
            ).fetchall()
            # Rename the known placeholder or the sole existing admin account.
            target = legacy_admin or (all_admins[0] if len(all_admins) == 1 else None)
            if target:
                cursor.execute(
                    "UPDATE users SET name = ?, email = ?, password = ?, role = 'admin' WHERE id = ?",
                    ("Admin", admin_email, password_hash, target["id"])
                )
            else:
                cursor.execute("""
                    INSERT INTO users (name, email, password, role)
                    VALUES (?, ?, ?, ?)
                """, ("Admin", admin_email, password_hash, "admin"))

    conn.commit()
    conn.close()


# Basic session-backed CSRF protection for all HTML form POST requests.
@app.context_processor
def inject_csrf_token():
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_urlsafe(32)
    return {"csrf_token": session["csrf_token"]}


@app.before_request
def protect_post_requests():
    if request.method == "POST":
        expected = session.get("csrf_token", "")
        submitted = request.form.get("csrf_token", "")
        if not expected or not submitted or not hmac.compare_digest(expected, submitted):
            abort(400, description="Invalid or missing security token. Refresh the page and try again.")


@app.after_request
def add_security_headers(response):
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    if request.is_secure:
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    return response


# =========================================================
# VALIDATION FUNCTIONS
# =========================================================

def valid_name_or_city(value):

    if not value:
        return False

    value = value.strip()

    if not value:
        return False

    # Characters and spaces only
    return all(
        char.isalpha() or char.isspace()
        for char in value
    )


def valid_phone(phone):

    if not phone:
        return False

    return bool(
        re.fullmatch(r"[0-9]{10}", phone.strip())
    )


def valid_blood_group(blood_group):

    return blood_group in VALID_BLOOD_GROUPS

def send_donor_email(email, name, status):
      try:
        smtp_email = os.getenv("SMTP_EMAIL")
        smtp_password = os.getenv("SMTP_APP_PASSWORD")

        if not smtp_email or not smtp_password:
            print("Email credentials are missing in .env")
            return False

        message = EmailMessage()
        message["Subject"] = f"Donor Registration {status}"
        message["From"] = smtp_email
        message["To"] = email

        if status == "Approved":
            message.set_content(
                f"""Hello {name},

Your blood donor registration has been APPROVED by the admin.

Thank you for registering as a blood donor.
Your contribution can help save lives.

Regards,
Emergency Blood Donor and Receiver System"""
            )
        elif status == "Rejected":
            message.set_content(
                f"""Hello {name},

Your blood donor registration has been REJECTED by the admin.

Please contact the administrator for more information.

Regards,
Emergency Blood Donor and Receiver System"""
            )
        else:
            message.set_content(
                f"""Hello {name},

Your donor registration status is now: {status}.

Regards,
Emergency Blood Donor and Receiver System"""
            )

        with smtplib.SMTP("smtp.gmail.com", 587) as server:
            server.starttls()
            server.login(smtp_email, smtp_password)
            server.send_message(message)

        print(f"Email notification sent to {email}")
        return True

      except Exception as e:
        print(f"Email notification failed: {e}")
        return False


# =========================================================
# DATE FUNCTIONS
# =========================================================

def add_months(original_date, months):

    month = original_date.month - 1 + months

    year = original_date.year + month // 12

    month = month % 12 + 1

    day = min(
        original_date.day,
        calendar.monthrange(year, month)[1]
    )

    return original_date.replace(
        year=year,
        month=month,
        day=day
    )


def donation_is_eligible(last_donation_date):

    if not last_donation_date:
        return False

    try:
        donation_date = datetime.strptime(
            last_donation_date,
            "%Y-%m-%d"
        ).date()
    except ValueError:
        return False

    today = date.today()

    # Future date is invalid
    if donation_date > today:
        return False

    eligible_date = add_months(
        donation_date,
        3
    )

    return today >= eligible_date


# =========================================================
# CURRENT USER
# =========================================================

def get_current_user():

    if "user_id" not in session:
        return None

    conn = get_db()

    user = conn.execute("""
        SELECT *
        FROM users
        WHERE id = ?
    """, (session["user_id"],)).fetchone()

    conn.close()

    return user


@app.route("/privacy")
def privacy_notice():
    return render_template("privacy.html")


# =========================================================
# HOME
# =========================================================

@app.route("/")
def index():

    conn = get_db()

    donor_count = conn.execute(
        "SELECT COUNT(*) FROM donors"
    ).fetchone()[0]

    receiver_count = conn.execute(
        "SELECT COUNT(*) FROM receivers"
    ).fetchone()[0]

    request_count = conn.execute(
        "SELECT COUNT(*) FROM blood_requests"
    ).fetchone()[0]

    conn.close()

    return render_template(
        "index.html",
        donor_count=donor_count,
        receiver_count=receiver_count,
        request_count=request_count
    )


# =========================================================
# DONOR REGISTRATION
# =========================================================

@app.route("/donor-register", methods=["GET", "POST"])
def donor_register():

    if request.method == "POST":

        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip()

        if not re.fullmatch(r"[a-z0-9._%+-]+@gmail\.com", email):
                return render_template(
                    "donor_register.html",
                    error="Enter a valid lowercase Gmail address ending with @gmail.com."
                )
        password = request.form.get("password", "")
        blood_group = request.form.get("blood_group", "").strip()
        phone = request.form.get("phone", "").strip()
        city = request.form.get("city", "").strip()
        last_donation_date = request.form.get("last_donation_date", "").strip()
        consent = request.form.get("consent") == "yes"
        certificate = request.files.get("blood_group_certificate")
        try:
            age = int(request.form.get("age", ""))
            weight_kg = float(request.form.get("weight_kg", ""))
        except (TypeError, ValueError):
            return render_template("donor_register.html", error="Enter a valid age and weight.")

        # Validation
        if not consent:
            return render_template("donor_register.html", error="Please provide consent to register.")
        if not 18 <= age <= 120:
            return render_template("donor_register.html", error="Donor age must be at least 18 years. Final eligibility must be decided by a blood bank.")
        if not 50 <= weight_kg <= 300:
            return render_template("donor_register.html", error="Donor weight must be at least 50 kg for this project. Final eligibility must be decided by a blood bank.")
        if not certificate or not certificate.filename:
            return render_template("donor_register.html", error="Please upload your blood group certificate from a hospital or authorized lab.")
        original_name = secure_filename(certificate.filename)
        extension = original_name.rsplit(".", 1)[-1].lower() if "." in original_name else ""
        if extension not in ALLOWED_CERTIFICATE_EXTENSIONS:
            return render_template("donor_register.html", error="Certificate must be a PDF, PNG, JPG, or JPEG file.")
        if len(password) < 8:
            return render_template("donor_register.html", error="Password must contain at least 8 characters.")
        if not valid_name_or_city(name):
            return render_template(
                "donor_register.html",
                error="Name must contain characters and spaces only."
            )

        if not valid_blood_group(blood_group):
            return render_template(
                "donor_register.html",
                error="Please select a valid blood group."
            )

        if not valid_phone(phone):
            return render_template(
                "donor_register.html",
                error="Phone number must contain exactly 10 digits."
            )

        if not valid_name_or_city(city):
            return render_template(
                "donor_register.html",
                error="City must contain characters and spaces only."
            )

        if not last_donation_date:
            return render_template(
                "donor_register.html",
                error="Please enter your last blood donation date."
            )

        try:
            donation_date = datetime.strptime(
                last_donation_date,
                "%Y-%m-%d"
            ).date()

            if donation_date > date.today():
                return render_template(
                    "donor_register.html",
                    error="Last donation date cannot be in the future."
                )

        except ValueError:
            return render_template(
                "donor_register.html",
                error="Invalid donation date."
            )

        conn = get_db()

        # Check email
        existing_user = conn.execute("""
            SELECT *
            FROM users
            WHERE email = ?
        """, (email,)).fetchone()

        if existing_user:
            conn.close()

            return render_template(
                "donor_register.html",
                error="Email already registered."
            )

        # Save certificate privately (not inside the public static folder).
        CERTIFICATE_DIR.mkdir(parents=True, exist_ok=True)
        certificate_filename = f"donor_certificate_{secrets.token_hex(16)}.{extension}"
        certificate_path = CERTIFICATE_DIR / certificate_filename
        try:
            certificate.save(certificate_path)
        except OSError:
            conn.close()
            return render_template("donor_register.html", error="Could not save the certificate. Please try again.")

        # Create user
        cursor = conn.cursor()

        cursor.execute("""
            INSERT INTO users
            (name, email, password, role)
            VALUES (?, ?, ?, ?)
        """, (
            name,
            email.lower(),
            generate_password_hash(password),
            "donor"
        ))

        user_id = cursor.lastrowid

        # Create donor
        cursor.execute("""
            INSERT INTO donors
            (
                user_id,
                name,
                blood_group,
                phone,
                city,
                last_donation_date,
                age,
                weight_kg,
                consent,
                verification_status,
                certificate_filename
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            user_id,
            name,
            blood_group,
            phone,
            city,
            last_donation_date,
            age,
            weight_kg,
            1,
            "Pending",
            certificate_filename
        ))

        conn.commit()
        conn.close()

        session["user_id"] = user_id
        session["role"] = "donor"

        return redirect("/donor-dashboard")

    return render_template(
        "donor_register.html"
    )


# =========================================================
# RECEIVER REGISTRATION
# =========================================================

@app.route("/receiver-register", methods=["GET", "POST"])
def receiver_register():

    if request.method == "POST":

        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip()

        if not re.fullmatch(r"[a-z0-9._%+-]+@gmail\.com", email):
            return render_template(
                "donor_register.html",
                error="Enter a valid lowercase Gmail address ending with @gmail.com."
            )
        password = request.form.get("password", "")
        blood_group = request.form.get("blood_group", "").strip()
        hospital = request.form.get("hospital", "").strip()
        city = request.form.get("city", "").strip()
        consent = request.form.get("consent") == "yes"

        if not consent:
            return render_template("receiver_register.html", error="Please provide consent to register.")
        if len(password) < 8:
            return render_template("receiver_register.html", error="Password must contain at least 8 characters.")
        if not valid_name_or_city(name):
            return render_template(
                "receiver_register.html",
                error="Name must contain characters and spaces only."
            )

        if not valid_blood_group(blood_group):
            return render_template(
                "receiver_register.html",
                error="Please select a valid blood group."
            )

        if not hospital:
            return render_template(
                "receiver_register.html",
                error="Please enter hospital name."
            )

        if not valid_name_or_city(city):
            return render_template(
                "receiver_register.html",
                error="City must contain characters and spaces only."
            )

        conn = get_db()

        existing_user = conn.execute("""
            SELECT *
            FROM users
            WHERE email = ?
        """, (email,)).fetchone()

        if existing_user:
            conn.close()

            return render_template(
                "receiver_register.html",
                error="Email already registered."
            )

        cursor = conn.cursor()

        cursor.execute("""
            INSERT INTO users
            (name, email, password, role)
            VALUES (?, ?, ?, ?)
        """, (
            name,
            email.lower(),
            generate_password_hash(password),
            "receiver"
        ))

        user_id = cursor.lastrowid

        cursor.execute("""
            INSERT INTO receivers
            (
                user_id,
                name,
                blood_group,
                hospital,
                city,
                consent
            )
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            user_id,
            name,
            blood_group,
            hospital,
            city,
            1
        ))

        conn.commit()
        conn.close()

        session["user_id"] = user_id
        session["role"] = "receiver"

        return redirect("/receiver-dashboard")

    return render_template(
        "receiver_register.html"
    )


# =========================================================
# LOGIN
# =========================================================

@app.route("/login", methods=["GET", "POST"])
def login():

    if request.method == "POST":

        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        conn = get_db()

        user = conn.execute("SELECT * FROM users WHERE email = ?", (email.lower(),)).fetchone()

        password_ok = False
        if user:
            stored_password = user["password"]
            try:
                password_ok = check_password_hash(stored_password, password)
            except (ValueError, TypeError):
                password_ok = False
            # One-time upgrade for accounts created before password hashing.
            if not password_ok and hmac.compare_digest(str(stored_password), password):
                password_ok = True
                conn.execute("UPDATE users SET password = ? WHERE id = ?", (generate_password_hash(password), user["id"]))
                conn.commit()

        conn.close()

        if not user or not password_ok:
            return render_template("login.html", error="Invalid email or password.")

        session["user_id"] = user["id"]
        session["role"] = user["role"]

        if user["role"] == "admin":
            return redirect("/admin")

        if user["role"] == "donor":
            return redirect("/donor-dashboard")

        if user["role"] == "receiver":
            return redirect("/receiver-dashboard")

    return render_template("login.html")


# =========================================================
# LOGOUT
# =========================================================

@app.route("/logout")
def logout():

    session.clear()

    return redirect("/")


# =========================================================
# DONOR DASHBOARD
# =========================================================

@app.route("/donor-dashboard")
def donor_dashboard():

    user = get_current_user()

    if not user or user["role"] != "donor":
        return redirect("/login")

    conn = get_db()

    donor = conn.execute("""
        SELECT *
        FROM donors
        WHERE user_id = ?
    """, (user["id"],)).fetchone()

    if not donor:
        conn.close()
        return redirect("/logout")

    notifications = conn.execute("""
        SELECT id, message, is_read, created_at
        FROM notifications
        WHERE user_id = ?
        ORDER BY id DESC
    """, (user["id"],)).fetchall()

    conn.close()

    return render_template(
        "donor_dashboard.html",
        donor=donor,
        notifications=notifications
    )

# =========================================================
# RECEIVER DASHBOARD
# =========================================================

@app.route("/receiver-dashboard")
def receiver_dashboard():

    user = get_current_user()

    if not user or user["role"] != "receiver":
        return redirect("/login")

    conn = get_db()

    receiver = conn.execute("""
        SELECT *
        FROM receivers
        WHERE user_id = ?
    """, (user["id"],)).fetchone()

    if not receiver:
        conn.close()
        return redirect("/logout")

    compatible_groups = BLOOD_COMPATIBILITY.get(
        receiver["blood_group"],
        []
    )

    placeholders = ",".join(
        ["?"] * len(compatible_groups)
    )

    donors = conn.execute(f"""
        SELECT *
        FROM donors
        WHERE blood_group IN ({placeholders})
          AND verification_status = 'Approved'
        ORDER BY
            CASE
                WHEN city = ? THEN 0
                ELSE 1
            END,
            id DESC
    """, (
        *compatible_groups,
        receiver["city"]
    )).fetchall()

    requests = conn.execute("""
        SELECT *
        FROM blood_requests
        WHERE receiver_id = ?
        ORDER BY id DESC
    """, (
        receiver["id"],
    )).fetchall()

    conn.close()

    eligible_donors = [
        donor
        for donor in donors
        if donation_is_eligible(
            donor["last_donation_date"]
        )
    ]

    same_city_donors = [
        donor
        for donor in eligible_donors
        if donor["city"].lower()
        == receiver["city"].lower()
    ]

    other_city_donors = [
        donor
        for donor in eligible_donors
        if donor["city"].lower()
        != receiver["city"].lower()
    ]

    return render_template(
        "receiver_dashboard.html",
        receiver=receiver,
        compatible_groups=compatible_groups,
        same_city_donors=same_city_donors,
        other_city_donors=other_city_donors,
        requests=requests,
        today=date.today().isoformat()
    )


# =========================================================
# CREATE BLOOD REQUEST
# =========================================================

@app.route("/create-request", methods=["POST"])
def create_request():

    user = get_current_user()

    if not user or user["role"] != "receiver":
        return redirect("/login")

    patient_name = request.form.get(
        "patient_name",
        ""
    ).strip()

    blood_group = request.form.get(
        "blood_group",
        ""
    ).strip()

    hospital = request.form.get(
        "hospital",
        ""
    ).strip()

    city = request.form.get("city", "").strip()
    required_by = request.form.get("required_by", "").strip()
    urgency = request.form.get("urgency", "Normal").strip()
    try:
        units = int(request.form.get("units", "1"))
    except ValueError:
        return redirect("/receiver-dashboard")

    if urgency not in ("Emergency", "Urgent", "Normal") or not 1 <= units <= 10:
        return redirect("/receiver-dashboard")
    try:
        required_date = datetime.strptime(required_by, "%Y-%m-%d").date()
        if required_date < date.today():
            return redirect("/receiver-dashboard")
    except ValueError:
        return redirect("/receiver-dashboard")

    if not valid_name_or_city(patient_name):
        return redirect("/receiver-dashboard")

    if not valid_blood_group(blood_group):
        return redirect("/receiver-dashboard")

    if not hospital:
        return redirect("/receiver-dashboard")

    if not valid_name_or_city(city):
        return redirect("/receiver-dashboard")

    conn = get_db()

    receiver = conn.execute("""
        SELECT *
        FROM receivers
        WHERE user_id = ?
    """, (
        user["id"],
    )).fetchone()

    if receiver:

        conn.execute("""
            INSERT INTO blood_requests
            (
                receiver_id,
                patient_name,
                blood_group,
                hospital,
                city,
                status,
                required_by,
                urgency,
                units
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            receiver["id"],
            patient_name,
            blood_group,
            hospital,
            city,
            "Pending",
            required_by,
            urgency,
            units
        ))

        conn.commit()

    conn.close()

    return redirect("/receiver-dashboard")


# =========================================================
# SEARCH DONORS
# =========================================================

@app.route("/search")
def search():

    current_user = get_current_user()
    if not current_user or current_user["role"] not in ("receiver", "admin"):
        return redirect("/login")

    blood_group = request.args.get(
        "blood_group",
        ""
    ).strip()

    city = request.args.get(
        "city",
        ""
    ).strip()

    donors = []
    compatible_groups = []

    if blood_group in VALID_BLOOD_GROUPS:

        compatible_groups = BLOOD_COMPATIBILITY.get(
            blood_group,
            []
        )

        conn = get_db()

        placeholders = ",".join(
            ["?"] * len(compatible_groups)
        )

        donors = conn.execute(f"""
            SELECT *
            FROM donors
            WHERE blood_group IN ({placeholders})
              AND verification_status = 'Approved'
            ORDER BY
                CASE
                    WHEN city = ? THEN 0
                    ELSE 1
                END,
                id DESC
        """, (
            *compatible_groups,
            city
        )).fetchall()

        conn.close()

        donors = [
            donor
            for donor in donors
            if donation_is_eligible(
                donor["last_donation_date"]
            )
        ]

    same_city_donors = [
        donor
        for donor in donors
        if city and
        donor["city"].lower() == city.lower()
    ]

    other_city_donors = [
        donor
        for donor in donors
        if not city or
        donor["city"].lower() != city.lower()
    ]

    return render_template(
        "search.html",
        donors=donors,
        same_city_donors=same_city_donors,
        other_city_donors=other_city_donors,
        blood_group=blood_group,
        city=city,
        compatible_groups=compatible_groups
    )


# =========================================================
# PROFILE
# =========================================================

@app.route("/profile")
def profile():

    user = get_current_user()

    if not user:
        return redirect("/login")

    conn = get_db()

    donor = None
    receiver = None

    if user["role"] == "donor":

        donor = conn.execute("""
            SELECT *
            FROM donors
            WHERE user_id = ?
        """, (
            user["id"],
        )).fetchone()

    elif user["role"] == "receiver":

        receiver = conn.execute("""
            SELECT *
            FROM receivers
            WHERE user_id = ?
        """, (
            user["id"],
        )).fetchone()

    conn.close()

    return render_template(
        "profile.html",
        user=user,
        donor=donor,
        receiver=receiver
    )


# =========================================================
# ALL REQUESTS
# =========================================================

@app.route("/requests")
def requests():

    user = get_current_user()

    if not user:
        return redirect("/login")

    conn = get_db()

    if user["role"] == "admin":
        blood_requests = conn.execute("SELECT * FROM blood_requests ORDER BY id DESC").fetchall()
    elif user["role"] == "receiver":
        receiver = conn.execute("SELECT id FROM receivers WHERE user_id = ?", (user["id"],)).fetchone()
        blood_requests = conn.execute(
            "SELECT * FROM blood_requests WHERE receiver_id = ? ORDER BY id DESC",
            (receiver["id"],)
        ).fetchall() if receiver else []
    else:
        conn.close()
        return redirect("/donor-dashboard")

    conn.close()

    return render_template(
        "requests.html",
        requests=blood_requests
    )


# =========================================================
# ADMIN DASHBOARD
# =========================================================

@app.route("/admin")
def admin():

    user = get_current_user()

    if not user or user["role"] != "admin":
        return redirect("/login")

    conn = get_db()

    donors = conn.execute("""
        SELECT *
        FROM donors
        ORDER BY id DESC
    """).fetchall()

    receivers = conn.execute("""
        SELECT *
        FROM receivers
        ORDER BY id DESC
    """).fetchall()

    blood_requests = conn.execute("""
        SELECT *
        FROM blood_requests
        ORDER BY id DESC
    """).fetchall()

    donor_count = len(donors)
    receiver_count = len(receivers)
    request_count = len(blood_requests)

    conn.close()

    return render_template(
        "admin.html",
        donors=donors,
        receivers=receivers,
        blood_requests=blood_requests,
        donor_count=donor_count,
        receiver_count=receiver_count,
        request_count=request_count
    )


@app.route("/admin/donor-certificate/<int:donor_id>")
def view_donor_certificate(donor_id):
    user = get_current_user()
    if not user or user["role"] != "admin":
        return redirect("/login")

    conn = get_db()
    donor = conn.execute(
        "SELECT certificate_filename FROM donors WHERE id = ?", (donor_id,)
    ).fetchone()
    conn.close()

    if not donor or not donor["certificate_filename"]:
        abort(404)

    filename = donor["certificate_filename"]
    # Only generated server-side filenames are accepted from the database.
    if Path(filename).name != filename:
        abort(404)
    file_path = CERTIFICATE_DIR / filename
    if not file_path.is_file():
        abort(404)
    return send_file(file_path, as_attachment=False, download_name=filename)



@app.route("/admin/donor-verification/<int:donor_id>/<action>", methods=["POST"])
def donor_verification(donor_id, action):
    user = get_current_user()

    if not user or user["role"] != "admin":
        return redirect("/login")

    status_map = {
        "approve": "Approved",
        "reject": "Rejected",
        "pending": "Pending"
    }

    if action not in status_map:
        abort(400)

    conn = get_db()

    donor = conn.execute("""
        SELECT donors.user_id, donors.name
        FROM donors
        WHERE donors.id = ?
    """, (donor_id,)).fetchone()

    if not donor:
        conn.close()
        abort(404)

    status = status_map[action]

    conn.execute(
        "UPDATE donors SET verification_status = ? WHERE id = ?",
        (status, donor_id)
    )

    # Create website notification after Approve or Reject
    if action in ("approve", "reject"):
        message = (
            f"Your donor registration has been {status.lower()} "
            f"by the admin."
        )

        conn.execute("""
            INSERT INTO notifications (user_id, message)
            VALUES (?, ?)
        """, (donor["user_id"], message))

    conn.commit()
    conn.close()

    return redirect("/admin")

# =========================================================
# ADMIN - EDIT DONOR
# =========================================================

@app.route("/admin/edit-donor/<int:donor_id>", methods=["GET", "POST"])
def edit_donor(donor_id):

    user = get_current_user()

    if not user or user["role"] != "admin":
        return redirect("/login")

    conn = get_db()

    donor = conn.execute(
        "SELECT * FROM donors WHERE id = ?",
        (donor_id,)
    ).fetchone()

    if not donor:
        conn.close()
        return redirect("/admin")

    if request.method == "POST":

        name = request.form.get("name", "").strip()
        blood_group = request.form.get("blood_group", "").strip()
        phone = request.form.get("phone", "").strip()
        city = request.form.get("city", "").strip()
        last_donation_date = request.form.get(
            "last_donation_date", ""
        ).strip()

        error = None

        if not valid_name_or_city(name):
            error = "Name must contain characters and spaces only."

        elif blood_group not in BLOOD_GROUPS:
            error = "Please select a valid blood group."

        elif not valid_phone(phone):
            error = "Phone number must contain exactly 10 digits."

        elif not valid_name_or_city(city):
            error = "City must contain characters and spaces only."

        else:
            try:
                donation_date = datetime.strptime(
                    last_donation_date, "%Y-%m-%d"
                ).date()

                if donation_date > date.today():
                    error = "Donation date cannot be in the future."

            except ValueError:
                error = "Please enter a valid donation date."

        if error:
            conn.close()
            return render_template(
                "edit_donor.html",
                donor=donor,
                blood_groups=BLOOD_GROUPS,
                error=error
            )

        conn.execute("""
            UPDATE donors
            SET name = ?,
                blood_group = ?,
                phone = ?,
                city = ?,
                last_donation_date = ?
            WHERE id = ?
        """, (
            name,
            blood_group,
            phone,
            city,
            last_donation_date,
            donor_id
        ))

        conn.execute("""
            UPDATE users
            SET name = ?
            WHERE id = ?
        """, (
            name,
            donor["user_id"]
        ))

        conn.commit()
        conn.close()

        return redirect("/admin")

    conn.close()

    return render_template(
        "edit_donor.html",
        donor=donor,
        blood_groups=BLOOD_GROUPS
    )


# =========================================================
# ADMIN - DELETE DONOR
# =========================================================

@app.route("/admin/delete-donor/<int:donor_id>", methods=["POST"])
def delete_donor(donor_id):

    user = get_current_user()

    if not user or user["role"] != "admin":
        return redirect("/login")

    conn = get_db()

    donor = conn.execute("""
        SELECT user_id
        FROM donors
        WHERE id = ?
    """, (
        donor_id,
    )).fetchone()

    if donor:

        conn.execute("""
            DELETE FROM donors
            WHERE id = ?
        """, (
            donor_id,
        ))

        conn.execute("""
            DELETE FROM users
            WHERE id = ?
            AND role = 'donor'
        """, (
            donor["user_id"],
        ))

        conn.commit()

    conn.close()

    return redirect("/admin")


# =========================================================
# ADMIN - EDIT RECEIVER
# =========================================================

@app.route("/admin/edit-receiver/<int:receiver_id>", methods=["GET", "POST"])
def edit_receiver(receiver_id):

    user = get_current_user()

    if not user or user["role"] != "admin":
        return redirect("/login")

    conn = get_db()

    receiver = conn.execute(
        "SELECT * FROM receivers WHERE id = ?",
        (receiver_id,)
    ).fetchone()

    if not receiver:
        conn.close()
        return redirect("/admin")

    if request.method == "POST":

        name = request.form.get("name", "").strip()
        blood_group = request.form.get("blood_group", "").strip()
        hospital = request.form.get("hospital", "").strip()
        city = request.form.get("city", "").strip()

        error = None

        if not valid_name_or_city(name):
            error = "Name must contain characters and spaces only."

        elif blood_group not in BLOOD_GROUPS:
            error = "Please select a valid blood group."

        elif not hospital:
            error = "Hospital name is required."

        elif not valid_name_or_city(city):
            error = "City must contain characters and spaces only."

        if error:
            conn.close()
            return render_template(
                "edit_receiver.html",
                receiver=receiver,
                blood_groups=BLOOD_GROUPS,
                error=error
            )

        conn.execute("""
            UPDATE receivers
            SET name = ?,
                blood_group = ?,
                hospital = ?,
                city = ?
            WHERE id = ?
        """, (
            name,
            blood_group,
            hospital,
            city,
            receiver_id
        ))

        conn.execute("""
            UPDATE users
            SET name = ?
            WHERE id = ?
        """, (
            name,
            receiver["user_id"]
        ))

        conn.commit()
        conn.close()

        return redirect("/admin")

    conn.close()

    return render_template(
        "edit_receiver.html",
        receiver=receiver,
        blood_groups=BLOOD_GROUPS
    )


# =========================================================
# ADMIN - DELETE RECEIVER
# =========================================================

@app.route("/admin/delete-receiver/<int:receiver_id>", methods=["POST"])
def delete_receiver(receiver_id):

    user = get_current_user()

    if not user or user["role"] != "admin":
        return redirect("/login")

    conn = get_db()

    receiver = conn.execute("""
        SELECT user_id
        FROM receivers
        WHERE id = ?
    """, (
        receiver_id,
    )).fetchone()

    if receiver:

        # Delete receiver's blood requests first
        conn.execute("""
            DELETE FROM blood_requests
            WHERE receiver_id = ?
        """, (
            receiver_id,
        ))

        # Delete receiver
        conn.execute("""
            DELETE FROM receivers
            WHERE id = ?
        """, (
            receiver_id,
        ))

        # Delete receiver login
        conn.execute("""
            DELETE FROM users
            WHERE id = ?
            AND role = 'receiver'
        """, (
            receiver["user_id"],
        ))

        conn.commit()

    conn.close()

    return redirect("/admin")


# =========================================================
# ADMIN - DELETE BLOOD REQUEST
# =========================================================

@app.route("/admin/delete-request/<int:request_id>", methods=["POST"])
def delete_request(request_id):

    user = get_current_user()

    if not user or user["role"] != "admin":
        return redirect("/login")

    conn = get_db()

    conn.execute("""
        DELETE FROM blood_requests
        WHERE id = ?
    """, (
        request_id,
    ))

    conn.commit()
    conn.close()

    return redirect("/admin")


# =========================================================
# ADMIN - MARK REQUEST AS DELIVERED
# =========================================================

@app.route("/admin/deliver-request/<int:request_id>", methods=["POST"])
def deliver_request(request_id):

    user = get_current_user()

    if not user or user["role"] != "admin":
        return redirect("/login")

    conn = get_db()

    conn.execute("""
        UPDATE blood_requests
        SET status = ?
        WHERE id = ?
    """, (
        "Successfully Delivered",
        request_id
    ))

    conn.commit()
    conn.close()

    return redirect("/admin")

# =========================================================
# Get Notifications
# =========================================================
from email.message import EmailMessage
from dotenv import load_dotenv
from twilio.rest import Client

load_dotenv()

# =========================================================
# START APPLICATION
# =========================================================

# Ensure schema/migrations run when launched by a WSGI server as well.
create_database()

@app.route("/sitemap.xml")
def sitemap():
    sitemap_xml = """<?xml version="1.0" encoding="UTF-8"?>
    <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
        <url>
            <loc>https://emergency-blood-referral-system.onrender.com/</loc>
        </url>
    </urlset>
    """
    return Response(sitemap_xml, mimetype="application/xml")


if __name__ == "__main__":
    app.run(debug=True)