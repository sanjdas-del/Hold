import hashlib
import os
import secrets
import sqlite3
from functools import wraps

from flask import Flask, abort, flash, g, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from db import get_db, init_db
from seed import seed

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "officehours-dev-secret")
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_NAME"] = "hold_flash"

SESSION_COOKIE = "hold_session"
SESSION_MAX_AGE = 60 * 60 * 24 * 14


def current_user():
    return getattr(g, "user", None)


def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not current_user():
            flash("Sign in to continue.")
            return redirect(url_for("login"))
        return fn(*args, **kwargs)

    return wrapper


def csrf_token():
    token = session.get("csrf")
    if not token:
        token = secrets.token_hex(32)
        session["csrf"] = token
    return token


def require_csrf():
    expected = session.get("csrf") or ""
    sent = request.form.get("csrf_token") or ""
    if not expected or len(sent) != len(expected) or not secrets.compare_digest(expected, sent):
        abort(400)


def digest(raw):
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def load_user_by_token(token):
    if not token:
        return None
    conn = get_db()
    row = conn.execute(
        """
        SELECT users.* FROM sessions
        JOIN users ON users.id = sessions.user_id
        WHERE sessions.token = ?
        """,
        (token,),
    ).fetchone()
    conn.close()
    return row


@app.before_request
def load_user():
    if request.endpoint == "static":
        return
    init_db()
    seed()
    # Login is the cookie the browser sends back. A token in the query string is not a session.
    token = request.cookies.get(SESSION_COOKIE)
    g.user = None
    g.session_token = None
    if not token:
        return
    row = load_user_by_token(token)
    if row:
        g.user = row
        g.session_token = token


@app.after_request
def persist_session_cookie(response):
    token = g.get("session_token")
    if token:
        response.set_cookie(
            SESSION_COOKIE,
            token,
            httponly=True,
            samesite="Lax",
            secure=request.is_secure,
            path="/",
            max_age=SESSION_MAX_AGE,
        )
        response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; style-src 'self'; form-action 'self'; frame-ancestors 'none'"
    )
    return response


@app.context_processor
def inject_user():
    return {"current_user": current_user(), "csrf_token": csrf_token}


def create_session(user_id):
    token = secrets.token_hex(24)
    conn = get_db()
    conn.execute("INSERT INTO sessions (token, user_id) VALUES (?, ?)", (token, user_id))
    conn.commit()
    conn.close()
    return token


def issue_handoff(user_id):
    raw = secrets.token_urlsafe(32)
    conn = get_db()
    conn.execute(
        """
        INSERT INTO handoff_codes (code_hash, user_id, expires_at)
        VALUES (?, ?, datetime('now', '+15 minutes'))
        """,
        (digest(raw), user_id),
    )
    conn.commit()
    conn.close()
    return url_for("redeem_handoff", code=raw, _external=True)


def fetch_booking(conn, booking_id):
    return conn.execute(
        """
        SELECT
            bookings.*,
            slots.starts_at,
            slots.location,
            slots.topic,
            slots.ta_id AS slot_ta_id,
            users.display_name AS ta_name,
            students.email AS student_email
        FROM bookings
        JOIN slots ON slots.id = bookings.slot_id
        JOIN users ON users.id = slots.ta_id
        JOIN users AS students ON students.id = bookings.student_id
        WHERE bookings.id = ?
        """,
        (booking_id,),
    ).fetchone()


def can_view_booking(user, booking):
    if booking["student_id"] == user["id"]:
        return True
    return user["role"] == "ta" and booking["slot_ta_id"] == user["id"]


@app.get("/health")
def health():
    conn = get_db()
    conn.execute("SELECT 1").fetchone()
    conn.close()
    return {"ok": True}


@app.get("/")
def home():
    conn = get_db()
    slots = conn.execute(
        """
        SELECT
            slots.*,
            users.display_name AS ta_name,
            bookings.id AS booking_id,
            bookings.student_id AS booked_by
        FROM slots
        JOIN users ON users.id = slots.ta_id
        LEFT JOIN bookings ON bookings.slot_id = slots.id
        ORDER BY slots.starts_at
        """
    ).fetchall()
    conn.close()
    return render_template("home.html", slots=slots)


@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user():
        return redirect(url_for("mine"))
    if request.method == "POST":
        require_csrf()
        email = (request.form.get("email") or "").strip().lower()
        password = request.form.get("password") or ""
        conn = get_db()
        user = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        conn.close()
        if not user or not check_password_hash(user["password_hash"], password):
            return render_template(
                "login.html",
                error="Those credentials do not match a Hold account.",
                email=email,
            )
        g.session_token = create_session(user["id"])
        return redirect(url_for("mine"))
    return render_template("login.html", email="")


