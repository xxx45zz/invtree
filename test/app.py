from flask import Flask, render_template, request, redirect, url_for, flash, session
import sqlite3
from datetime import datetime
import threading
import subprocess
import platform
import socket
import time
import os

app = Flask(__name__)
app.secret_key = "supersecretkey"
app.config['SESSION_TYPE'] = 'filesystem'
DB = "inventory.db"

def get_db():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            price REAL NOT NULL,
            quantity INTEGER NOT NULL
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS sales (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id INTEGER,
            quantity INTEGER,
            total_price REAL,
            custom_price REAL,
            vat_applied TEXT,
            timestamp TEXT,
            FOREIGN KEY(product_id) REFERENCES products(id)
        )
    ''')
    conn.commit()
    conn.close()

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        if username == "admin" and password == "1234":
            session['logged_in'] = True
            return redirect(url_for('dashboard'))
        else:
            flash("Invalid username or password", "error")
            return redirect(url_for('login'))
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.pop('logged_in', None)
    flash("Logged out successfully.")
    return redirect(url_for('login'))

@app.route('/')
def dashboard():
    if not session.get('logged_in'):
        return redirect(url_for('login'))

    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT COUNT(*) FROM products')
    total_products = c.fetchone()[0]

    c.execute('SELECT SUM(price * quantity) FROM products')
    stock_value = c.fetchone()[0] or 0

    c.execute('SELECT * FROM products WHERE quantity < 5 ORDER BY quantity ASC')
    low_stock = c.fetchall()

    c.execute('SELECT SUM(quantity), SUM(total_price) FROM sales')
    sales_data = c.fetchone()
    total_items_sold = sales_data[0] or 0
    total_sales_value = sales_data[1] or 0

    return render_template('dashboard.html',
                           total_products=total_products,
                           stock_value=stock_value,
                           low_stock=low_stock,
                           total_items_sold=total_items_sold,
                           total_sales_value=total_sales_value)

@app.route('/inventory', methods=['GET', 'POST'])
def inventory():
    if not session.get('logged_in'):
        return redirect(url_for('login'))

    conn = get_db()
    c = conn.cursor()

    if request.method == 'POST':
        action = request.form.get('action')
        product_id = request.form.get('product_id')

        if action == "add_stock":
            qty = int(request.form.get('add_qty', 0))
            c.execute('UPDATE products SET quantity = quantity + ? WHERE id = ?', (qty, product_id))
            flash(f"Added {qty} items to product ID {product_id}")
        elif action == "subtract_stock":
            qty = int(request.form.get('subtract_qty', 0))
            c.execute('SELECT quantity FROM products WHERE id=?', (product_id,))
            current_qty = c.fetchone()[0]
            if qty > current_qty:
                flash("Cannot subtract more than current stock!", "error")
            else:
                c.execute('UPDATE products SET quantity = quantity - ? WHERE id = ?', (qty, product_id))
                flash(f"Subtracted {qty} items from product ID {product_id}")
        elif action == "add_product":
            name = request.form.get('name').strip()
            price = float(request.form.get('price'))
            quantity = int(request.form.get('quantity'))
            try:
                c.execute('INSERT INTO products (name, price, quantity) VALUES (?, ?, ?)',
                          (name, price, quantity))
                flash(f"Product {name} added!")
            except sqlite3.IntegrityError:
                flash("Product name must be unique!", "error")
        elif action == "delete_product": # New action for deleting products
            try:
                c.execute('DELETE FROM products WHERE id = ?', (product_id,))
                conn.commit()
                flash(f"Product ID {product_id} deleted successfully!", "success")
            except sqlite3.Error as e:
                flash(f"Error deleting product: {e}", "error")


        conn.commit()

    c.execute('SELECT * FROM products')
    products = c.fetchall()
    conn.close()
    return render_template('inventory.html', products=products)

@app.route('/pos', methods=['GET', 'POST'])
def pos():
    if not session.get('logged_in'):
        return redirect(url_for('login'))

    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT * FROM products WHERE quantity > 0')
    products = c.fetchall()

    if request.method == 'POST':
        product_id = int(request.form.get('product_id'))
        qty = int(request.form.get(f'qty_{product_id}') or 0)
        custom_price = float(request.form.get(f'price_{product_id}') or 0)
        vat_choice = request.form.get(f'vat_{product_id}')  # "yes" or "no"

        if qty <= 0:
            flash("Quantity must be greater than 0", "error")
            return redirect(url_for('pos'))

        # Validate product
        c.execute('SELECT * FROM products WHERE id = ?', (product_id,))
        product = c.fetchone()
        if not product:
            flash("Product not found!", "error")
            return redirect(url_for('pos'))

        if qty > product['quantity']:
            flash("Not enough stock!", "error")
            return redirect(url_for('pos'))

        # Apply VAT if selected
        vat_rate = 0.15 if vat_choice == "yes" else 0
        total_price = (custom_price * qty) * (1 + vat_rate)

        # Update product quantity
        c.execute('UPDATE products SET quantity = quantity - ? WHERE id = ?', (qty, product_id))

        # Insert into sales table
        c.execute('''
            INSERT INTO sales (product_id, quantity, total_price, custom_price, vat_applied, timestamp)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (product_id, qty, total_price, custom_price, vat_choice, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))

        conn.commit()
        flash(f"Sold {qty} x {product['name']} for ${total_price:.2f}")
        return redirect(url_for('pos'))

    conn.close()
    return render_template('pos.html', products=products)

