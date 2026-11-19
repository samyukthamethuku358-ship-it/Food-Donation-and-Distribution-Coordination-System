"""FoodShare web application. Run with: python app.py"""

from __future__ import annotations

import os
import secrets
import sqlite3
from datetime import datetime
from functools import wraps
from pathlib import Path
from werkzeug.security import check_password_hash, generate_password_hash
from flask import Flask, abort, flash, g, has_app_context, redirect, render_template, request, session, url_for

ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "foodshare.db"
DATE_FORMAT = "%Y-%m-%d %H:%M"
app = Flask(__name__)
app.config.update(SECRET_KEY=os.environ.get("FOODSHARE_SECRET_KEY") or secrets.token_hex(32),
                  SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax")


def connect_db():
    if has_app_context() and g.get("_foodshare_db") is not None:
        return g._foodshare_db
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    if has_app_context():
        g._foodshare_db = conn
    return conn


def init_db():
    with connect_db() as db:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL, email TEXT NOT NULL COLLATE NOCASE UNIQUE,
                password_hash TEXT NOT NULL, created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS donors (
                id TEXT PRIMARY KEY, name TEXT NOT NULL, contact TEXT NOT NULL DEFAULT '',
                phone TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS organizations (
                id TEXT PRIMARY KEY, name TEXT NOT NULL, contact TEXT NOT NULL DEFAULT '',
                phone TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS donations (
                id TEXT PRIMARY KEY, donor_id TEXT NOT NULL REFERENCES donors(id),
                category TEXT NOT NULL, quantity INTEGER NOT NULL CHECK(quantity>0),
                remaining INTEGER NOT NULL CHECK(remaining>=0), unit TEXT NOT NULL,
                prepared_at TEXT NOT NULL, expires_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'Available', created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS requests (
                id TEXT PRIMARY KEY, organization_id TEXT NOT NULL REFERENCES organizations(id),
                category TEXT NOT NULL, quantity INTEGER NOT NULL CHECK(quantity>0),
                unit TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'Pending', created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS allocations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                donation_id TEXT NOT NULL REFERENCES donations(id),
                request_id TEXT NOT NULL REFERENCES requests(id), quantity INTEGER NOT NULL CHECK(quantity>0),
                allocated_at TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'Allocated', distributed_at TEXT
            );
        """)


def now():
    return datetime.now().strftime(DATE_FORMAT)


def expire_food(db):
    db.execute("UPDATE donations SET status='Expired' WHERE remaining>0 AND expires_at<=?", (now(),))


def metrics_snapshot(db):
    """Reports required by the problem statement."""
    return {
        "donations": db.execute("SELECT COUNT(*) n FROM donations").fetchone()["n"],
        "available": db.execute(
            "SELECT COALESCE(SUM(remaining),0) n FROM donations "
            "WHERE remaining>0 AND status!='Expired' AND expires_at>?",
            (now(),),
        ).fetchone()["n"],
        "pending": db.execute("SELECT COUNT(*) n FROM requests WHERE status='Pending'").fetchone()["n"],
        "completed": db.execute("SELECT COUNT(*) n FROM requests WHERE status='Completed'").fetchone()["n"],
        "distributed": db.execute(
            "SELECT COALESCE(SUM(quantity),0) n FROM allocations WHERE status='Distributed'"
        ).fetchone()["n"],
        "expired": db.execute("SELECT COUNT(*) n FROM donations WHERE status='Expired'").fetchone()["n"],
    }


def allocate_donation(db, donation_id: str, request_id: str, qty: int) -> tuple[int, str, int]:
    """Apply business rules and allocate food. Returns (qty, unit, remaining)."""
    d = db.execute("SELECT * FROM donations WHERE id=?", (donation_id,)).fetchone()
    r = db.execute("SELECT * FROM requests WHERE id=?", (request_id,)).fetchone()
    if not d or not r or qty <= 0:
        raise ValueError("Choose a donation and request and enter a positive quantity.")
    if d["status"] == "Expired" or datetime.strptime(d["expires_at"], DATE_FORMAT) <= datetime.now():
        raise ValueError("Expired food cannot be allocated.")
    if d["remaining"] <= 0:
        raise ValueError("This donation has no remaining quantity.")
    if d["category"].strip().casefold() != r["category"].strip().casefold():
        raise ValueError("Food categories must match.")
    if d["unit"].strip().casefold() != r["unit"].strip().casefold():
        raise ValueError("Donation and request units must match.")
    if r["status"] == "Completed":
        raise ValueError("This request is already complete.")
    allocated = db.execute(
        "SELECT COALESCE(SUM(quantity),0) FROM allocations WHERE request_id=?", (request_id,)
    ).fetchone()[0]
    outstanding = r["quantity"] - allocated
    if qty > d["remaining"]:
        raise ValueError(f"Only {d['remaining']} {d['unit']} remain in this donation.")
    if qty > outstanding:
        raise ValueError(f"This request needs only {outstanding} more {r['unit']}.")
    remaining = d["remaining"] - qty
    status = "Allocated" if remaining == 0 else "Available"
    db.execute("UPDATE donations SET remaining=?, status=? WHERE id=?", (remaining, status, donation_id))
    db.execute(
        "INSERT INTO allocations(donation_id,request_id,quantity,allocated_at) VALUES(?,?,?,?)",
        (donation_id, request_id, qty, now()),
    )
    if allocated + qty >= r["quantity"]:
        db.execute("UPDATE requests SET status='Completed' WHERE id=?", (request_id,))
    return qty, d["unit"], remaining


@app.before_request
def load_user():
    if request.method == "POST":
        expected = session.get("csrf_token", "")
        provided = request.form.get("csrf_token", "")
        if not expected or not secrets.compare_digest(expected, provided):
            abort(400, "Form expired. Refresh the page and try again.")
    g.user = None
    if session.get("user_id"):
        with connect_db() as db:
            g.user = db.execute("SELECT id,name,email FROM users WHERE id=?", (session["user_id"],)).fetchone()


@app.teardown_appcontext
def close_db(_error=None):
    db = g.pop("_foodshare_db", None)
    if db is not None:
        db.close()


def login_required(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        if not g.user:
            flash("Please sign in to continue.", "info")
            return redirect(url_for("login", next=request.path))
        return fn(*args, **kwargs)
    return wrapped


@app.context_processor
def shared_context():
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_urlsafe(32)
    return {"current_year": datetime.now().year, "csrf_token": session["csrf_token"]}


@app.route("/")
def index():
    if g.user:
        return redirect(url_for("home"))
    return render_template("landing.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    if g.user:
        return redirect(url_for("home"))
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        confirm = request.form.get("confirm_password", "")
        if not name or not email or "@" not in email:
            flash("Enter your name and a valid email address.", "error")
        elif len(password) < 8:
            flash("Choose a password with at least 8 characters.", "error")
        elif password != confirm:
            flash("The passwords do not match.", "error")
        else:
            try:
                with connect_db() as db:
                    cur = db.execute("INSERT INTO users(name,email,password_hash,created_at) VALUES(?,?,?,?)",
                                     (name, email, generate_password_hash(password), now()))
                session.clear()
                session["user_id"] = cur.lastrowid
                flash("Your FoodShare account is ready. Welcome!", "success")
                return redirect(url_for("home"))
            except sqlite3.IntegrityError:
                flash("An account with that email already exists. Please sign in.", "error")
    return render_template("auth.html", mode="register")


@app.route("/login", methods=["GET", "POST"])
def login():
    if g.user:
        return redirect(url_for("home"))
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        with connect_db() as db:
            user = db.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
        if user and check_password_hash(user["password_hash"], password):
            session.clear()
            session["user_id"] = user["id"]
            flash(f"Welcome back, {user['name']}.", "success")
            return redirect(url_for("home"))
        flash("Email or password is incorrect.", "error")
    return render_template("auth.html", mode="login")


@app.post("/logout")
@login_required
def logout():
    session.clear()
    flash("You are signed out.", "info")
    return redirect(url_for("index"))


@app.route("/home")
@login_required
def home():
    with connect_db() as db:
        expire_food(db)
        metrics = metrics_snapshot(db)
        recent = db.execute(
            "SELECT d.id,d.category,d.remaining,d.unit,d.expires_at,d.status,n.name donor "
            "FROM donations d JOIN donors n ON n.id=d.donor_id ORDER BY d.created_at DESC LIMIT 6"
        ).fetchall()
        needs = db.execute(
            "SELECT r.id,r.category,r.quantity,r.unit,o.name organization FROM requests r "
            "JOIN organizations o ON o.id=r.organization_id WHERE r.status='Pending' "
            "ORDER BY r.created_at DESC LIMIT 6"
        ).fetchall()
    return render_template("home.html", metrics=metrics, recent=recent, needs=needs)


@app.route("/donors", methods=["GET", "POST"])
@login_required
def donors():
    with connect_db() as db:
        if request.method == "POST":
            values = [request.form.get(k, "").strip() for k in ("id", "name", "contact", "phone")]
            if not values[0] or not values[1]: flash("Donor ID and name are required.", "error")
            else:
                try:
                    db.execute("INSERT INTO donors VALUES(?,?,?,?,?)", (*values, now()))
                    flash("Donor registered.", "success")
                    return redirect(url_for("donors"))
                except sqlite3.IntegrityError: flash("That donor ID is already in use.", "error")
        records = db.execute("SELECT * FROM donors ORDER BY name").fetchall()
    return render_template("records.html", kind="donors", records=records)


@app.route("/organizations", methods=["GET", "POST"])
@login_required
def organizations():
    with connect_db() as db:
        if request.method == "POST":
            values = [request.form.get(k, "").strip() for k in ("id", "name", "contact", "phone")]
            if not values[0] or not values[1]: flash("Organization ID and name are required.", "error")
            else:
                try:
                    db.execute("INSERT INTO organizations VALUES(?,?,?,?,?)", (*values, now()))
                    flash("Organization registered.", "success")
                    return redirect(url_for("organizations"))
                except sqlite3.IntegrityError: flash("That organization ID is already in use.", "error")
        records = db.execute("SELECT * FROM organizations ORDER BY name").fetchall()
    return render_template("records.html", kind="organizations", records=records)


@app.route("/donations", methods=["GET", "POST"])
@login_required
def donations():
    view = request.args.get("view", "all")
    with connect_db() as db:
        expire_food(db)
        donors_list = db.execute("SELECT id,name FROM donors ORDER BY name").fetchall()
        if request.method == "POST":
            try:
                donation_id = request.form.get("id", "").strip()
                donor = request.form.get("donor_id", "").strip()
                category = request.form.get("category", "").strip()
                unit = request.form.get("unit", "").strip() or "Packets"
                qty = int(request.form.get("quantity", "0"))
                prepared = datetime.strptime(request.form.get("prepared_at", ""), DATE_FORMAT).strftime(DATE_FORMAT)
                expires = datetime.strptime(request.form.get("expires_at", ""), DATE_FORMAT).strftime(DATE_FORMAT)
                if not donation_id or not donor or not category or not unit or qty <= 0:
                    raise ValueError("Donation ID, donor, category, unit, and a positive quantity are required.")
                if datetime.strptime(expires, DATE_FORMAT) <= datetime.strptime(prepared, DATE_FORMAT):
                    raise ValueError("Use-by time must be after preparation time.")
                if datetime.strptime(expires, DATE_FORMAT) <= datetime.now():
                    raise ValueError("Use-by time must be in the future so the food can be allocated.")
                db.execute(
                    "INSERT INTO donations VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (donation_id, donor, category, qty, qty, unit, prepared, expires, "Available", now()),
                )
                flash(f"Donation {donation_id} added: {qty} {unit} of {category} available.", "success")
                return redirect(url_for("donations"))
            except ValueError as exc:
                flash(str(exc) if str(exc) else "Check the required fields, quantity, and dates.", "error")
            except sqlite3.IntegrityError:
                flash("Check the unique donation ID and that the donor exists.", "error")
        if view == "available":
            records = db.execute(
                "SELECT d.*,n.name donor_name FROM donations d JOIN donors n ON n.id=d.donor_id "
                "WHERE d.remaining>0 AND d.status!='Expired' AND d.expires_at>? "
                "ORDER BY d.expires_at",
                (now(),),
            ).fetchall()
        else:
            records = db.execute(
                "SELECT d.*,n.name donor_name FROM donations d JOIN donors n ON n.id=d.donor_id "
                "ORDER BY d.created_at DESC"
            ).fetchall()
    return render_template(
        "records.html", kind="donations", records=records, donors=donors_list, view=view
    )


@app.route("/requests", methods=["GET", "POST"])
@login_required
def food_requests():
    with connect_db() as db:
        orgs = db.execute("SELECT id,name FROM organizations ORDER BY name").fetchall()
        if request.method == "POST":
            try:
                rid = request.form.get("id", "").strip()
                org = request.form.get("organization_id", "").strip()
                category = request.form.get("category", "").strip()
                unit = request.form.get("unit", "").strip() or "Packets"
                qty = int(request.form.get("quantity", "0"))
                if not rid or not org or not category or not unit or qty <= 0:
                    raise ValueError("Request ID, organization, category, unit, and a positive quantity are required.")
                db.execute(
                    "INSERT INTO requests VALUES(?,?,?,?,?,?,?)",
                    (rid, org, category, qty, unit, "Pending", now()),
                )
                flash(f"Food request {rid} created for {qty} {unit} of {category}.", "success")
                return redirect(url_for("food_requests"))
            except ValueError as exc:
                flash(str(exc), "error")
            except sqlite3.IntegrityError:
                flash("Check the unique request ID and that the organization exists.", "error")
        records = db.execute(
            "SELECT r.*,o.name organization_name,COALESCE(SUM(a.quantity),0) allocated "
            "FROM requests r JOIN organizations o ON o.id=r.organization_id "
            "LEFT JOIN allocations a ON a.request_id=r.id GROUP BY r.id ORDER BY r.created_at DESC"
        ).fetchall()
    return render_template("records.html", kind="requests", records=records, organizations=orgs)


@app.route("/allocations", methods=["GET", "POST"])
@login_required
def allocations():
    with connect_db() as db:
        expire_food(db)
        db.commit()  # keep expiry updates even if a later allocation attempt fails
        if request.method == "POST":
            donation_id = request.form.get("donation_id", "").strip()
            request_id = request.form.get("request_id", "").strip()
            try:
                qty = int(request.form.get("quantity", "0"))
                with db:
                    qty, unit, remaining = allocate_donation(db, donation_id, request_id, qty)
                flash(
                    f"Allocation successful! {qty} {unit} allocated to request {request_id}. "
                    f"Remaining quantity: {remaining} {unit}.",
                    "success",
                )
                return redirect(url_for("allocations"))
            except ValueError as e:
                flash(str(e) or "Enter a valid positive quantity.", "error")
            except (TypeError, sqlite3.IntegrityError):
                flash("Allocation failed. Check the donation, request, and quantity.", "error")
        available = db.execute(
            "SELECT id,category,remaining,unit,expires_at FROM donations "
            "WHERE remaining>0 AND status!='Expired' AND expires_at>? ORDER BY expires_at",
            (now(),),
        ).fetchall()
        open_requests = db.execute(
            "SELECT r.id,r.category,r.unit,r.quantity-COALESCE(SUM(a.quantity),0) needed "
            "FROM requests r LEFT JOIN allocations a ON a.request_id=r.id "
            "WHERE r.status='Pending' GROUP BY r.id "
            "HAVING needed>0 ORDER BY r.created_at"
        ).fetchall()
        history = db.execute(
            "SELECT a.*,d.category,d.unit,r.id request_code,o.name organization "
            "FROM allocations a JOIN donations d ON d.id=a.donation_id "
            "JOIN requests r ON r.id=a.request_id "
            "JOIN organizations o ON o.id=r.organization_id ORDER BY a.id DESC"
        ).fetchall()
    return render_template(
        "allocations.html", available=available, open_requests=open_requests, history=history
    )


@app.post("/allocations/<int:allocation_id>/distribute")
@login_required
def distribute(allocation_id):
    with connect_db() as db:
        with db:
            row = db.execute(
                "SELECT donation_id,quantity,status FROM allocations WHERE id=?", (allocation_id,)
            ).fetchone()
            if not row or row["status"] != "Allocated":
                flash("This allocation is missing or already distributed.", "error")
            else:
                db.execute(
                    "UPDATE allocations SET status='Distributed',distributed_at=? WHERE id=? AND status='Allocated'",
                    (now(), allocation_id),
                )
                flash(
                    f"Distribution marked complete for {row['quantity']} units from donation {row['donation_id']}.",
                    "success",
                )
    return redirect(url_for("allocations"))


@app.route("/reports")
@login_required
def reports():
    with connect_db() as db:
        expire_food(db)
        metrics = metrics_snapshot(db)
        donors_report = db.execute(
            "SELECT n.id,n.name,COUNT(d.id) donations,COALESCE(SUM(d.quantity),0) quantity "
            "FROM donors n LEFT JOIN donations d ON d.donor_id=n.id GROUP BY n.id ORDER BY n.name"
        ).fetchall()
        history = db.execute(
            "SELECT a.id,a.donation_id,a.request_id,a.quantity,a.allocated_at,a.status,a.distributed_at,"
            "d.category,d.unit,o.name organization "
            "FROM allocations a JOIN donations d ON d.id=a.donation_id "
            "JOIN requests r ON r.id=a.request_id "
            "JOIN organizations o ON o.id=r.organization_id ORDER BY a.id DESC"
        ).fetchall()
    return render_template("reports.html", metrics=metrics, donors=donors_report, history=history)


if __name__ == "__main__":
    init_db()
    app.run(host="127.0.0.1", port=5000, debug=False)
