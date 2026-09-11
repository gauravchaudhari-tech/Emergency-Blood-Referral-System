from flask import Flask, render_template, request, redirect, url_for, session
import sqlite3

app = Flask(__name__)

# Secret key for session
app.secret_key = "smart_blood_secret_key"

DATABASE = "database.db"


# =========================================================
# DATABASE CONNECTION
# =========================================================

def get_db():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn


# =========================================================
# CREATE DATABASE
# =========================================================

def create_database():

    conn = get_db()

    # ---------------- USERS TABLE ----------------
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            role TEXT NOT NULL
        )
    """)

    # ---------------- DONORS TABLE ----------------
    conn.execute("""
        CREATE TABLE IF NOT EXISTS donors (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            name TEXT NOT NULL,
            blood_group TEXT NOT NULL,
            phone TEXT NOT NULL,
            city TEXT NOT NULL
        )
    """)

    # ---------------- RECEIVERS TABLE ----------------
    conn.execute("""
        CREATE TABLE IF NOT EXISTS receivers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            name TEXT NOT NULL,
            blood_group TEXT NOT NULL,
            hospital TEXT NOT NULL,
            city TEXT NOT NULL
        )
    """)

    # ---------------- BLOOD REQUESTS TABLE ----------------
    conn.execute("""
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

    conn.commit()

    # =====================================================
    # DEFAULT ADMIN ACCOUNT
    # =====================================================

    admin = conn.execute(
        "SELECT * FROM users WHERE email = ?",
        ("admin@gmail.com",)
    ).fetchone()

    if admin is None:

        conn.execute("""
            INSERT INTO users
            (name, email, password, role)
            VALUES (?, ?, ?, ?)
        """, (
            "Admin",
            "admin@gmail.com",
            "admin123",
            "admin"
        ))

        conn.commit()

    conn.close()


# =========================================================
# HOME PAGE
# =========================================================

@app.route("/")
def index():

    conn = get_db()

    donor_count = conn.execute(
        "SELECT COUNT(*) AS count FROM donors"
    ).fetchone()["count"]

    receiver_count = conn.execute(
        "SELECT COUNT(*) AS count FROM receivers"
    ).fetchone()["count"]

    request_count = conn.execute(
        "SELECT COUNT(*) AS count FROM blood_requests"
    ).fetchone()["count"]

    conn.close()

    return render_template(
        "index.html",
        donor_count=donor_count,
        receiver_count=receiver_count,
        request_count=request_count
    )


# =========================================================
# LOGIN
# =========================================================

@app.route("/login", methods=["GET", "POST"])
def login():

    if request.method == "POST":

        email = request.form["email"].strip()
        password = request.form["password"]

        conn = get_db()

        user = conn.execute("""
            SELECT *
            FROM users
            WHERE email = ? AND password = ?
        """, (email, password)).fetchone()

        conn.close()

        if user:

            # Store login information
            session["user_id"] = user["id"]
            session["name"] = user["name"]
            session["role"] = user["role"]

            # ---------------- DONOR ----------------
            if user["role"] == "donor":
                return redirect(url_for("donor_dashboard"))

            # ---------------- RECEIVER ----------------
            elif user["role"] == "receiver":
                return redirect(url_for("receiver_dashboard"))

            # ---------------- ADMIN ----------------
            elif user["role"] == "admin":
                return redirect(url_for("admin"))

        return render_template(
            "login.html",
            error="Invalid email or password"
        )

    return render_template("login.html")


# =========================================================
# LOGOUT
# =========================================================

@app.route("/logout")
def logout():

    session.clear()

    return redirect(url_for("index"))


# =========================================================
# DONOR REGISTRATION
# =========================================================

