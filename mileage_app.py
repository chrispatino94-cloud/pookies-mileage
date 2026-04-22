import csv
import os
import sqlite3
from datetime import date, datetime, timedelta
from functools import wraps
from io import StringIO

from dotenv import load_dotenv
from flask import Flask, Response, flash, redirect, render_template, request, session, url_for
from flask_session import Session

load_dotenv()

app = Flask(__name__)
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "dev-secret-change-me")
app.config["SESSION_TYPE"] = "filesystem"
app.config["SESSION_PERMANENT"] = True
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(hours=24)
Session(app)

DB_PATH = os.path.join(os.path.dirname(__file__), "mileage.db")
IRS_RATE = 0.67
TRIP_TYPES = [
    "Client Visit",
    "Supply Run",
    "Bank/ATM",
    "Vet/Emergency",
    "Marketing",
    "Post Office",
    "Equipment Pickup",
    "Other",
]
SEED_LOCATIONS = [
    ("Home", "Bennett CO", 0.0),
    ("PetSmart", "Parker CO", 12.3),
    ("Petco", "Parker CO", 11.8),
    ("Costco", "Aurora CO", 18.2),
    ("Walmart", "Bennett CO", 2.1),
    ("Citizens Bank", "Parker CO", 11.5),
    ("Post Office", "Bennett CO", 1.8),
    ("Vet Emergency", "Parker CO", 13.0),
]


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_db() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS trips (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT,
                purpose TEXT,
                start_location TEXT,
                end_location TEXT,
                miles REAL,
                deductible_amount REAL,
                trip_type TEXT,
                client_name TEXT,
                notes TEXT,
                created_at TEXT,
                lat_start REAL,
                lng_start REAL,
                lat_end REAL,
                lng_end REAL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS saved_locations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT,
                address TEXT,
                typical_miles REAL
            )
            """
        )
        count = conn.execute("SELECT COUNT(*) FROM saved_locations").fetchone()[0]
        if count == 0:
            conn.executemany(
                "INSERT INTO saved_locations (name, address, typical_miles) VALUES (?, ?, ?)",
                SEED_LOCATIONS,
            )
        conn.commit()


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("authenticated"):
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)

    return wrapped


def money(value):
    return f"${float(value or 0):,.2f}"


app.jinja_env.filters["money"] = money


def parse_float(value, default=0.0):
    try:
        return float(value or default)
    except (TypeError, ValueError):
        return default


def get_saved_locations():
    with get_db() as conn:
        return conn.execute("SELECT * FROM saved_locations ORDER BY name").fetchall()


def today_totals():
    today = date.today().isoformat()
    with get_db() as conn:
        row = conn.execute(
            """
            SELECT COUNT(*) AS trips, COALESCE(SUM(miles), 0) AS miles,
                   COALESCE(SUM(deductible_amount), 0) AS deduction
            FROM trips WHERE date = ?
            """,
            (today,),
        ).fetchone()
    return row


def summary_between(start_date=None, end_date=None):
    query = "SELECT COUNT(*) AS trips, COALESCE(SUM(miles), 0) AS miles, COALESCE(SUM(deductible_amount), 0) AS deduction FROM trips"
    params = []
    conditions = []
    if start_date:
        conditions.append("date >= ?")
        params.append(start_date)
    if end_date:
        conditions.append("date <= ?")
        params.append(end_date)
    if conditions:
        query += " WHERE " + " AND ".join(conditions)
    with get_db() as conn:
        return conn.execute(query, params).fetchone()


def month_bounds(day=None):
    day = day or date.today()
    start = day.replace(day=1)
    if start.month == 12:
        next_month = start.replace(year=start.year + 1, month=1)
    else:
        next_month = start.replace(month=start.month + 1)
    return start, next_month - timedelta(days=1)


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        configured_password = os.getenv("APP_PASSWORD", "").strip()
        submitted_password = request.form.get("password", "")
        if configured_password and submitted_password == configured_password:
            session.permanent = True
            session["authenticated"] = True
            return redirect(request.args.get("next") or url_for("log_trip"))
        flash("Incorrect password. Try again.", "danger")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/")
def home():
    return redirect(url_for("log_trip"))


@app.route("/log", methods=["GET", "POST"])
@login_required
def log_trip():
    if request.method == "POST":
        trip_date = request.form.get("date") or date.today().isoformat()
        trip_type = request.form.get("trip_type") or "Other"
        client_name = request.form.get("client_name", "").strip()
        start_location = request.form.get("start_location", "").strip()
        custom_end = request.form.get("custom_end_location", "").strip()
        selected_end = request.form.get("end_location", "").strip()
        end_location = custom_end or selected_end
        miles = parse_float(request.form.get("miles"))
        notes = request.form.get("notes", "").strip()
        deductible_amount = round(miles * IRS_RATE, 2)
        purpose = f"{trip_type}: {client_name}" if client_name and trip_type == "Client Visit" else trip_type

        with get_db() as conn:
            conn.execute(
                """
                INSERT INTO trips (
                    date, purpose, start_location, end_location, miles, deductible_amount,
                    trip_type, client_name, notes, created_at, lat_start, lng_start, lat_end, lng_end
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    trip_date,
                    purpose,
                    start_location,
                    end_location,
                    miles,
                    deductible_amount,
                    trip_type,
                    client_name,
                    notes,
                    datetime.utcnow().isoformat(timespec="seconds"),
                    parse_float(request.form.get("lat_start"), None),
                    parse_float(request.form.get("lng_start"), None),
                    parse_float(request.form.get("lat_end"), None),
                    parse_float(request.form.get("lng_end"), None),
                ),
            )
            conn.commit()
        flash(f"Logged {miles:.1f} miles for a {money(deductible_amount)} deduction.", "success")
        return redirect(url_for("log_trip"))

    return render_template(
        "log_trip.html",
        today=date.today().isoformat(),
        trip_types=TRIP_TYPES,
        locations=get_saved_locations(),
        totals=today_totals(),
    )