@app.route("/register", methods=["GET", "POST"])
def register():
    if current_user():
        return redirect(url_for("mine"))
    if request.method == "POST":
        require_csrf()
        email = (request.form.get("email") or "").strip().lower()
        display_name = (request.form.get("display_name") or "").strip()
        password = request.form.get("password") or ""
        values = {"email": email, "display_name": display_name}
        if not email.endswith(".edu"):
            return render_template("register.html", error="Use a .edu email.", values=values)
        if len(display_name) < 2 or len(password) < 8:
            return render_template(
                "register.html",
                error="Name and an 8+ character password are required.",
                values=values,
            )
        conn = get_db()
        taken = conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
        if taken:
            conn.close()
            return render_template("register.html", error="That email already has an account.", values=values)
        cur = conn.execute(
            "INSERT INTO users (email, password_hash, display_name, role) VALUES (?, ?, ?, 'student')",
            (email, generate_password_hash(password), display_name),
        )
        conn.commit()
        user_id = cur.lastrowid
        conn.close()
        g.session_token = create_session(user_id)
        return redirect(url_for("mine"))
    return render_template("register.html", values={})


@app.post("/logout")
def logout():
    require_csrf()
    token = g.get("session_token")
    if token:
        conn = get_db()
        conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
        conn.commit()
        conn.close()
    g.session_token = None
    session.clear()
    resp = redirect(url_for("home"))
    resp.delete_cookie(SESSION_COOKIE, path="/", samesite="Lax")
    return resp


@app.get("/slots/new")
@login_required
def new_slot():
    if current_user()["role"] != "ta":
        abort(403)
    return render_template("new_slot.html")


@app.post("/slots")
@login_required
def create_slot():
    require_csrf()
    if current_user()["role"] != "ta":
        abort(403)
    starts_at = (request.form.get("starts_at") or "").strip()
    location = (request.form.get("location") or "").strip()
    topic = (request.form.get("topic") or "").strip()
    if not starts_at or not location or not topic:
        return render_template("new_slot.html", error="All fields are required.")
    conn = get_db()
    conn.execute(
        "INSERT INTO slots (ta_id, starts_at, location, topic) VALUES (?, ?, ?, ?)",
        (current_user()["id"], starts_at, location, topic),
    )
    conn.commit()
    conn.close()
    flash("Slot posted.")
    return redirect(url_for("home"))


