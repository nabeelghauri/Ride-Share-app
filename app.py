import sqlite3
import os
from datetime import datetime
from flask import Flask, request, jsonify, g, render_template

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "rideshare.db")

app = Flask(
    __name__,
    static_folder="static",
    template_folder="templates"
)

# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


@app.teardown_appcontext
def close_db(exception=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    fresh = not os.path.exists(DB_PATH)
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            name          TEXT NOT NULL,
            email         TEXT UNIQUE NOT NULL,
            password      TEXT NOT NULL,
            role          TEXT NOT NULL CHECK(role IN ('rider','driver')),
            vehicle       TEXT,
            is_online     INTEGER NOT NULL DEFAULT 0,
            rating_total  REAL NOT NULL DEFAULT 0,
            rating_count  INTEGER NOT NULL DEFAULT 0,
            created_at    TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS rides (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            rider_id      INTEGER NOT NULL REFERENCES users(id),
            driver_id     INTEGER REFERENCES users(id),
            pickup        TEXT NOT NULL,
            dropoff       TEXT NOT NULL,
            pooled        INTEGER NOT NULL DEFAULT 0,
            status        TEXT NOT NULL DEFAULT 'requested'
                          CHECK(status IN ('requested','accepted','ongoing','completed','cancelled')),
            fare          REAL NOT NULL,
            distance_km   REAL NOT NULL,
            created_at    TEXT NOT NULL,
            accepted_at   TEXT,
            completed_at  TEXT,
            cancelled_by  TEXT
        );

        CREATE TABLE IF NOT EXISTS ratings (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            ride_id       INTEGER NOT NULL REFERENCES rides(id),
            from_user     INTEGER NOT NULL REFERENCES users(id),
            to_user       INTEGER NOT NULL REFERENCES users(id),
            stars         INTEGER NOT NULL CHECK(stars BETWEEN 1 AND 5),
            comment       TEXT,
            created_at    TEXT NOT NULL,
            UNIQUE(ride_id, from_user)
        );
        """
    )
    ride_columns = {row[1] for row in conn.execute("PRAGMA table_info(rides)")}
    for column in ("pickup_lat", "pickup_lng", "dropoff_lat", "dropoff_lng"):
        if column not in ride_columns:
            conn.execute(f"ALTER TABLE rides ADD COLUMN {column} REAL")
    if "vehicle_category" not in ride_columns:
        conn.execute("ALTER TABLE rides ADD COLUMN vehicle_category TEXT")
    user_columns = {row[1] for row in conn.execute("PRAGMA table_info(users)")}
    if "city" not in user_columns:
        conn.execute("ALTER TABLE users ADD COLUMN city TEXT")
    for column in ("vehicle_make", "vehicle_model", "vehicle_year", "vehicle_front_image", "vehicle_back_image"):
        if column not in user_columns:
            conn.execute(f"ALTER TABLE users ADD COLUMN {column} TEXT")
    if "personal_image" not in user_columns:
        conn.execute("ALTER TABLE users ADD COLUMN personal_image TEXT")
    conn.commit()
    conn.close()
    return fresh


# ---------------------------------------------------------------------------
# Small utilities
# ---------------------------------------------------------------------------

def now():
    return datetime.utcnow().isoformat(timespec="seconds")


def estimate_trip(pickup, dropoff, vehicle_category):
    """Return a deterministic distance and base-plus-distance PKR fare."""
    seed = abs(hash(pickup.strip().lower())) % 17 + abs(hash(dropoff.strip().lower())) % 13
    distance_km = round(2.5 + (seed % 15) * 0.8, 1)
    base_fare = 70 if vehicle_category == "bike" else 100
    return distance_km, round(base_fare + distance_km * 10, 2)


def user_public(row, include_vehicle_images=False):
    if row is None:
        return None
    avg = round(row["rating_total"] / row["rating_count"], 2) if row["rating_count"] else None
    return {
        "id": row["id"],
        "name": row["name"],
        "email": row["email"],
        "role": row["role"],
        "vehicle": row["vehicle"],
        "vehicle_make": row["vehicle_make"],
        "vehicle_model": row["vehicle_model"],
        "vehicle_year": row["vehicle_year"],
        "city": row["city"],
        "is_online": bool(row["is_online"]),
        "rating_avg": avg,
        "rating_count": row["rating_count"],
        "personal_image": row["personal_image"],
        **({
            "vehicle_front_image": row["vehicle_front_image"],
            "vehicle_back_image": row["vehicle_back_image"],
        } if include_vehicle_images else {}),
    }


def ride_public(row, db):
    rider = db.execute("SELECT * FROM users WHERE id=?", (row["rider_id"],)).fetchone()
    driver = None
    if row["driver_id"]:
        driver = db.execute("SELECT * FROM users WHERE id=?", (row["driver_id"],)).fetchone()
    return {
        "id": row["id"],
        "pickup": row["pickup"],
        "dropoff": row["dropoff"],
        "vehicle_category": row["vehicle_category"],
        "pooled": bool(row["pooled"]),
        "status": row["status"],
        "fare": row["fare"],
        "distance_km": row["distance_km"],
        "pickup_lat": row["pickup_lat"],
        "pickup_lng": row["pickup_lng"],
        "dropoff_lat": row["dropoff_lat"],
        "dropoff_lng": row["dropoff_lng"],
        "created_at": row["created_at"],
        "accepted_at": row["accepted_at"],
        "completed_at": row["completed_at"],
        "cancelled_by": row["cancelled_by"],
        "rider": user_public(rider),
        "driver": user_public(driver),
    }


def error(msg, code=400):
    return jsonify({"error": msg}), code


# ---------------------------------------------------------------------------
# Static frontend
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    print("Flask template folder:", app.template_folder)
    print("Flask root path:", app.root_path)
    print("Templates folder exists:", os.path.exists(os.path.join(app.root_path, "templates")))
    print("index.html exists:", os.path.exists(os.path.join(app.root_path, "templates", "index.html")))
    
    return render_template("index.html")

# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

@app.post("/api/register")
def register():
    data = request.get_json(force=True, silent=True) or {}
    name = (data.get("name") or "").strip()
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""
    role = data.get("role")
    vehicle = (data.get("vehicle") or "").strip() or None

    if not name or not email or not password or role not in ("rider", "driver"):
        return error("name, email, password and a valid role are required")
    if role == "driver" and not vehicle:
        return error("vehicle details are required for drivers")

    db = get_db()
    try:
        cur = db.execute(
            "INSERT INTO users (name, email, password, role, vehicle, is_online, created_at) "
            "VALUES (?, ?, ?, ?, ?, 0, ?)",
            (name, email, password, role, vehicle, now()),
        )
        db.commit()
    except sqlite3.IntegrityError:
        return error("an account with that email already exists", 409)

    row = db.execute("SELECT * FROM users WHERE id=?", (cur.lastrowid,)).fetchone()
    return jsonify(user_public(row)), 201


@app.post("/api/login")
def login():
    data = request.get_json(force=True, silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""

    db = get_db()
    row = db.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
    if row is None or row["password"] != password:
        return error("invalid email or password", 401)
    return jsonify(user_public(row, include_vehicle_images=True))


# ---------------------------------------------------------------------------
# Driver status
# ---------------------------------------------------------------------------

@app.post("/api/driver/<int:driver_id>/status")
def set_driver_status(driver_id):
    data = request.get_json(force=True, silent=True) or {}
    online = bool(data.get("is_online"))

    db = get_db()
    row = db.execute("SELECT * FROM users WHERE id=? AND role='driver'", (driver_id,)).fetchone()
    if row is None:
        return error("driver not found", 404)

    db.execute("UPDATE users SET is_online=? WHERE id=?", (1 if online else 0, driver_id))
    db.commit()
    row = db.execute("SELECT * FROM users WHERE id=?", (driver_id,)).fetchone()
    return jsonify(user_public(row))


# ---------------------------------------------------------------------------
# Rides — rider side
# ---------------------------------------------------------------------------

@app.post("/api/rides/request")
def request_ride():
    data = request.get_json(force=True, silent=True) or {}
    rider_id = data.get("rider_id")
    pickup = (data.get("pickup") or "").strip()
    dropoff = (data.get("dropoff") or "").strip()
    vehicle_category = (data.get("vehicle_category") or "").strip().lower()
    pooled = bool(data.get("pooled"))
    try:
        pickup_lat = float(data["pickup_lat"])
        pickup_lng = float(data["pickup_lng"])
        dropoff_lat = float(data["dropoff_lat"])
        dropoff_lng = float(data["dropoff_lng"])
    except (KeyError, TypeError, ValueError):
        return error("pickup and drop-off map locations are required")

    if not rider_id or not pickup or not dropoff or vehicle_category not in (
        "bike", "car", "city-to-city", "courier"
    ):
        return error("rider_id, pickup, dropoff and a valid vehicle category are required")

    db = get_db()
    rider = db.execute("SELECT * FROM users WHERE id=? AND role='rider'", (rider_id,)).fetchone()
    if rider is None:
        return error("rider not found", 404)

    existing = db.execute(
        "SELECT 1 FROM rides WHERE rider_id=? AND status IN ('requested','accepted','ongoing')",
        (rider_id,),
    ).fetchone()
    if existing:
        return error("you already have an active ride", 409)

    distance_km, fare = estimate_trip(pickup, dropoff, vehicle_category)
    if pooled:
        fare = round(fare * 0.72, 2)  # pooled discount

    cur = db.execute(
        "INSERT INTO rides (rider_id, pickup, dropoff, pickup_lat, pickup_lng, "
        "dropoff_lat, dropoff_lng, vehicle_category, pooled, status, fare, distance_km, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'requested', ?, ?, ?)",
        (rider_id, pickup, dropoff, pickup_lat, pickup_lng, dropoff_lat, dropoff_lng,
         vehicle_category, 1 if pooled else 0, fare, distance_km, now()),
    )
    db.commit()
    row = db.execute("SELECT * FROM rides WHERE id=?", (cur.lastrowid,)).fetchone()
    return jsonify(ride_public(row, db)), 201


@app.post("/api/rides/<int:ride_id>/cancel")
def cancel_ride(ride_id):
    data = request.get_json(force=True, silent=True) or {}
    by = data.get("by", "rider")  # 'rider' or 'driver'

    db = get_db()
    row = db.execute("SELECT * FROM rides WHERE id=?", (ride_id,)).fetchone()
    if row is None:
        return error("ride not found", 404)
    if row["status"] in ("completed", "cancelled"):
        return error("ride can no longer be cancelled", 409)

    db.execute(
        "UPDATE rides SET status='cancelled', cancelled_by=? WHERE id=?", (by, ride_id)
    )
    db.commit()
    row = db.execute("SELECT * FROM rides WHERE id=?", (ride_id,)).fetchone()
    return jsonify(ride_public(row, db))


# ---------------------------------------------------------------------------
# Rides — driver side
# ---------------------------------------------------------------------------

@app.get("/api/rides/available")
def available_rides():
    db = get_db()
    rows = db.execute(
        "SELECT * FROM rides WHERE status='requested' ORDER BY created_at ASC"
    ).fetchall()
    return jsonify([ride_public(r, db) for r in rows])


@app.post("/api/rides/<int:ride_id>/accept")
def accept_ride(ride_id):
    data = request.get_json(force=True, silent=True) or {}
    driver_id = data.get("driver_id")
    if not driver_id:
        return error("driver_id is required")

    db = get_db()
    driver = db.execute("SELECT * FROM users WHERE id=? AND role='driver'", (driver_id,)).fetchone()
    if driver is None:
        return error("driver not found", 404)
    if not driver["is_online"]:
        return error("go online before accepting rides", 409)

    active = db.execute(
        "SELECT 1 FROM rides WHERE driver_id=? AND status IN ('accepted','ongoing')", (driver_id,)
    ).fetchone()
    if active:
        return error("you already have an active ride", 409)

    row = db.execute("SELECT * FROM rides WHERE id=?", (ride_id,)).fetchone()
    if row is None:
        return error("ride not found", 404)
    if row["status"] != "requested":
        return error("ride is no longer available", 409)

    db.execute(
        "UPDATE rides SET driver_id=?, status='accepted', accepted_at=? WHERE id=?",
        (driver_id, now(), ride_id),
    )
    db.commit()
    row = db.execute("SELECT * FROM rides WHERE id=?", (ride_id,)).fetchone()
    return jsonify(ride_public(row, db))


@app.post("/api/rides/<int:ride_id>/start")
def start_ride(ride_id):
    db = get_db()
    row = db.execute("SELECT * FROM rides WHERE id=?", (ride_id,)).fetchone()
    if row is None:
        return error("ride not found", 404)
    if row["status"] != "accepted":
        return error("ride must be accepted before it can start", 409)
    db.execute("UPDATE rides SET status='ongoing' WHERE id=?", (ride_id,))
    db.commit()
    row = db.execute("SELECT * FROM rides WHERE id=?", (ride_id,)).fetchone()
    return jsonify(ride_public(row, db))


@app.post("/api/rides/<int:ride_id>/complete")
def complete_ride(ride_id):
    db = get_db()
    row = db.execute("SELECT * FROM rides WHERE id=?", (ride_id,)).fetchone()
    if row is None:
        return error("ride not found", 404)
    if row["status"] not in ("accepted", "ongoing"):
        return error("ride is not in progress", 409)
    db.execute(
        "UPDATE rides SET status='completed', completed_at=? WHERE id=?", (now(), ride_id)
    )
    db.commit()
    row = db.execute("SELECT * FROM rides WHERE id=?", (ride_id,)).fetchone()
    return jsonify(ride_public(row, db))


# ---------------------------------------------------------------------------
# History & profile
# ---------------------------------------------------------------------------

@app.get("/api/rides/history/<int:user_id>")
def ride_history(user_id):
    db = get_db()
    user = db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    if user is None:
        return error("user not found", 404)

    col = "rider_id" if user["role"] == "rider" else "driver_id"
    rows = db.execute(
        f"SELECT * FROM rides WHERE {col}=? ORDER BY created_at DESC", (user_id,)
    ).fetchall()
    return jsonify([ride_public(r, db) for r in rows])


@app.get("/api/rides/<int:ride_id>")
def get_ride(ride_id):
    db = get_db()
    row = db.execute("SELECT * FROM rides WHERE id=?", (ride_id,)).fetchone()
    if row is None:
        return error("ride not found", 404)
    return jsonify(ride_public(row, db))


@app.get("/api/users/<int:user_id>")
def get_user(user_id):
    db = get_db()
    row = db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    if row is None:
        return error("user not found", 404)
    return jsonify(user_public(row))


@app.patch("/api/users/<int:user_id>")
def update_user(user_id):
    data = request.get_json(force=True, silent=True) or {}
    name = (data.get("name") or "").strip()
    email = (data.get("email") or "").strip().lower()
    city = (data.get("city") or "").strip() or None
    password = data.get("password") or ""

    if not name or not email:
        return error("name and email are required")

    db = get_db()
    user = db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    if user is None:
        return error("user not found", 404)
    vehicle = (data.get("vehicle") or "").strip() or user["vehicle"]
    if user["role"] == "driver" and not vehicle:
        return error("vehicle details are required for drivers")
    vehicle_make = (data.get("vehicle_make") or "").strip() or user["vehicle_make"]
    vehicle_model = (data.get("vehicle_model") or "").strip() or user["vehicle_model"]
    vehicle_year = (data.get("vehicle_year") or "").strip() or user["vehicle_year"]
    vehicle_front_image = data.get("vehicle_front_image") or user["vehicle_front_image"]
    vehicle_back_image = data.get("vehicle_back_image") or user["vehicle_back_image"]
    personal_image = data.get("personal_image") or user["personal_image"]

    try:
        if password:
            db.execute(
                "UPDATE users SET name=?, email=?, vehicle=?, city=?, vehicle_make=?, vehicle_model=?, vehicle_year=?, vehicle_front_image=?, vehicle_back_image=?, personal_image=?, password=? WHERE id=?",
                (name, email, vehicle, city, vehicle_make, vehicle_model, vehicle_year, vehicle_front_image, vehicle_back_image, personal_image, password, user_id),
            )
        else:
            db.execute(
                "UPDATE users SET name=?, email=?, vehicle=?, city=?, vehicle_make=?, vehicle_model=?, vehicle_year=?, vehicle_front_image=?, vehicle_back_image=?, personal_image=? WHERE id=?",
                (name, email, vehicle, city, vehicle_make, vehicle_model, vehicle_year, vehicle_front_image, vehicle_back_image, personal_image, user_id),
            )
        db.commit()
    except sqlite3.IntegrityError:
        return error("an account with that email already exists", 409)

    updated = db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    return jsonify(user_public(updated, include_vehicle_images=True))


# ---------------------------------------------------------------------------
# Ratings
# ---------------------------------------------------------------------------

@app.post("/api/rides/<int:ride_id>/rate")
def rate_ride(ride_id):
    data = request.get_json(force=True, silent=True) or {}
    from_user = data.get("from_user")
    stars = data.get("stars")
    comment = (data.get("comment") or "").strip() or None

    if not from_user or stars not in (1, 2, 3, 4, 5):
        return error("from_user and stars (1-5) are required")

    db = get_db()
    row = db.execute("SELECT * FROM rides WHERE id=?", (ride_id,)).fetchone()
    if row is None:
        return error("ride not found", 404)
    if row["status"] != "completed":
        return error("only completed rides can be rated", 409)

    if from_user != row["rider_id"]:
        return error("you were not part of this ride", 403)
    to_user = row["driver_id"]

    if to_user is None:
        return error("nothing to rate on this ride", 409)

    try:
        db.execute(
            "INSERT INTO ratings (ride_id, from_user, to_user, stars, comment, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (ride_id, from_user, to_user, stars, comment, now()),
        )
    except sqlite3.IntegrityError:
        return error("you already rated this ride", 409)

    db.execute(
        "UPDATE users SET rating_total = rating_total + ?, rating_count = rating_count + 1 WHERE id=?",
        (stars, to_user),
    )
    db.commit()
    target = db.execute("SELECT * FROM users WHERE id=?", (to_user,)).fetchone()
    return jsonify(user_public(target)), 201


if __name__ == "__main__":
    created = init_db()
    if created:
        print(f"Initialized new database at {DB_PATH}")
    app.run(host="0.0.0.0", debug=False, port=5000)