@app.route("/history", methods=["GET", "POST"])
@login_required
def history():
    if request.method == "POST":
        trip_id = request.form.get("trip_id")
        with get_db() as conn:
            conn.execute("DELETE FROM trips WHERE id = ?", (trip_id,))
            conn.commit()
        flash("Trip deleted.", "success")
        return redirect(url_for("history", filter=request.args.get("filter", "month")))

    filter_name = request.args.get("filter", "month")
    today_value = date.today()
    params = []
    where = ""
    if filter_name == "week":
        start = today_value - timedelta(days=today_value.weekday())
        where = "WHERE date >= ?"
        params.append(start.isoformat())
    elif filter_name == "month":
        start, _ = month_bounds(today_value)
        where = "WHERE date >= ?"
        params.append(start.isoformat())

    with get_db() as conn:
        trips = conn.execute(
            f"SELECT * FROM trips {where} ORDER BY date DESC, created_at DESC, id DESC",
            params,
        ).fetchall()
    return render_template("history.html", trips=trips, active_filter=filter_name)


@app.route("/export/csv")
@login_required
def export_csv():
    with get_db() as conn:
        trips = conn.execute("SELECT * FROM trips ORDER BY date DESC, id DESC").fetchall()

    output = StringIO()
    writer = csv.writer(output)
    writer.writerow(["date", "purpose", "client", "start", "end", "miles", "deduction", "notes"])
    for trip in trips:
        writer.writerow([
            trip["date"],
            trip["purpose"],
            trip["client_name"],
            trip["start_location"],
            trip["end_location"],
            f"{trip['miles']:.2f}",
            f"{trip['deductible_amount']:.2f}",
            trip["notes"],
        ])
    filename = f"pookies-mileage-{date.today().isoformat()}.csv"
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@app.route("/stats")
@login_required
def stats():
    today_value = date.today()
    month_start, month_end = month_bounds(today_value)
    year_start = today_value.replace(month=1, day=1)
    month_summary = summary_between(month_start.isoformat(), month_end.isoformat())
    year_summary = summary_between(year_start.isoformat(), today_value.isoformat())
    all_summary = summary_between()

    days_elapsed = max((today_value - year_start).days + 1, 1)
    year_days = 366 if today_value.year % 4 == 0 else 365
    pace_miles = (year_summary["miles"] or 0) / days_elapsed * year_days
    pace_deduction = pace_miles * IRS_RATE

    with get_db() as conn:
        monthly_rows = conn.execute(
            """
            SELECT substr(date, 1, 7) AS month, COALESCE(SUM(miles), 0) AS miles,
                   COALESCE(SUM(deductible_amount), 0) AS deduction
            FROM trips GROUP BY month ORDER BY month
            """
        ).fetchall()
        destinations = conn.execute(
            """
            SELECT end_location, COUNT(*) AS visits, COALESCE(SUM(miles), 0) AS miles
            FROM trips WHERE TRIM(end_location) != ''
            GROUP BY end_location ORDER BY visits DESC, miles DESC LIMIT 8
            """
        ).fetchall()
        avg_row = conn.execute("SELECT COALESCE(AVG(miles), 0) FROM trips").fetchone()

    return render_template(
        "stats.html",
        month_summary=month_summary,
        year_summary=year_summary,
        all_summary=all_summary,
        pace_miles=pace_miles,
        pace_deduction=pace_deduction,
        monthly_labels=[row["month"] for row in monthly_rows],
        monthly_miles=[round(row["miles"], 2) for row in monthly_rows],
        monthly_deductions=[round(row["deduction"], 2) for row in monthly_rows],
        destinations=destinations,
        average_miles=avg_row[0],
    )


init_db()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5003, debug=True)