@app.route('/sales', methods=['GET', 'POST']) # Added POST method for sales page
def sales():
    if not session.get('logged_in'):
        return redirect(url_for('login'))

    conn = get_db()
    c = conn.cursor()

    if request.method == 'POST':
        action = request.form.get('action')
        sale_id = request.form.get('sale_id')

        if action == "revoke_sale":
            try:
                c.execute('SELECT product_id, quantity FROM sales WHERE id = ?', (sale_id,))
                sale_record = c.fetchone()

                if sale_record:
                    product_id = sale_record['product_id']
                    quantity_sold = sale_record['quantity']

                    # Revert product quantity
                    c.execute('UPDATE products SET quantity = quantity + ? WHERE id = ?', (quantity_sold, product_id))
                    # Delete the sale record
                    c.execute('DELETE FROM sales WHERE id = ?', (sale_id,))
                    conn.commit()
                    flash(f"Sale ID {sale_id} revoked and stock replenished.", "success")
                else:
                    flash("Sale record not found.", "error")
            except sqlite3.Error as e:
                flash(f"Error revoking sale: {e}", "error")
        elif action == "delete_sale": # New action for deleting sales without replenishing stock
            try:
                c.execute('DELETE FROM sales WHERE id = ?', (sale_id,))
                conn.commit()
                flash(f"Sale ID {sale_id} permanently deleted.", "success")
            except sqlite3.Error as e:
                flash(f"Error deleting sale record: {e}", "error")

    c.execute('''
        SELECT sales.id, products.name as product_name, sales.quantity,
               sales.total_price, sales.custom_price, sales.vat_applied, sales.timestamp
        FROM sales
        JOIN products ON sales.product_id = products.id
        ORDER BY sales.timestamp DESC
    ''')
    sales_records = c.fetchall()
    conn.close()
    return render_template('sales.html', sales=sales_records)

def open_browser():
    time.sleep(1)
    ip_address = socket.gethostbyname(socket.gethostname())
    url = f"http://{ip_address}:5000"
    system = platform.system()

    if system == "Windows":
        chrome_path = r"C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe"
        if not os.path.exists(chrome_path):
            chrome_path = r"C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe"
        subprocess.Popen([chrome_path, url])
    elif system == "Darwin":
        subprocess.Popen(["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", url])
    elif system == "Linux":
        subprocess.Popen(["google-chrome", url])
    else:
        import webbrowser
        webbrowser.open(url)

if __name__ == '__main__':
    init_db()
    threading.Thread(target=open_browser).start()
    app.run(host='0.0.0.0', port=5000, debug=True)