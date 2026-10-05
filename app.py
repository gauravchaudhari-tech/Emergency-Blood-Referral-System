from flask import Flask, render_template, request, redirect, session
import sqlite3
import re
import os
import calendar
from datetime import date, datetime
from dotenv import load_dotenv

load_dotenv()

ADMIN_EMAIL = os.getenv("ADMIN_EMAIL")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD")

app = Flask(__name__)
app.secret_key = "smart_blood_system_secret_key"

DATABASE = "database.db"


# =========================================================
# BLOOD GROUP COMPATIBILITY
# =========================================================

VALID_BLOOD_GROUPS = [
    "A+", "A-",
    "B+", "B-",
    "AB+", "AB-",
    "O+", "O-"
]

BLOOD_COMPATIBILITY = {
    "A+": ["A+", "O+"],
    "A-": ["A-", "O-"],

    "B+": ["B+", "O+"],
    "B-": ["B-", "O-"],

    "AB+": [
        "A+", "A-",
        "B+", "B-",
        "AB+", "AB-",
        "O+", "O-"
    ],

    "AB-": [
        "A-", "B-",
        "AB-", "O-"
    ],

    "O+": ["O+", "O-"],
    "O-": ["O-"]
}


# =========================================================
# DATABASE CONNECTION
# =========================================================

def get_db():
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
            last_donation_date TEXT
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
            city TEXT NOT NULL
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
            status TEXT DEFAULT 'Pending'
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

    # -----------------------------------------------------
    # Create default admin
    # -----------------------------------------------------

    cursor.execute("""
        SELECT * FROM users
        WHERE email = ?
    """, (ADMIN_EMAIL,))

    admin = cursor.fetchone()

    if not admin:
        cursor.execute("""
            INSERT INTO users
            (name, email, password, role)
            VALUES (?, ?, ?, ?)
        """, (
            "Admin",
            "ADMIN_EMAIL",
            "ADMIN_PASSWORD",
            "admin"
        ))

    conn.commit()
    conn.close()


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
        password = request.form.get("password", "")
        blood_group = request.form.get("blood_group", "").strip()
        phone = request.form.get("phone", "").strip()
        city = request.form.get("city", "").strip()
        last_donation_date = request.form.get(
            "last_donation_date",
            ""
        ).strip()

        # Validation
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

        # Create user
        cursor = conn.cursor()

        cursor.execute("""
            INSERT INTO users
            (name, email, password, role)
            VALUES (?, ?, ?, ?)
        """, (
            name,
            email,
            password,
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
                last_donation_date
            )
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            user_id,
            name,
            blood_group,
            phone,
            city,
            last_donation_date
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
        password = request.form.get("password", "")
        blood_group = request.form.get("blood_group", "").strip()
        hospital = request.form.get("hospital", "").strip()
        city = request.form.get("city", "").strip()

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
            email,
            password,
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
                city
            )
            VALUES (?, ?, ?, ?, ?)
        """, (
            user_id,
            name,
            blood_group,
            hospital,
            city
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

        email = request.form.get("email", "").strip()
        password = request.form.get("password", "")

        conn = get_db()

        user = conn.execute("""
            SELECT *
            FROM users
            WHERE email = ?
            AND password = ?
        """, (
            email,
            password
        )).fetchone()

        conn.close()

        if not user:
            return render_template(
                "login.html",
                error="Invalid email or password."
            )

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

    conn.close()

    return render_template(
        "donor_dashboard.html",
        donor=donor
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
        requests=requests
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

    city = request.form.get(
        "city",
        ""
    ).strip()

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
                status
            )
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            receiver["id"],
            patient_name,
            blood_group,
            hospital,
            city,
            "Pending"
        ))

        conn.commit()

    conn.close()

    return redirect("/receiver-dashboard")


# =========================================================
# SEARCH DONORS
# =========================================================

@app.route("/search")
def search():

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

    blood_requests = conn.execute("""
        SELECT *
        FROM blood_requests
        ORDER BY id DESC
    """).fetchall()

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


# =========================================================
# ADMIN - EDIT DONOR
# =========================================================

@app.route("/admin/edit-donor/<int:donor_id>", methods=["GET", "POST"])
def edit_donor(donor_id):

    user = get_current_user()

    if not user or user["role"] != "admin":
        return redirect("/login")

    conn = get_db()

    donor = conn.execute("""
        SELECT *
        FROM donors
        WHERE id = ?
    """, (
        donor_id,
    )).fetchone()

    if not donor:
        conn.close()
        return redirect("/admin")

    if request.method == "POST":

        name = request.form.get(
            "name",
            ""
        ).strip()

        blood_group = request.form.get(
            "blood_group",
            ""
        ).strip()

        phone = request.form.get(
            "phone",
            ""
        ).strip()

        city = request.form.get(
            "city",
            ""
        ).strip()

        last_donation_date = request.form.get(
            "last_donation_date",
            ""
        ).strip()

        if not valid_name_or_city(name):
            conn.close()

            return render_template(
                "edit_donor.html",
                donor=donor,
                error="Name must contain characters and spaces only."
            )

        if not valid_blood_group(blood_group):
            conn.close()

            return render_template(
                "edit_donor.html",
                donor=donor,
                error="Invalid blood group."
            )

        if not valid_phone(phone):
            conn.close()

            return render_template(
                "edit_donor.html",
                donor=donor,
                error="Phone number must contain exactly 10 digits."
            )

        if not valid_name_or_city(city):
            conn.close()

            return render_template(
                "edit_donor.html",
                donor=donor,
                error="City must contain characters and spaces only."
            )

        try:

            donation_date = datetime.strptime(
                last_donation_date,
                "%Y-%m-%d"
            ).date()

            if donation_date > date.today():
                conn.close()

                return render_template(
                    "edit_donor.html",
                    donor=donor,
                    error="Donation date cannot be in the future."
                )

        except ValueError:

            conn.close()

            return render_template(
                "edit_donor.html",
                donor=donor,
                error="Invalid donation date."
            )

        conn.execute("""
            UPDATE donors
            SET
                name = ?,
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

        # Update name in users table also
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
        donor=donor
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

    receiver = conn.execute("""
        SELECT *
        FROM receivers
        WHERE id = ?
    """, (
        receiver_id,
    )).fetchone()

    if not receiver:
        conn.close()
        return redirect("/admin")

    if request.method == "POST":

        name = request.form.get(
            "name",
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

        city = request.form.get(
            "city",
            ""
        ).strip()

        if not valid_name_or_city(name):

            conn.close()

            return render_template(
                "edit_receiver.html",
                receiver=receiver,
                error="Name must contain characters and spaces only."
            )

        if not valid_blood_group(blood_group):

            conn.close()

            return render_template(
                "edit_receiver.html",
                receiver=receiver,
                error="Invalid blood group."
            )

        if not hospital:

            conn.close()

            return render_template(
                "edit_receiver.html",
                receiver=receiver,
                error="Hospital name is required."
            )

        if not valid_name_or_city(city):

            conn.close()

            return render_template(
                "edit_receiver.html",
                receiver=receiver,
                error="City must contain characters and spaces only."
            )

        conn.execute("""
            UPDATE receivers
            SET
                name = ?,
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

        # Update name in users table
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
        receiver=receiver
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
# START APPLICATION
# =========================================================

if __name__ == "__main__":

    create_database()

    app.run(
        debug=True
    )