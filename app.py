from flask import Flask, render_template, redirect, url_for, session, request
from flask_socketio import SocketIO

app = Flask(__name__)
app.secret_key = "smart_canteen_secret_change_later"
socketio = SocketIO(app, cors_allowed_origins="*")

# FOOD MENU AND PRICES
prices = {
    "Pav Bhaji": 50,
    "Poha": 25,
    "Maggi": 35,
    "Sandwich": 40,
    "Tea": 15,
    "Cold Coffee": 40,
    "Wada Pav": 20,
    "Veg Pulav": 50,
    "Samosa": 15
}

# TEMPORARY ORDER STORAGE
orders = []
next_token_number = 130

ALLOWED_STATUSES = [
    "Pending",
    "Preparing",
    "Ready",
    "Completed",
    "Cancelled"
]


def get_order_counts():
    return {
        "pending_count": sum(o["status"] == "Pending" for o in orders),
        "preparing_count": sum(o["status"] == "Preparing" for o in orders),
        "ready_count": sum(o["status"] == "Ready" for o in orders),
        "completed_count": sum(o["status"] == "Completed" for o in orders),
        "cancelled_count": sum(o["status"] == "Cancelled" for o in orders),
        "total_orders": len(orders)
    }


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
        "cart.html",
        cart_items=cart_items,
        prices=prices,
        total=total
    )


# CLEAR CART
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
        "checkout.html",
        cart_items=cart_items,
        prices=prices,
        total=total
    )


# PLACE ORDER
@app.route("/place_order", methods=["POST"])
def place_order():
    global next_token_number

    cart_items = session.get("cart", [])

    if not cart_items:
        return redirect(url_for("menu"))

    total = sum(prices.get(item, 0) for item in cart_items)

    new_order = {
        "token": f"A{next_token_number}",
        "items": cart_items.copy(),
        "total": total,
        "pickup": "Collect when your order is ready",
        "status": "Pending",
        "student_email": session.get("student_email", "Guest")
    }

    next_token_number += 1
    orders.append(new_order)

    session.pop("cart", None)
    session["last_order"] = new_order.copy()

    socketio.emit("new_order", new_order)

    return redirect(url_for("order_success"))


# ORDER SUCCESS / TOKEN
@app.route("/order-success")
def order_success():
    order = session.get("last_order")

    if not order:
        return redirect(url_for("menu"))

    return render_template(
        "order_success.html",
        order=order,
        prices=prices
    )


# MY ORDERS
@app.route("/my-orders")
def my_orders():
    return render_template("my_orders.html", orders=orders)


# STUDENT LOGIN
# Demo only: credentials are not saved or verified.
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "")

        if email and password:
            session.clear()
            session["student_email"] = email
            session["student_name"] = email.split("@")[0]
            return redirect(url_for("menu"))

        return render_template(
            "login.html",
            error="Please enter your email and password."
        )

    return render_template("login.html")


# STUDENT REGISTRATION
@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "")

        if name and email and password:
            session.clear()
            session["student_email"] = email
            session["student_name"] = name
            return redirect(url_for("menu"))

        return render_template(
            "register.html",
            error="Please complete all fields."
        )

    return render_template("register.html")


# STUDENT LOGOUT
@app.route("/student-logout")
def student_logout():
    session.clear()
    return redirect(url_for("home"))


# STAFF LOGIN
# Email: staff@smartcanteen.com
# Password: admin123
@app.route("/staff-login", methods=["GET", "POST"])
def staff_login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        if email == "staff@smartcanteen.com" and password == "admin123":
            session.clear()
            session["staff_logged_in"] = True
            return redirect(url_for("staff_dashboard"))

        return render_template(
            "staff_login.html",
            error="Invalid staff email or password."
        )

    return render_template("staff_login.html")


# STAFF DASHBOARD
@app.route("/staff-dashboard")
def staff_dashboard():
    if not session.get("staff_logged_in"):
        return redirect(url_for("staff_login"))

    return render_template(
        "staff_dashboard.html",
        orders=orders,
        **get_order_counts()
    )


# STAFF LOGOUT
@app.route("/staff-logout")
def staff_logout():
    session.pop("staff_logged_in", None)
    return redirect(url_for("staff_login"))


# ADMIN LOGIN
# Email: admin@smartcanteen.com
# Password: admin123
@app.route("/admin-login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        if email == "admin@smartcanteen.com" and password == "admin123":
            session.clear()
            session["admin_logged_in"] = True
            return redirect(url_for("admin_dashboard"))

        return render_template(
            "admin_login.html",
            error="Invalid admin email or password."
        )

    return render_template("admin_login.html")


# ADMIN DASHBOARD
@app.route("/admin")
def admin_dashboard():
    if not session.get("admin_logged_in"):
        return redirect(url_for("admin_login"))

    return render_template(
        "admin_dashboard.html",
        orders=orders,
        **get_order_counts()
    )


# ADMIN LOGOUT
@app.route("/admin-logout")
def admin_logout():
    session.pop("admin_logged_in", None)
    return redirect(url_for("admin_login"))


# UPDATE ORDER STATUS
@app.route("/update_order/<token>/<status>", methods=["GET", "POST"])
def update_order(token, status):
    is_staff = session.get("staff_logged_in")
    is_admin = session.get("admin_logged_in")

    if not (is_staff or is_admin):
        return redirect(url_for("staff_login"))

    if request.method == "POST":
        status = request.form.get("new_status", status)

    if status not in ALLOWED_STATUSES:
        if is_admin:
            return redirect(url_for("admin_dashboard"))
        return redirect(url_for("staff_dashboard"))

    for order in orders:
        if order["token"] == token:
            order["status"] = status

            socketio.emit(
                "order_status_updated",
                {
                    "token": token,
                    "status": status
                }
            )
            break

    if is_admin:
        return redirect(url_for("admin_dashboard"))

    return redirect(url_for("staff_dashboard"))


# RUN APPLICATION
if __name__ == "__main__":
    socketio.run(app, debug=True)