@app.post("/slots/<int:slot_id>/book")
@login_required
def book_slot(slot_id):
    require_csrf()
    if current_user()["role"] != "student":
        flash("TA accounts post hours; students book them.")
        return redirect(url_for("home"))
    conn = get_db()
    slot = conn.execute("SELECT * FROM slots WHERE id = ?", (slot_id,)).fetchone()
    if not slot:
        conn.close()
        abort(404)
    taken = conn.execute("SELECT id FROM bookings WHERE slot_id = ?", (slot_id,)).fetchone()
    if taken:
        conn.close()
        flash("That slot was just taken.")
        return redirect(url_for("home"))
    try:
        conn.execute(
            "INSERT INTO bookings (slot_id, student_id) VALUES (?, ?)",
            (slot_id, current_user()["id"]),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        conn.rollback()
        conn.close()
        flash("That slot was just taken.")
        return redirect(url_for("home"))
    conn.close()
    flash("You're on the list. Check My bookings for any note from the TA.")
    return redirect(url_for("mine"))


@app.route("/me", methods=["GET", "POST"])
@login_required
def mine():
    user = current_user()
    handoff_url = None
    if request.method == "POST":
        require_csrf()
        if user["role"] != "student":
            abort(403)
        handoff_url = issue_handoff(user["id"])

    conn = get_db()
    if user["role"] == "ta":
        slots = conn.execute(
            """
            SELECT slots.*, bookings.id AS booking_id, students.display_name AS student_name
            FROM slots
            LEFT JOIN bookings ON bookings.slot_id = slots.id
            LEFT JOIN users AS students ON students.id = bookings.student_id
            WHERE slots.ta_id = ?
            ORDER BY slots.starts_at
            """,
            (user["id"],),
        ).fetchall()
        conn.close()
        return render_template("ta_schedule.html", slots=slots)

    bookings = conn.execute(
        """
        SELECT
            bookings.*,
            slots.starts_at,
            slots.location,
            slots.topic,
            users.display_name AS ta_name
        FROM bookings
        JOIN slots ON slots.id = bookings.slot_id
        JOIN users ON users.id = slots.ta_id
        WHERE bookings.student_id = ?
        ORDER BY slots.starts_at
        """,
        (user["id"],),
    ).fetchall()
    conn.close()
    return render_template("mine.html", bookings=bookings, handoff_url=handoff_url)


def handoff_expired():
    return (
        render_template(
            "error.html",
            title="Link expired",
            message="That device link has already been used or has expired. Create a new one from My bookings.",
        ),
        400,
    )


@app.get("/handoff/<code>")
def redeem_handoff(code):
    code_hash = digest(code)
    conn = get_db()
    conn.isolation_level = None
    try:
        conn.execute("BEGIN IMMEDIATE")
        updated = conn.execute(
            """
            UPDATE handoff_codes
            SET used_at = datetime('now')
            WHERE code_hash = ? AND used_at IS NULL AND expires_at > datetime('now')
            """,
            (code_hash,),
        )
        if updated.rowcount != 1:
            conn.execute("ROLLBACK")
            conn.close()
            return handoff_expired()
        row = conn.execute(
            "SELECT user_id FROM handoff_codes WHERE code_hash = ?",
            (code_hash,),
        ).fetchone()
        token = secrets.token_hex(24)
        conn.execute(
            "INSERT INTO sessions (token, user_id) VALUES (?, ?)",
            (token, row["user_id"]),
        )
        conn.execute("COMMIT")
    except sqlite3.Error:
        try:
            conn.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        conn.close()
        return handoff_expired()
    conn.close()
    g.session_token = token
    flash("This browser is signed in.")
    return redirect(url_for("mine"))


@app.get("/bookings/<int:booking_id>")
@login_required
def booking_detail(booking_id):
    conn = get_db()
    booking = fetch_booking(conn, booking_id)
    conn.close()
    if not booking:
        abort(404)
    if not can_view_booking(current_user(), booking):
        abort(403)
    return render_template("booking.html", booking=booking)


@app.get("/bookings/<int:booking_id>/cancel")
@login_required
def cancel_booking_get(booking_id):
    flash("Cancellation has to be confirmed from the booking page.")
    return redirect(url_for("booking_detail", booking_id=booking_id))


@app.post("/bookings/<int:booking_id>/cancel")
@login_required
def cancel_booking(booking_id):
    require_csrf()
    conn = get_db()
    booking = fetch_booking(conn, booking_id)
    if not booking:
        conn.close()
        abort(404)
    if not can_view_booking(current_user(), booking):
        conn.close()
        abort(403)
    conn.execute("DELETE FROM bookings WHERE id = ?", (booking_id,))
    conn.commit()
    conn.close()
    flash("Booking cancelled.")
    return redirect(url_for("mine"))


@app.get("/bookings/<int:booking_id>/transfer")
@login_required
def transfer_booking_get(booking_id):
    flash("Use the transfer form on the booking page.")
    return redirect(url_for("booking_detail", booking_id=booking_id))


@app.post("/bookings/<int:booking_id>/transfer")
@login_required
def transfer_booking(booking_id):
    require_csrf()
    email = (request.form.get("email") or "").strip().lower()
    conn = get_db()
    booking = fetch_booking(conn, booking_id)
    if not booking:
        conn.close()
        abort(404)
    if booking["student_id"] != current_user()["id"]:
        conn.close()
        abort(403)
    if not email:
        conn.close()
        flash("Need an email to transfer to.")
        return redirect(url_for("booking_detail", booking_id=booking_id))
    other = conn.execute(
        "SELECT * FROM users WHERE email = ? AND role = 'student'",
        (email,),
    ).fetchone()
    if not other:
        conn.close()
        flash("No student account with that email.")
        return redirect(url_for("booking_detail", booking_id=booking_id))
    if other["id"] == current_user()["id"]:
        conn.close()
        flash("That booking is already yours.")
        return redirect(url_for("booking_detail", booking_id=booking_id))
    conn.execute("UPDATE bookings SET student_id = ? WHERE id = ?", (other["id"], booking_id))
    conn.commit()
    conn.close()
    flash("Transferred.")
    return redirect(url_for("mine"))


@app.errorhandler(400)
def bad_request(_e):
    return (
        render_template(
            "error.html",
            title="Bad request",
            message="That form expired. Go back, reload the page, and try again.",
        ),
        400,
    )


@app.errorhandler(403)
def forbidden(_e):
    return render_template("error.html", title="Not allowed", message="You cannot do that with this account."), 403


@app.errorhandler(404)
def not_found(_e):
    return render_template("error.html", title="Missing", message="That slot or booking is not on the board."), 404


if __name__ == "__main__":
    seed()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "3000")), debug=False)
