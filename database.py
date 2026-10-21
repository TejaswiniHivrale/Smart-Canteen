import sqlite3
import json
from pathlib import Path
from functools import wraps
from werkzeug.security import generate_password_hash, check_password_hash
from flask import Flask, render_template, redirect, url_for, session, request, flash
from flask_socketio import SocketIO

app = Flask(__name__)
app.secret_key = "change_this_to_a_long_random_secret_before_deployment"
socketio = SocketIO(app, cors_allowed_origins="*")

BASE_DIR = Path(__file__).resolve().parent
DATABASE = BASE_DIR / "canteen.db"

prices = {
    "Pav Bhaji": 50,
    "Poha": 25,
    "Maggi": 35,
    "Sandwich": 40,
    "Tea": 15,
    "Cold Coffee": 40,
    "Wada Pav": 20,
    "Veg Pulav": 50,
    "Samosa": 15,
}
ALLOWED_STATUSES = ["Pending", "Preparing", "Ready", "Completed", "Cancelled"]


def get_db_connection():
    db = sqlite3.connect(DATABASE, timeout=10)
    db.row_factory = sqlite3.Row
    return db


def init_db():
    with get_db_connection() as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS students (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                email TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                token TEXT NOT NULL UNIQUE,
                student_email TEXT,
                items_json TEXT NOT NULL,
                total REAL NOT NULL,
                pickup TEXT NOT NULL DEFAULT 'Collect when your order is ready',
                status TEXT NOT NULL DEFAULT 'Pending',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS token_counter (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                next_number INTEGER NOT NULL
            )
        """)
        db.execute(
            "INSERT OR IGNORE INTO token_counter (id, next_number) VALUES (1, 130)"
        )


def order_to_dict(row):
    order = dict(row)
    order["items"] = json.loads(order.pop("items_json"))
    return order


def fetch_orders():
    with get_db_connection() as db:
        rows = db.execute(
            "SELECT * FROM orders ORDER BY id DESC"
        ).fetchall()
    return [order_to_dict(row) for row in rows]


def get_order_counts(orders):
    return {
        "pending_count": sum(o["status"] == "Pending" for o in orders),
        "preparing_count": sum(o["status"] == "Preparing" for o in orders),
        "ready_count": sum(o["status"] == "Ready" for o in orders),
        "completed_count": sum(o["status"] == "Completed" for o in orders),
        "cancelled_count": sum(o["status"] == "Cancelled" for o in orders),
        "total_orders": len(orders),
    }


def staff_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("staff_logged_in"):
            return redirect(url_for("staff_login"))
        return view(*args, **kwargs)
    return wrapped


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("admin_logged_in"):
            return redirect(url_for("admin_login"))
        return view(*args, **kwargs)
    return wrapped


def staff_or_admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not (session.get("staff_logged_in") or session.get("admin_logged_in")):
            return redirect(url_for("home"))
        return view(*args, **kwargs)
    return wrapped


# HOME
@app.route("/")
def home():
    return render_template("home.html")


# STUDENT MENU
@app.route("/menu")
def menu():
    return render_template("menu.html", prices=prices)


# ADD ITEM TO CART
@app.route("/add_to_cart/<item>")
def add_to_cart(item):
    if item not in prices:
        return redirect(url_for("menu"))
    quantity = request.args.get("quantity", default=1, type=int)
    quantity = max(1, min(quantity or 1, 20))
    cart_items = session.get("cart", [])
    cart_items.extend([item] * quantity)
    session["cart"] = cart_items
    return redirect(url_for("cart"))


# CART
@app.route("/cart")
def cart():
    cart_items = session.get("cart", [])
    total = sum(prices.get(item, 0) for item in cart_items)
    return render_template(
        "cart.html", cart_items=cart_items, prices=prices, total=total
    )


@app.route("/clear_cart")
def clear_cart():
    session.pop("cart", None)
    return redirect(url_for("cart"))


# CHECKOUT
@app.route("/checkout", methods=["GET", "POST"])
def checkout():
    cart_items = session.get("cart", [])
    if not cart_items:
        return redirect(url_for("menu"))
    total = sum(prices.get(item, 0) for item in cart_items)
    return render_template(
        "checkout.html", cart_items=cart_items, prices=prices, total=total
    )


# PLACE ORDER — saves order permanently in SQLite
@app.route("/place_order", methods=["POST"])
def place_order():
    cart_items = session.get("cart", [])
    if not cart_items or any(item not in prices for item in cart_items):
        return redirect(url_for("menu"))

    total = sum(prices[item] for item in cart_items)
    student_email = session.get("student_email", "Guest")
    items_json = json.dumps(cart_items)

    try:
        with get_db_connection() as db:
            # Counter update is in the same transaction as order creation.
            row = db.execute(
                "SELECT next_number FROM token_counter WHERE id = 1"
            ).fetchone()
            token_number = row["next_number"]
            token = f"A{token_number}"

            db.execute(
                "UPDATE token_counter SET next_number = ? WHERE id = 1",
                (token_number + 1,)
            )
            db.execute("""
                INSERT INTO orders (token, student_email, items_json, total, status)
                VALUES (?, ?, ?, ?, 'Pending')
            """, (token, student_email, items_json, total))
    except sqlite3.Error:
        app.logger.exception("Could not save order to database")
        return "Order could not be saved. Please try again.", 500

    new_order = {
        "token": token,
        "items": cart_items.copy(),
        "total": total,
        "pickup": "Collect when your order is ready",
        "status": "Pending",
        "student_email": student_email,
    }
    session.pop("cart", None)
    session["last_order"] = new_order
    socketio.emit("new_order", new_order)
    return redirect(url_for("order_success"))


@app.route("/order-success")
def order_success():
    order = session.get("last_order")
    if not order:
        return redirect(url_for("menu"))
    return render_template("order_success.html", order=order, prices=prices)


@app.route("/my-orders")
def my_orders():
    if not session.get("student_email"):
        return redirect(url_for("login"))
    with get_db_connection() as db:
        rows = db.execute(
            "SELECT * FROM orders WHERE student_email = ? ORDER BY id DESC",
            (session["student_email"],)
        ).fetchall()
    user_orders = [order_to_dict(row) for row in rows]
    return render_template("my_orders.html", orders=user_orders)


# STUDENT REGISTRATION
@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        if not name or not email or not password:
            return render_template("register.html", error="Please complete all fields.")
        if len(password) < 6:
            return render_template("register.html", error="Password must be at least 6 characters.")

        password_hash = generate_password_hash(password)
        try:
            with get_db_connection() as db:
                db.execute(
                    "INSERT INTO students (name, email, password_hash) VALUES (?, ?, ?)",
                    (name, email, password_hash)
                )
        except sqlite3.IntegrityError:
            return render_template("register.html", error="This email is already registered.")
        except sqlite3.Error:
            app.logger.exception("Registration database error")
            return render_template("register.html", error="Registration failed. Please try again.")

        session.clear()
        session["student_email"] = email
        session["student_name"] = name
        return redirect(url_for("menu"))

    return render_template("register.html")


# STUDENT LOGIN — checks SQLite credentials
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        with get_db_connection() as db:
            student = db.execute(
                "SELECT * FROM students WHERE email = ?", (email,)
            ).fetchone()

        if student and check_password_hash(student["password_hash"], password):
            session.clear()
            session["student_email"] = student["email"]
            session["student_name"] = student["name"]
            return redirect(url_for("menu"))

        return render_template("login.html", error="Invalid email or password. Register first if you are new.")

    return render_template("login.html")


@app.route("/student-logout")
def student_logout():
    session.clear()
    return redirect(url_for("home"))


# STAFF LOGIN (demo credentials; configure securely for real deployment)
# Email: staff@smartcanteen.com | Password: admin123
@app.route("/staff-login", methods=["GET", "POST"])
def staff_login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        if email == "staff@smartcanteen.com" and password == "admin123":
            session.clear()
            session["staff_logged_in"] = True
            return redirect(url_for("staff_dashboard"))
        return render_template("staff_login.html", error="Invalid staff email or password.")
    return render_template("staff_login.html")


@app.route("/staff-dashboard")
@staff_required
def staff_dashboard():
    all_orders = fetch_orders()
    return render_template(
        "staff_dashboard.html",
        orders=all_orders,
        **get_order_counts(all_orders)
    )


@app.route("/staff-logout")
def staff_logout():
    session.pop("staff_logged_in", None)
    return redirect(url_for("staff_login"))


# ADMIN LOGIN (demo credentials; configure securely for real deployment)
# Email: admin@smartcanteen.com | Password: admin123
@app.route("/admin-login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        if email == "admin@smartcanteen.com" and password == "admin123":
            session.clear()
            session["admin_logged_in"] = True
            return redirect(url_for("admin_dashboard"))
        return render_template("admin_login.html", error="Invalid admin email or password.")
    return render_template("admin_login.html")


@app.route("/admin")
@admin_required
def admin_dashboard():
    all_orders = fetch_orders()
    return render_template(
        "admin_dashboard.html",
        orders=all_orders,
        **get_order_counts(all_orders)
    )


@app.route("/admin-logout")
def admin_logout():
    session.pop("admin_logged_in", None)
    return redirect(url_for("admin_login"))


# UPDATE ORDER STATUS
@app.route("/update_order/<token>/<status>", methods=["GET", "POST"])
@staff_or_admin_required
def update_order(token, status):
    if request.method == "POST":
        status = request.form.get("new_status", status)

    if status not in ALLOWED_STATUSES:
        flash("Invalid order status.")
        return redirect(url_for("admin_dashboard") if session.get("admin_logged_in")
                        else url_for("staff_dashboard"))

    with get_db_connection() as db:
        result = db.execute(
            "UPDATE orders SET status = ? WHERE token = ?",
            (status, token)
        )
        found = result.rowcount > 0

    if found:
        socketio.emit("order_status_updated", {"token": token, "status": status})
    else:
        flash("Order not found.")

    if session.get("admin_logged_in"):
        return redirect(url_for("admin_dashboard"))
    return redirect(url_for("staff_dashboard"))


# Initialize tables automatically when the app starts
init_db()


if __name__ == "__main__":
    socketio.run(app, debug=True)