@app.route("/donor-register", methods=["GET", "POST"])
def donor_register():

    if request.method == "POST":

        name = request.form["name"].strip()
        email = request.form["email"].strip()
        password = request.form["password"]
        blood_group = request.form["blood_group"].strip()
        phone = request.form["phone"].strip()
        city = request.form["city"].strip()

        conn = get_db()

        try:

            # Create user
            cursor = conn.execute("""
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
            conn.execute("""
                INSERT INTO donors
                (user_id, name, blood_group, phone, city)
                VALUES (?, ?, ?, ?, ?)
            """, (
                user_id,
                name,
                blood_group,
                phone,
                city
            ))

            conn.commit()

        except sqlite3.IntegrityError:

            conn.close()

            return render_template(
                "donor_register.html",
                error="Email already registered."
            )

        conn.close()

        # =================================================
        # DIRECT DONOR LOGIN AFTER REGISTRATION
        # =================================================

        session["user_id"] = user_id
        session["name"] = name
        session["role"] = "donor"

        return redirect(url_for("donor_dashboard"))

    return render_template("donor_register.html")


# =========================================================
# RECEIVER REGISTRATION
# =========================================================

@app.route("/receiver-register", methods=["GET", "POST"])
def receiver_register():

    if request.method == "POST":

        name = request.form["name"].strip()
        email = request.form["email"].strip()
        password = request.form["password"]
        blood_group = request.form["blood_group"].strip()
        hospital = request.form["hospital"].strip()
        city = request.form["city"].strip()

        conn = get_db()

        try:

            # =================================================
            # SAVE RECEIVER IN USERS TABLE
            # =================================================

            cursor = conn.execute("""
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

            # =================================================
            # SAVE RECEIVER IN RECEIVERS TABLE
            # =================================================

            conn.execute("""
                INSERT INTO receivers
                (user_id, name, blood_group, hospital, city)
                VALUES (?, ?, ?, ?, ?)
            """, (
                user_id,
                name,
                blood_group,
                hospital,
                city
            ))

            conn.commit()

        except sqlite3.IntegrityError:

            conn.close()

            return render_template(
                "receiver_register.html",
                error="Email already registered."
            )

        # =====================================================
        # IMPORTANT:
        # Receiver is automatically logged in
        # =====================================================

        session["user_id"] = user_id
        session["name"] = name
        session["role"] = "receiver"

        conn.close()

        # =====================================================
        # DIRECTLY OPEN RECEIVER DASHBOARD
        # Matching donors will be displayed there
        # =====================================================

        return redirect(url_for("receiver_dashboard"))

    return render_template("receiver_register.html")


# =========================================================
# DONOR DASHBOARD
# =========================================================

@app.route("/donor-dashboard")
def donor_dashboard():

    # Only donor can access this page
    if session.get("role") != "donor":
        return redirect(url_for("login"))

    conn = get_db()

    donor = conn.execute("""
        SELECT *
        FROM donors
        WHERE user_id = ?
    """, (session["user_id"],)).fetchone()

    conn.close()

    # If donor record does not exist
    if donor is None:
        session.clear()
        return redirect(url_for("login"))

    return render_template(
        "donor_dashboard.html",
        donor=donor
    )


# =========================================================
# RECEIVER DASHBOARD
# =========================================================

@app.route("/receiver-dashboard")
def receiver_dashboard():

    # Only receiver can access this page
    if session.get("role") != "receiver":
        return redirect(url_for("login"))

    conn = get_db()

    # =====================================================
    # GET CURRENT RECEIVER
    # =====================================================

    receiver = conn.execute("""
        SELECT *
        FROM receivers
        WHERE user_id = ?
    """, (session["user_id"],)).fetchone()

    # If receiver record doesn't exist
    if receiver is None:

        conn.close()
        session.clear()

        return redirect(url_for("login"))

    # =====================================================
    # GET RECEIVER'S BLOOD REQUESTS
    # =====================================================

    requests = conn.execute("""
        SELECT *
        FROM blood_requests
        WHERE receiver_id = ?
        ORDER BY id DESC
    """, (receiver["id"],)).fetchall()

    # =====================================================
    # MATCHING DONORS
    #
    # 1. Same blood group
    # 2. Same city first
    # 3. Other cities next
    # =====================================================

    donors = conn.execute("""
        SELECT *
        FROM donors

        WHERE LOWER(TRIM(blood_group))
              = LOWER(TRIM(?))

        ORDER BY
            CASE
                WHEN LOWER(TRIM(city))
                     = LOWER(TRIM(?))
                THEN 0
                ELSE 1
            END,

            LOWER(TRIM(city)) ASC,
            name ASC
    """, (
        receiver["blood_group"],
        receiver["city"]
    )).fetchall()

    # =====================================================
    # SEPARATE SAME CITY AND OTHER CITY DONORS
    # =====================================================

    same_city_donors = []
    other_city_donors = []

    receiver_city = receiver["city"].strip().lower()

    for donor in donors:

        donor_city = donor["city"].strip().lower()

        if donor_city == receiver_city:

            same_city_donors.append(donor)

        else:

            other_city_donors.append(donor)

    conn.close()

    # =====================================================
    # SEND DATA TO RECEIVER DASHBOARD
    # =====================================================

    return render_template(
        "receiver_dashboard.html",
        receiver=receiver,
        requests=requests,
        donors=donors,
        same_city_donors=same_city_donors,
        other_city_donors=other_city_donors
    )


# =========================================================
# CREATE BLOOD REQUEST
# =========================================================

@app.route("/create-request", methods=["POST"])
def create_request():

    # Only receiver can create request
    if session.get("role") != "receiver":
        return redirect(url_for("login"))

    patient_name = request.form["patient_name"].strip()
    blood_group = request.form["blood_group"].strip()
    hospital = request.form["hospital"].strip()
    city = request.form["city"].strip()

    conn = get_db()

    receiver = conn.execute("""
        SELECT *
        FROM receivers
        WHERE user_id = ?
    """, (session["user_id"],)).fetchone()

    if receiver is None:

        conn.close()

        session.clear()

        return redirect(url_for("login"))

    conn.execute("""
        INSERT INTO blood_requests
        (receiver_id, patient_name, blood_group, hospital, city)
        VALUES (?, ?, ?, ?, ?)
    """, (
        receiver["id"],
        patient_name,
        blood_group,
        hospital,
        city
    ))

    conn.commit()
    conn.close()

    # After request creation go to search
    return redirect(
        url_for(
            "search",
            blood_group=blood_group,
            city=city
        )
    )


# =========================================================
# SEARCH DONORS
# =========================================================

@app.route("/search")
def search():

    blood_group = request.args.get(
        "blood_group", ""
    ).strip()

    city = request.args.get(
        "city", ""
    ).strip()

    conn = get_db()

    donors = []

    if blood_group:

        if city:

            # Same blood group
            # Same city first
            # Other cities next

            donors = conn.execute("""
                SELECT *
                FROM donors

                WHERE LOWER(TRIM(blood_group))
                      = LOWER(TRIM(?))

                ORDER BY
                    CASE
                        WHEN LOWER(TRIM(city))
                             = LOWER(TRIM(?))
                        THEN 0
                        ELSE 1
                    END,

                    LOWER(TRIM(city)) ASC,
                    name ASC
            """, (
                blood_group,
                city
            )).fetchall()

        else:

            donors = conn.execute("""
                SELECT *
                FROM donors

                WHERE LOWER(TRIM(blood_group))
                      = LOWER(TRIM(?))

                ORDER BY
                    LOWER(TRIM(city)) ASC,
                    name ASC
            """, (
                blood_group
            )).fetchall()

    conn.close()

    return render_template(
        "search.html",
        donors=donors,
        blood_group=blood_group,
        city=city
    )


# =========================================================
# ALL BLOOD REQUESTS
# =========================================================

@app.route("/requests")
def requests():

    if "user_id" not in session:

        return redirect(url_for("login"))

    conn = get_db()

    all_requests = conn.execute("""
        SELECT *
        FROM blood_requests
        ORDER BY id DESC
    """).fetchall()

    conn.close()

    return render_template(
        "requests.html",
        requests=all_requests
    )


# =========================================================
# ADMIN DASHBOARD
# =========================================================

@app.route("/admin")
def admin():

    # =====================================================
    # VERY IMPORTANT ADMIN SECURITY
    #
    # Only user with role = admin can enter
    # =====================================================

    if session.get("role") != "admin":

        return redirect(url_for("login"))

    conn = get_db()

    # =====================================================
    # GET ALL DONORS
    # =====================================================

    donors = conn.execute("""
        SELECT *
        FROM donors
        ORDER BY id DESC
    """).fetchall()

    # =====================================================
    # GET ALL RECEIVERS
    # =====================================================

    receivers = conn.execute("""
        SELECT *
        FROM receivers
        ORDER BY id DESC
    """).fetchall()

    # =====================================================
    # GET ALL BLOOD REQUESTS
    # =====================================================

    blood_requests = conn.execute("""
        SELECT *
        FROM blood_requests
        ORDER BY id DESC
    """).fetchall()

    conn.close()

    return render_template(
        "admin.html",
        donors=donors,
        receivers=receivers,
        requests=blood_requests
    )


# =========================================================
# PROFILE
# =========================================================

@app.route("/profile")
def profile():

    # User must be logged in
    if "user_id" not in session:

        return redirect(url_for("login"))

    conn = get_db()

    user = conn.execute("""
        SELECT *
        FROM users
        WHERE id = ?
    """, (session["user_id"],)).fetchone()

    if user is None:

        conn.close()

        session.clear()

        return redirect(url_for("login"))

    donor = None
    receiver = None

    # =====================================================
    # DONOR PROFILE
    # =====================================================

    if user["role"] == "donor":

        donor = conn.execute("""
            SELECT *
            FROM donors
            WHERE user_id = ?
        """, (user["id"],)).fetchone()

    # =====================================================
    # RECEIVER PROFILE
    # =====================================================

    elif user["role"] == "receiver":

        receiver = conn.execute("""
            SELECT *
            FROM receivers
            WHERE user_id = ?
        """, (user["id"],)).fetchone()

    conn.close()

    return render_template(
        "profile.html",
        user=user,
        donor=donor,
        receiver=receiver
    )


# =========================================================
# RUN APPLICATION
# =========================================================

if __name__ == "__main__":

    create_database()

    app.run(debug=True)