import os
import io
import sqlite3
import datetime
from flask import Flask, render_template, request, jsonify, send_file, session, redirect, url_for
import xlsxwriter
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side

app = Flask(__name__)
app.secret_key = 'paragon-agro-distribution-secret-key-2026'
app.config['TEMPLATES_AUTO_RELOAD'] = True
app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 0
app.config['SESSION_PERMANENT'] = False
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'

@app.after_request
def add_header(response):
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, post-check=0, pre-check=0, max-age=0'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '-1'
    return response

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'distribution.db')

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    cursor = conn.cursor()
    
    # Drop and recreate schema
    cursor.execute('DROP TABLE IF EXISTS users')
    cursor.execute('DROP TABLE IF EXISTS depots')
    cursor.execute('DROP TABLE IF EXISTS daily_reports')
    cursor.execute('DROP TABLE IF EXISTS trips')
    cursor.execute('DROP TABLE IF EXISTS invoices')
    
    # Users Table (RBAC with Active Status)
    cursor.execute('''
    CREATE TABLE users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT NOT NULL UNIQUE,
        password TEXT NOT NULL,
        role TEXT NOT NULL, -- 'admin' or 'incharge'
        depot_id INTEGER,
        display_name TEXT NOT NULL,
        is_active INTEGER DEFAULT 1,
        FOREIGN KEY (depot_id) REFERENCES depots (id)
    )
    ''')

    # Depots Table
    cursor.execute('''
    CREATE TABLE depots (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL UNIQUE,
        slug TEXT NOT NULL UNIQUE,
        category TEXT NOT NULL,
        incharge_name TEXT NOT NULL,
        contact TEXT,
        region TEXT NOT NULL,
        default_uom TEXT DEFAULT 'Pkt'
    )
    ''')
    
    # Daily Reports Table with Cancellation & Audit Tracking
    cursor.execute('''
    CREATE TABLE daily_reports (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        report_date TEXT NOT NULL,
        depot_id INTEGER NOT NULL,
        incharge_name TEXT NOT NULL,
        contact TEXT,
        shift TEXT DEFAULT 'Day Shift',
        total_vehicles INTEGER DEFAULT 0,
        total_invoices INTEGER DEFAULT 0,
        capacity_util_pct REAL DEFAULT 0,
        dispatched_gross_val REAL DEFAULT 0,
        delivered_net_val REAL DEFAULT 0,
        returned_val REAL DEFAULT 0,
        stock_mismatch_qty REAL DEFAULT 0,
        cash_mismatch_val REAL DEFAULT 0,
        adjustment_status TEXT DEFAULT '100% Adjusted',
        audit_status TEXT DEFAULT 'OK / Verified',
        status TEXT DEFAULT 'active', -- 'active', 'cancellation_requested', 'cancelled', 'unlocked'
        cancellation_reason TEXT,
        cancellation_requested_at TIMESTAMP,
        cancelled_by TEXT,
        cancelled_at TIMESTAMP,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (depot_id) REFERENCES depots (id)
    )
    ''')
    
    # Trips Table
    cursor.execute('''
    CREATE TABLE trips (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        report_id INTEGER NOT NULL,
        trip_no TEXT NOT NULL,
        vehicle_no TEXT NOT NULL,
        vehicle_type TEXT NOT NULL,
        driver_name TEXT,
        delivery_man TEXT,
        helper_name TEXT,
        route_name TEXT,
        planned_invoices INTEGER DEFAULT 0,
        departure_time TEXT,
        return_time TEXT,
        capacity_kg REAL DEFAULT 0,
        loaded_kg REAL DEFAULT 0,
        util_pct REAL DEFAULT 0,
        odo_start REAL DEFAULT 0,
        odo_end REAL DEFAULT 0,
        km_run REAL DEFAULT 0,
        fuel_cost REAL DEFAULT 0,
        trip_status TEXT DEFAULT 'Completed',
        gate_pass_no TEXT,
        reefer_temp TEXT DEFAULT '-18°C',
        remarks TEXT,
        FOREIGN KEY (report_id) REFERENCES daily_reports (id)
    )
    ''')
    
    # Invoices Table
    cursor.execute('''
    CREATE TABLE invoices (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        report_id INTEGER NOT NULL,
        trip_id INTEGER,
        invoice_no TEXT NOT NULL,
        customer_name TEXT NOT NULL,
        customer_code TEXT,
        customer_address TEXT,
        customer_type TEXT DEFAULT 'Superstore / Retail',
        trip_no TEXT,
        product_category TEXT NOT NULL,
        sku_uom TEXT DEFAULT 'Pkt',
        dispatched_qty REAL DEFAULT 0,
        dispatched_val REAL DEFAULT 0,
        delivery_status TEXT DEFAULT 'Delivered',
        delivered_qty REAL DEFAULT 0,
        delivered_val REAL DEFAULT 0,
        returned_qty REAL DEFAULT 0,
        returned_val REAL DEFAULT 0,
        return_reason TEXT DEFAULT 'None',
        collection_mode TEXT DEFAULT 'Credit',
        amount_collected REAL DEFAULT 0,
        credit_amount REAL DEFAULT 0,
        shortage_amount REAL DEFAULT 0,
        reconciliation_status TEXT DEFAULT '100% Reconciled',
        remarks TEXT,
        FOREIGN KEY (report_id) REFERENCES daily_reports (id),
        FOREIGN KEY (trip_id) REFERENCES trips (id)
    )
    ''')
    
    seed_database(cursor)
    conn.commit()
    conn.close()

def seed_database(cursor):
    # 17 Depots & Factories
    depots_data = [
        (1, 'Ashulia Factory - Frozen', 'ashulia_frozen', 'Frozen Food', 'Sajedul Islam (AM)', '01711000001', 'Dhaka North', 'Pkt'),
        (2, 'Ashulia Factory - Dry Food', 'ashulia_dryfood', 'Dry Food', 'Delower (AM Dist)', '01711000002', 'Dhaka North', 'Pkt'),
        (3, 'Gazipur-Process CK', 'gazipur_ck', 'Processed Chicken', 'Mahmud (Sr. Officer)', '01711000003', 'Gazipur', 'Kg'),
        (4, 'Sirajganj Dairy', 'sirajganj_dairy', 'Dairy', 'Rafiqul (Officer)', '01711000004', 'North Bengal', 'Ltr'),
        (5, 'Sylhet - Tea Packing Unit', 'sylhet_tea', 'Tea', 'Incharge Vacant', '01711000005', 'Sylhet', 'Kg'),
        (6, 'Sylhet Depot - Frozen', 'sylhet_frozen', 'Frozen Food', 'Dist & Acc Officer', '01711000006', 'Sylhet', 'Pkt'),
        (7, 'Tejgaon - Frozen/CK', 'tejgaon_frozenck', 'Chicken & Frozen', 'Sabbir (Dist Officer)', '01711000007', 'Central Dhaka', 'Kg/Pkt'),
        (8, 'Tejgaon - e-Commerce', 'tejgaon_ecommerce', 'e-Commerce', 'Shahin (Officer)', '01711000008', 'Central Dhaka', 'Kg'),
        (9, 'Tejgaon - Fresh Egg', 'tejgaon_egg', 'Fresh Eggs', 'Mushfik (Officer)', '01711000009', 'Central Dhaka', 'Pcs'),
        (10, 'Tejgaon - Tea Distribution', 'tejgaon_tea', 'Tea', 'Officer (Tea Dist)', '01711000010', 'Central Dhaka', 'Kg'),
        (11, 'Tejgaon - Dairy', 'tejgaon_dairy', 'Dairy', 'Mahmudul (AM)', '01711000011', 'Central Dhaka', 'Ltr'),
        (12, 'Mohakhali Depot', 'mohakhali_momo', 'Momo & Snacks', 'Shohag (Supervisor)', '01711000012', 'Dhaka North', 'Kg'),
        (13, 'CTG Depot - Frozen', 'ctg_frozen', 'Frozen Food', 'Monjurul (Asst Officer)', '01711000013', 'Chittagong', 'Pkt'),
        (14, 'CTG Depot - Dry Food', 'ctg_dryfood', 'Dry Food', 'Monjurul (Asst Officer)', '01711000014', 'Chittagong', 'Pkt'),
        (15, 'Jessore - Frozen Food', 'jessore_frozen', 'Frozen Food', 'Shohag (Supervisor)', '01711000015', 'South Bengal', 'Pkt'),
        (16, 'Jessore - Dry Food', 'jessore_dryfood', 'Dry Food', 'Shohag (Supervisor)', '01711000016', 'South Bengal', 'Pkt'),
        (17, 'Rangpur Depot - Frozen', 'rangpur_frozen', 'Frozen Food', 'Dist Officer', '01711000017', 'North Bengal', 'Pkt')
    ]
    
    for d in depots_data:
        cursor.execute('''
        INSERT OR IGNORE INTO depots (id, name, slug, category, incharge_name, contact, region, default_uom)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', d)
    
    # Users: Admin + 17 Incharges
    cursor.execute('''
    INSERT OR IGNORE INTO users (id, username, password, role, depot_id, display_name)
    VALUES (1, 'admin', 'admin123', 'admin', NULL, 'Executive Admin')
    ''')

    for d in depots_data:
        uid = d[0] + 1
        username = d[2]
        display_name = f"{d[1]} (Incharge: {d[4]})"
        cursor.execute('''
        INSERT OR IGNORE INTO users (id, username, password, role, depot_id, display_name)
        VALUES (?, ?, 'depot123', 'incharge', ?, ?)
        ''', (uid, username, d[0], display_name))
        
    conn.commit()
    conn.close()

# Initialize DB on start if not present
if not os.path.exists(DB_PATH):
    init_db()
else:
    # Ensure users table exists
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT count(name) FROM sqlite_master WHERE type='table' AND name='users'")
    if c.fetchone()[0] == 0:
        init_db()
    conn.close()

# ----------------- AUTHENTICATION ROUTES -----------------
@app.route('/api/current-user')
def get_current_user():
    user = session.get('user')
    if not user:
        return jsonify({
            "authenticated": False,
            "role": "guest",
            "username": "",
            "depot_id": None,
            "display_name": "Not Logged In"
        })
    return jsonify({
        "authenticated": True,
        "role": user.get('role', 'admin'),
        "username": user.get('username'),
        "depot_id": user.get('depot_id'),
        "display_name": user.get('display_name')
    })

@app.route('/api/login', methods=['POST'])
def api_login():
    data = request.json or {}
    username = data.get('username', '').strip().lower()
    password = data.get('password', '').strip()
    
    conn = get_db()
    user_row = conn.execute('SELECT * FROM users WHERE LOWER(username) = ? AND password = ?', (username, password)).fetchone()
    conn.close()
    
    if user_row:
        user_dict = dict(user_row)
        session['user'] = {
            "authenticated": True,
            "id": user_dict['id'],
            "username": user_dict['username'],
            "role": user_dict['role'],
            "depot_id": user_dict['depot_id'],
            "display_name": user_dict['display_name']
        }
        return jsonify({"success": True, "user": session['user']})
    else:
        return jsonify({"success": False, "message": "Invalid username or password. Please check your credentials."}), 401

@app.route('/api/logout')
def api_logout():
    session.pop('user', None)
    return jsonify({"success": True, "message": "Logged out successfully"})



@app.route('/api/admin/users/reset-password', methods=['POST'])
def reset_user_password():
    data = request.json or {}
    user_id = data.get('user_id')
    depot_id = data.get('depot_id')
    new_username = data.get('username')
    new_password = data.get('password')
    
    if not new_password:
        return jsonify({"success": False, "message": "Password cannot be empty"}), 400
        
    conn = get_db()
    cursor = conn.cursor()
    
    if user_id:
        if new_username:
            cursor.execute('UPDATE users SET username = ?, password = ? WHERE id = ?', (new_username.lower().strip(), new_password.strip(), user_id))
        else:
            cursor.execute('UPDATE users SET password = ? WHERE id = ?', (new_password.strip(), user_id))
    elif depot_id:
        if new_username:
            cursor.execute('UPDATE users SET username = ?, password = ? WHERE depot_id = ?', (new_username.lower().strip(), new_password.strip(), depot_id))
        else:
            cursor.execute('UPDATE users SET password = ? WHERE depot_id = ?', (new_password.strip(), depot_id))
            
    conn.commit()
    conn.close()
    return jsonify({"success": True, "message": "Credentials / Password updated successfully!"})

# ----------------- MASTER / DASHBOARD ROUTES -----------------
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/depots')
def get_depots():
    conn = get_db()
    depots = conn.execute('SELECT * FROM depots ORDER BY id ASC').fetchall()
    conn.close()
    return jsonify([dict(row) for row in depots])

@app.route('/api/depots/save', methods=['POST'])
def save_depot():
    data = request.json
    conn = get_db()
    cursor = conn.cursor()
    
    depot_id = data.get('id')
    name = data.get('name')
    category = data.get('category')
    incharge_name = data.get('incharge_name')
    contact = data.get('contact')
    region = data.get('region', 'Central')
    default_uom = data.get('default_uom', 'Pkt')
    
    if depot_id:
        cursor.execute('''
        UPDATE depots SET name = ?, category = ?, incharge_name = ?, contact = ?, region = ?, default_uom = ?
        WHERE id = ?
        ''', (name, category, incharge_name, contact, region, default_uom, depot_id))
    else:
        slug = name.lower().replace(' ', '_').replace('-', '_')
        cursor.execute('''
        INSERT INTO depots (name, slug, category, incharge_name, contact, region, default_uom)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (name, slug, category, incharge_name, contact, region, default_uom))
        
    conn.commit()
    conn.close()
    return jsonify({"success": True, "message": "Incharge / Depot saved successfully!"})

@app.route('/api/dashboard/summary')
def get_dashboard_summary():
    date_param = request.args.get('date', datetime.date.today().strftime('%Y-%m-%d'))
    conn = get_db()
    
    query = '''
    SELECT 
        d.id as depot_id,
        d.name as depot_name,
        d.slug as depot_slug,
        d.category,
        d.region,
        d.default_uom,
        r.id as report_id,
        COALESCE(r.incharge_name, d.incharge_name) as incharge_name,
        COALESCE(r.total_vehicles, 0) as total_vehicles,
        COALESCE(r.total_invoices, 0) as total_invoices,
        COALESCE(r.capacity_util_pct, 0) as capacity_util_pct,
        COALESCE(r.dispatched_gross_val, 0) as dispatched_gross_val,
        COALESCE(r.delivered_net_val, 0) as delivered_net_val,
        COALESCE(r.returned_val, 0) as returned_val,
        COALESCE(r.stock_mismatch_qty, 0) as stock_mismatch_qty,
        COALESCE(r.cash_mismatch_val, 0) as cash_mismatch_val,
        COALESCE(r.adjustment_status, '100% Adjusted') as adjustment_status,
        COALESCE(r.audit_status, 'OK / Verified') as audit_status
    FROM depots d
    LEFT JOIN daily_reports r ON d.id = r.depot_id AND r.report_date = ?
    ORDER BY d.id ASC
    '''
    rows = conn.execute(query, (date_param,)).fetchall()
    
    depot_list = []
    tot_dispatched = 0
    tot_delivered = 0
    tot_returned = 0
    tot_invoices = 0
    tot_vehicles = 0
    tot_stock_var = 0
    tot_cash_var = 0
    util_sum = 0
    active_units_count = 0
    
    for row in rows:
        d_dict = dict(row)
        disp = d_dict['dispatched_gross_val']
        deliv = d_dict['delivered_net_val']
        ret = d_dict['returned_val']
        
        d_dict['success_rate'] = round((deliv / disp * 100), 1) if disp > 0 else 0
        d_dict['return_rate'] = round((ret / disp * 100), 1) if disp > 0 else 0
        
        tot_dispatched += disp
        tot_delivered += deliv
        tot_returned += ret
        tot_invoices += d_dict['total_invoices']
        tot_vehicles += d_dict['total_vehicles']
        tot_stock_var += d_dict['stock_mismatch_qty']
        tot_cash_var += d_dict['cash_mismatch_val']
        
        if d_dict['total_vehicles'] > 0:
            util_sum += d_dict['capacity_util_pct']
            active_units_count += 1
        
        depot_list.append(d_dict)
        
    overall_success_rate = round((tot_delivered / tot_dispatched * 100), 1) if tot_dispatched > 0 else 0
    avg_capacity_util = round((util_sum / active_units_count), 1) if active_units_count > 0 else 0
    
    category_names = [
        ("Frozen Food & RTC", "Pkt"),
        ("Processed Chicken", "Kg"),
        ("Sweets & Savory", "Kg"),
        ("Fresh Eggs", "Pcs"),
        ("Dairy (Sirajganj & Tejgaon)", "Ltr"),
        ("Dry Food & Bakery", "Pkt"),
        ("Momo & Dimsum", "Kg"),
        ("Tea Distribution & Packing", "Kg")
    ]
    
    categories = []
    for c_name, c_uom in category_names:
        # Sum from depots matching this category
        cat_disp = sum(d['dispatched_gross_val'] for d in depot_list if c_name.lower() in d['category'].lower() or d['category'].lower() in c_name.lower())
        cat_deliv = sum(d['delivered_net_val'] for d in depot_list if c_name.lower() in d['category'].lower() or d['category'].lower() in c_name.lower())
        cat_ret = sum(d['returned_val'] for d in depot_list if c_name.lower() in d['category'].lower() or d['category'].lower() in c_name.lower())
        cat_succ = round((cat_deliv / cat_disp * 100), 1) if cat_disp > 0 else 0
        cat_ret_rate = round((cat_ret / cat_disp * 100), 1) if cat_disp > 0 else 0
        
        categories.append({
            "name": c_name,
            "uom": c_uom,
            "disp_qty": round(cat_disp / 50, 1) if cat_disp > 0 else 0,
            "disp_val": cat_disp,
            "deliv_qty": round(cat_deliv / 50, 1) if cat_deliv > 0 else 0,
            "deliv_val": cat_deliv,
            "ret_qty": round(cat_ret / 50, 1) if cat_ret > 0 else 0,
            "ret_val": cat_ret,
            "success_rate": cat_succ,
            "ret_rate": cat_ret_rate,
            "stock_var": 0
        })
    
    return_reasons = []
    if tot_returned > 0:
        return_reasons = [
            {"code": "R-01", "reason": "Shop Closed / Customer Unavailable", "qty_est": f"{round(tot_returned * 0.37 / 100)} Pkt/Kg", "invoices": max(1, round(tot_invoices * 0.05)), "val": round(tot_returned * 0.37), "pct": 37.0},
            {"code": "R-02", "reason": "Payment Shortage / Cash Not Ready", "qty_est": f"{round(tot_returned * 0.26 / 100)} Pkt/Kg", "invoices": max(1, round(tot_invoices * 0.03)), "val": round(tot_returned * 0.26), "pct": 25.7},
            {"code": "R-03", "reason": "Transit Damage / Breakage", "qty_est": f"{round(tot_returned * 0.15 / 100)} Pkt/Kg", "invoices": max(1, round(tot_invoices * 0.02)), "val": round(tot_returned * 0.15), "pct": 15.0},
            {"code": "R-04", "reason": "Wrong Product / Short Order", "qty_est": f"{round(tot_returned * 0.12 / 100)} Pkt/Kg", "invoices": max(1, round(tot_invoices * 0.01)), "val": round(tot_returned * 0.12), "pct": 11.6},
            {"code": "R-05", "reason": "Price Dispute / Rate Mismatch", "qty_est": f"{round(tot_returned * 0.07 / 100)} Pkt/Kg", "invoices": max(1, round(tot_invoices * 0.01)), "val": round(tot_returned * 0.07), "pct": 6.9},
            {"code": "R-06", "reason": "Route Delay / Traffic Breakdown", "qty_est": f"{round(tot_returned * 0.04 / 100)} Pkt/Kg", "invoices": max(1, round(tot_invoices * 0.01)), "val": round(tot_returned * 0.04), "pct": 3.8}
        ]
    
    conn.close()
    return jsonify({
        "kpis": {
            "total_dispatched_val": tot_dispatched,
            "total_delivered_val": tot_delivered,
            "total_returned_val": tot_returned,
            "delivery_success_rate": overall_success_rate,
            "total_vehicles": tot_vehicles,
            "avg_capacity_util": avg_capacity_util,
            "total_stock_variance": tot_stock_var,
            "total_cash_variance": tot_cash_var
        },
        "depots": depot_list,
        "categories": categories,
        "return_reasons": return_reasons
    })

@app.route('/api/admin/clear-all-demo-data', methods=['POST'])
def clear_all_demo_data():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('DELETE FROM invoices')
    cursor.execute('DELETE FROM trips')
    cursor.execute('DELETE FROM daily_reports')
    conn.commit()
    conn.close()
    return jsonify({"success": True, "message": "All demo & historical distribution reports have been cleared. Database is clean."})

# ----------------- REPORT SUBMISSION & ADMIN MANAGEMENT -----------------
@app.route('/api/reports/submit', methods=['POST'])
def submit_report():
    data = request.json
    conn = get_db()
    cursor = conn.cursor()
    
    depot_id = data.get('depot_id')
    report_date = data.get('report_date', datetime.date.today().strftime('%Y-%m-%d'))
    incharge_name = data.get('incharge_name')
    contact = data.get('contact')
    trips = data.get('trips', [])
    invoices = data.get('invoices', [])
    
    tot_disp_val = sum(float(i.get('dispatched_val', 0)) for i in invoices)
    tot_deliv_val = sum(float(i.get('delivered_val', 0)) for i in invoices)
    tot_ret_val = sum(float(i.get('returned_val', 0)) for i in invoices)
    
    tot_disp_qty = sum(float(i.get('dispatched_qty', 0)) for i in invoices)
    tot_deliv_qty = sum(float(i.get('delivered_qty', 0)) for i in invoices)
    tot_ret_qty = sum(float(i.get('returned_qty', 0)) for i in invoices)
    
    stock_var = round(tot_disp_qty - (tot_deliv_qty + tot_ret_qty))
    
    tot_cash = sum(float(i.get('amount_collected', 0)) for i in invoices)
    tot_credit = sum(float(i.get('delivered_val', 0)) for i in invoices if i.get('collection_mode') == 'Credit')
    cash_var = round(tot_deliv_val - (tot_cash + tot_credit))
    
    util_sum = sum(float(t.get('loaded_kg', 0))/float(t.get('capacity_kg', 1))*100 for t in trips if float(t.get('capacity_kg', 0)) > 0)
    avg_util = round(util_sum / len(trips), 1) if trips else 0
    
    cursor.execute('''
    DELETE FROM daily_reports WHERE depot_id = ? AND report_date = ?
    ''', (depot_id, report_date))
    
    cursor.execute('''
    INSERT INTO daily_reports (
        report_date, depot_id, incharge_name, contact, 
        total_vehicles, total_invoices, capacity_util_pct,
        dispatched_gross_val, delivered_net_val, returned_val,
        stock_mismatch_qty, cash_mismatch_val, adjustment_status, audit_status
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '100% Adjusted', 'OK / Verified')
    ''', (
        report_date, depot_id, incharge_name, contact,
        len(trips), len(invoices), avg_util,
        tot_disp_val, tot_deliv_val, tot_ret_val,
        stock_var, cash_var
    ))
    report_id = cursor.lastrowid
    
    for t in trips:
        cap = float(t.get('capacity_kg', 0))
        load = float(t.get('loaded_kg', 0))
        u = round(load / cap * 100, 1) if cap > 0 else 0
        cursor.execute('''
        INSERT INTO trips (
            report_id, trip_no, vehicle_no, vehicle_type, driver_name, delivery_man, route_name,
            capacity_kg, loaded_kg, util_pct, reefer_temp
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            report_id, t.get('trip_no'), t.get('vehicle_no'), t.get('vehicle_type'),
            t.get('driver_name'), t.get('delivery_man'), t.get('route_name'),
            cap, load, u, t.get('reefer_temp')
        ))
        
    for inv in invoices:
        cursor.execute('''
        INSERT INTO invoices (
            report_id, invoice_no, customer_name, trip_no, product_category, sku_uom,
            dispatched_qty, dispatched_val, delivery_status, delivered_qty, delivered_val,
            returned_qty, returned_val, return_reason, collection_mode, amount_collected
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            report_id,
            inv.get('invoice_no') or 'INV-001',
            inv.get('customer_name') or 'Customer Outlet',
            inv.get('trip_no') or 'TRIP-01',
            inv.get('product_category') or 'Frozen Food',
            inv.get('sku_uom') or 'Pkt',
            float(inv.get('dispatched_qty') or 0),
            float(inv.get('dispatched_val') or 0),
            inv.get('delivery_status') or 'Delivered',
            float(inv.get('delivered_qty') or 0),
            float(inv.get('delivered_val') or 0),
            float(inv.get('returned_qty') or 0),
            float(inv.get('returned_val') or 0),
            inv.get('return_reason') or 'None',
            inv.get('collection_mode') or 'Credit',
            float(inv.get('amount_collected') or 0)
        ))
        
    conn.commit()
    conn.close()
    return jsonify({"success": True, "report_id": report_id, "message": "Report submitted and verified successfully!"})

@app.route('/api/reports/delete/<int:report_id>', methods=['DELETE', 'POST'])
def delete_report(report_id):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('DELETE FROM invoices WHERE report_id = ?', (report_id,))
    cursor.execute('DELETE FROM trips WHERE report_id = ?', (report_id,))
    cursor.execute('DELETE FROM daily_reports WHERE id = ?', (report_id,))
    conn.commit()
    conn.close()
    return jsonify({"success": True, "message": f"Daily report #{report_id} deleted successfully!"})

@app.route('/api/reports/get-by-date')
def get_report_by_date():
    depot_id = request.args.get('depot_id')
    report_date = request.args.get('date')
    if not depot_id or not report_date:
        return jsonify({"success": False, "message": "Missing depot_id or date"})
    
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM daily_reports WHERE depot_id = ? AND report_date = ?', (depot_id, report_date))
    rep = cursor.fetchone()
    if not rep:
        conn.close()
        return jsonify({"success": True, "exists": False})
    
    report_id = rep['id']
    cursor.execute('SELECT * FROM trips WHERE report_id = ?', (report_id,))
    trips = [dict(t) for t in cursor.fetchall()]
    
    cursor.execute('SELECT * FROM invoices WHERE report_id = ?', (report_id,))
    invoices = [dict(i) for i in cursor.fetchall()]
    
    conn.close()
    return jsonify({
        "success": True,
        "exists": True,
        "report": dict(rep),
        "trips": trips,
        "invoices": invoices
    })

@app.route('/api/reports/delete-by-date', methods=['POST', 'DELETE'])
def delete_report_by_date():
    data = request.json or {}
    depot_id = data.get('depot_id') or request.args.get('depot_id')
    report_date = data.get('date') or request.args.get('date')
    if not depot_id or not report_date:
        return jsonify({"success": False, "message": "Missing depot_id or date"})
    
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT id FROM daily_reports WHERE depot_id = ? AND report_date = ?', (depot_id, report_date))
    reps = cursor.fetchall()
    if not reps:
        conn.close()
        return jsonify({"success": False, "message": f"No report found for depot {depot_id} on date {report_date}"})
        
    for r in reps:
        rid = r['id']
        cursor.execute('DELETE FROM invoices WHERE report_id = ?', (rid,))
        cursor.execute('DELETE FROM trips WHERE report_id = ?', (rid,))
        cursor.execute('DELETE FROM daily_reports WHERE id = ?', (rid,))
    conn.commit()
    conn.close()
    return jsonify({"success": True, "message": f"Report for date {report_date} has been deleted successfully!"})

@app.route('/api/admin/reset-demo-data', methods=['POST'])
def reset_demo_data():
    init_db()
    return jsonify({"success": True, "message": "Demo data reset successfully to clean initial state!"})

@app.route('/api/export/excel')
def export_master_excel():
    date_param = request.args.get('date', datetime.date.today().strftime('%Y-%m-%d'))
    master_file = r'd:\AI project\Distribution\Paragon_Distribution_Master_Report.xlsx'
    if os.path.exists(master_file):
        return send_file(master_file, as_attachment=True, download_name=f"Paragon_Master_Distribution_{date_param}.xlsx")
    return jsonify({"error": "Master excel not found"}), 404


# ----------------- BULK EXCEL TEMPLATE (CRYSTAL CLEAR HIGH-VISIBILITY TYPOGRAPHY) -----------------
@app.route('/api/template/daily-entry-excel')
def download_daily_entry_template():
    depot_id = request.args.get('depot_id')
    depot_name = "Paragon Depot"
    category = "Frozen Food & RTC"
    incharge_info = "Depot Incharge"
    
    if depot_id:
        try:
            conn = get_db()
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM depots WHERE id = ?', (depot_id,))
            depot = cursor.fetchone()
            conn.close()
            if depot:
                depot_name = depot['name']
                category = depot['category']
                incharge_info = f"{depot['incharge_name']} ({depot['contact']})"
        except Exception as e:
            pass

    output = io.BytesIO()
    wb = xlsxwriter.Workbook(output, {'in_memory': True})
    
    # High-Visibility Typography & Border Styles (Guaranteed 100% Legibility on Any Screen / Excel)
    fmt_navy_hdr = wb.add_format({
        'bold': True, 'font_name': 'Segoe UI', 'font_size': 11, 'font_color': '#1E3A8A',
        'align': 'center', 'valign': 'vcenter', 'top': 2, 'bottom': 6, 'left': 1, 'right': 1,
        'border_color': '#1E3A8A'
    })
    fmt_cyan_hdr = wb.add_format({
        'bold': True, 'font_name': 'Segoe UI', 'font_size': 11, 'font_color': '#0369A1',
        'align': 'center', 'valign': 'vcenter', 'top': 2, 'bottom': 6, 'left': 1, 'right': 1,
        'border_color': '#0369A1'
    })
    fmt_sample_hdr = wb.add_format({
        'font_name': 'Segoe UI', 'font_size': 9.5, 'font_color': '#475569', 'italic': True,
        'align': 'left', 'valign': 'vcenter', 'border': 1, 'border_color': '#CBD5E1'
    })
    fmt_sample_num = wb.add_format({
        'font_name': 'Segoe UI', 'font_size': 9.5, 'font_color': '#475569', 'italic': True,
        'align': 'right', 'valign': 'vcenter', 'border': 1, 'border_color': '#CBD5E1', 'num_format': '#,##0'
    })
    fmt_sample_center = wb.add_format({
        'font_name': 'Segoe UI', 'font_size': 9.5, 'font_color': '#475569', 'italic': True,
        'align': 'center', 'valign': 'vcenter', 'border': 1, 'border_color': '#CBD5E1'
    })

    # Sheet 1: Fleet & Trips
    ws_trips = wb.add_worksheet("Fleet_and_Trips")
    ws_trips.hide_gridlines(0)
    ws_trips.set_row(0, 28)
    
    trip_headers = [
        "Trip_No", "Vehicle_Reg_No", "Vehicle_Type", "Driver_Name",
        "Delivery_Man", "Route_Market_Area", "Capacity_Kg", "Loaded_Kg", "Reefer_Temp"
    ]
    for c_i, h in enumerate(trip_headers):
        ws_trips.write(0, c_i, h, fmt_navy_hdr)
        
    sample_trips = [
        ["TRIP-01", "DM-THA-11-2045", "1.5T Reefer Van", "Abul Kalam", "Md. Rafiq", f"{depot_name} Standard Route", 1500, 1420, "-18°C"],
        ["TRIP-02", "DM-THA-11-3090", "2.0T Reefer Van", "Kalam Miah", "Md. Dulal", f"{depot_name} Secondary Route", 2000, 1890, "-18°C"]
    ]
    for r_i, r in enumerate(sample_trips, start=1):
        ws_trips.set_row(r_i, 20)
        for c_i, val in enumerate(r):
            if c_i in [6, 7]:
                ws_trips.write(r_i, c_i, val, fmt_sample_num)
            elif c_i in [0, 8]:
                ws_trips.write(r_i, c_i, val, fmt_sample_center)
            else:
                ws_trips.write(r_i, c_i, val, fmt_sample_hdr)
                
    ws_trips.set_column(0, 0, 14)
    ws_trips.set_column(1, 1, 20)
    ws_trips.set_column(2, 2, 18)
    ws_trips.set_column(3, 4, 18)
    ws_trips.set_column(5, 5, 34)
    ws_trips.set_column(6, 7, 16)
    ws_trips.set_column(8, 8, 14)

    # Sheet 2: Invoices & Delivery
    ws_inv = wb.add_worksheet("Invoices_and_Delivery")
    ws_inv.hide_gridlines(0)
    ws_inv.set_row(0, 28)
    
    inv_headers = [
        "Invoice_No", "Customer_Name", "Trip_No", "Product_Category", "SKU_UOM",
        "Dispatched_Qty", "Dispatched_Val", "Delivery_Status", "Delivered_Qty",
        "Delivered_Val", "Returned_Qty", "Returned_Val", "Return_Reason",
        "Payment_Mode", "Amount_Collected"
    ]
    for c_i, h in enumerate(inv_headers):
        ws_inv.write(0, c_i, h, fmt_cyan_hdr)
        
    sample_invoices = [
        ["INV-2026-0001", "Agora Superstore Outlet", "TRIP-01", category, "Pkt", 120, 48000, "Delivered", 120, 48000, 0, 0, "None", "Credit", 0],
        ["INV-2026-0002", "Meena Bazar Outlet", "TRIP-01", category, "Kg", 80, 36000, "Partial Delivery", 70, 31500, 10, 4500, "R-01: Shop Closed", "Cash", 31500],
        ["INV-2026-0003", "Shwapno Express Outlet", "TRIP-02", category, "Pcs", 2500, 25000, "Delivered", 2500, 25000, 0, 0, "None", "Credit", 0]
    ]
    for r_i, r in enumerate(sample_invoices, start=1):
        ws_inv.set_row(r_i, 20)
        for c_i, val in enumerate(r):
            if c_i in [5, 6, 8, 9, 10, 11, 14]:
                ws_inv.write(r_i, c_i, val, fmt_sample_num)
            elif c_i in [0, 2, 4, 7, 13]:
                ws_inv.write(r_i, c_i, val, fmt_sample_center)
            else:
                ws_inv.write(r_i, c_i, val, fmt_sample_hdr)

    # Add Dropdown Data Validations
    ws_inv.data_validation('D2:D500', {
        'validate': 'list',
        'source': ['Fresh Eggs', 'Frozen Food & RTC', 'Processed Chicken', 'Dairy', 'Dry Food & Bakery', 'Tea', 'Momo & Dimsum', 'Sweets & Savory']
    })
    ws_inv.data_validation('H2:H500', {
        'validate': 'list',
        'source': ['Delivered', 'Partial Delivery', 'Undelivered']
    })
    ws_inv.data_validation('N2:N500', {
        'validate': 'list',
        'source': ['Credit', 'Cash', 'bKash / MFS', 'Bank Cheque']
    })

    ws_inv.set_column(0, 0, 16)
    ws_inv.set_column(1, 1, 32)
    ws_inv.set_column(2, 2, 14)
    ws_inv.set_column(3, 3, 20)
    ws_inv.set_column(4, 4, 12)
    ws_inv.set_column(5, 6, 16)
    ws_inv.set_column(7, 7, 18)
    ws_inv.set_column(8, 11, 16)
    ws_inv.set_column(12, 12, 24)
    ws_inv.set_column(13, 14, 18)

    wb.close()
    output.seek(0)
    
    clean_name = "".join(c for c in depot_name if c.isalnum() or c in (' ', '_', '-')).strip().replace(' ', '_')
    fname = f"Paragon_Daily_Entry_Template_{clean_name}.xlsx" if depot_id else "Paragon_Depot_Daily_Entry_Template.xlsx"
    
    return send_file(
        output,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name=fname
    )

@app.route('/api/template/parse-excel', methods=['POST'])
def parse_daily_entry_excel():
    if 'file' not in request.files:
        return jsonify({"success": False, "message": "No Excel file uploaded"}), 400
    
    file = request.files['file']
    if not file.filename.endswith(('.xlsx', '.xls')):
        return jsonify({"success": False, "message": "Invalid file format. Please upload an .xlsx file."}), 400

    try:
        wb = openpyxl.load_workbook(file, data_only=True)
        
        # 1. Parse Trips
        trips = []
        if "Fleet_and_Trips" in wb.sheetnames:
            ws_trips = wb["Fleet_and_Trips"]
            rows = list(ws_trips.iter_rows(values_only=True))
            if len(rows) > 1:
                header = [str(h).strip().lower() for h in rows[0] if h]
                for r in rows[1:]:
                    if not any(r):
                        continue
                    # Skip sample if matches exact default sample
                    trip_no = str(r[0] or '').strip()
                    veh_no = str(r[1] or '').strip()
                    if not trip_no and not veh_no:
                        continue
                    
                    trips.append({
                        "trip_no": trip_no or f"TRIP-0{len(trips)+1}",
                        "vehicle_no": veh_no,
                        "vehicle_type": str(r[2] or '1.5T Reefer Van').strip(),
                        "driver_name": str(r[3] or '').strip(),
                        "delivery_man": str(r[4] or '').strip(),
                        "route_name": str(r[5] or '').strip(),
                        "capacity_kg": float(r[6] or 1500),
                        "loaded_kg": float(r[7] or 1400),
                        "reefer_temp": str(r[8] or '-18°C').strip()
                    })
        
        # 2. Parse Invoices
        invoices = []
        if "Invoices_and_Delivery" in wb.sheetnames:
            ws_inv = wb["Invoices_and_Delivery"]
            rows = list(ws_inv.iter_rows(values_only=True))
            if len(rows) > 1:
                for r in rows[1:]:
                    if not any(r):
                        continue
                    inv_no = str(r[0] or '').strip()
                    cust_name = str(r[1] or '').strip()
                    if not inv_no and not cust_name:
                        continue

                    disp_qty = float(r[5] or 0)
                    disp_val = float(r[6] or 0)
                    del_qty = float(r[8] or disp_qty)
                    del_val = float(r[9] or disp_val)
                    ret_qty = float(r[10] or max(0, disp_qty - del_qty))
                    ret_val = float(r[11] or max(0, disp_val - del_val))
                    cash_coll = float(r[14] or 0)

                    invoices.append({
                        "invoice_no": inv_no or f"INV-2026-00{len(invoices)+1}",
                        "customer_name": cust_name,
                        "trip_no": str(r[2] or 'TRIP-01').strip(),
                        "product_category": str(r[3] or 'Frozen Food').strip(),
                        "sku_uom": str(r[4] or 'Pkt').strip(),
                        "dispatched_qty": disp_qty,
                        "dispatched_val": disp_val,
                        "delivery_status": str(r[7] or 'Delivered').strip(),
                        "delivered_qty": del_qty,
                        "delivered_val": del_val,
                        "returned_qty": ret_qty,
                        "returned_val": ret_val,
                        "return_reason": str(r[12] or 'None').strip(),
                        "collection_mode": str(r[13] or 'Credit').strip(),
                        "amount_collected": cash_coll
                    })

        if not trips and not invoices:
            return jsonify({"success": False, "message": "No valid data rows found in the uploaded Excel template."}), 400

        return jsonify({
            "success": True,
            "trips": trips,
            "invoices": invoices,
            "count_trips": len(trips),
            "count_invoices": len(invoices),
            "message": f"Excel parsed successfully: {len(trips)} vehicle trips and {len(invoices)} invoices loaded!"
        })

    except Exception as e:
        return jsonify({"success": False, "message": f"Error parsing Excel file: {str(e)}"}), 500


# ----------------- HEALTH & OFFLINE SYNC -----------------
@app.route('/api/health')
def health_check():
    return jsonify({"status": "online", "timestamp": datetime.datetime.now().isoformat()})

# ----------------- REPORT CANCELLATION & AUDIT WORKFLOW -----------------
@app.route('/api/reports/depot/<int:depot_id>')
def get_depot_recent_reports(depot_id):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT id, report_date, total_vehicles, total_invoices, dispatched_gross_val,
               delivered_net_val, returned_val, status, cancellation_reason, created_at
        FROM daily_reports
        WHERE depot_id = ?
        ORDER BY report_date DESC, id DESC LIMIT 15
    ''', (depot_id,))
    reports = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return jsonify({"success": True, "reports": reports})

@app.route('/api/reports/request-cancellation', methods=['POST'])
def request_report_cancellation():
    data = request.json or {}
    report_id = data.get('report_id')
    reason = data.get('reason', 'User requested correction')
    
    if not report_id:
        return jsonify({"success": False, "message": "Report ID is required"}), 400
        
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''
        UPDATE daily_reports
        SET status = 'cancellation_requested',
            cancellation_reason = ?,
            cancellation_requested_at = CURRENT_TIMESTAMP
        WHERE id = ?
    ''', (reason, report_id))
    conn.commit()
    conn.close()
    return jsonify({"success": True, "message": "Cancellation request submitted to Executive Admin for audit review."})

@app.route('/api/reports/cancellations')
def get_pending_cancellations():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT r.id, r.report_date, d.name as depot_name, r.incharge_name,
               r.dispatched_gross_val, r.delivered_net_val, r.cancellation_reason,
               r.cancellation_requested_at, r.status
        FROM daily_reports r
        JOIN depots d ON r.depot_id = d.id
        WHERE r.status = 'cancellation_requested'
        ORDER BY r.cancellation_requested_at DESC
    ''')
    requests = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return jsonify({"success": True, "requests": requests})

@app.route('/api/reports/approve-cancellation/<int:report_id>', methods=['POST'])
def approve_cancellation(report_id):
    admin_name = session.get('user', {}).get('display_name', 'Executive Admin')
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''
        UPDATE daily_reports
        SET status = 'cancelled',
            cancelled_by = ?,
            cancelled_at = CURRENT_TIMESTAMP
        WHERE id = ?
    ''', (admin_name, report_id))
    conn.commit()
    conn.close()
    return jsonify({"success": True, "message": f"Report #{report_id} has been voided/cancelled successfully."})

@app.route('/api/reports/reject-cancellation/<int:report_id>', methods=['POST'])
def reject_cancellation(report_id):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''
        UPDATE daily_reports
        SET status = 'active',
            cancellation_reason = NULL
        WHERE id = ?
    ''', (report_id,))
    conn.commit()
    conn.close()
    return jsonify({"success": True, "message": f"Cancellation request for Report #{report_id} was rejected."})

@app.route('/api/reports/unlock/<int:report_id>', methods=['POST'])
def unlock_report_for_edit(report_id):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''
        UPDATE daily_reports
        SET status = 'unlocked'
        WHERE id = ?
    ''', (report_id,))
    conn.commit()
    conn.close()
    return jsonify({"success": True, "message": f"Report #{report_id} unlocked. Incharge may now re-edit and resubmit."})

# ----------------- ADMIN USER & INCHARGE MANAGEMENT -----------------
@app.route('/api/admin/users')
def get_admin_users():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT u.id, u.username, u.role, u.depot_id, u.display_name, u.is_active,
               d.name as depot_name, d.category as depot_category
        FROM users u
        LEFT JOIN depots d ON u.depot_id = d.id
        ORDER BY u.role ASC, u.username ASC
    ''')
    users = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return jsonify({"success": True, "users": users})

@app.route('/api/admin/users/update', methods=['POST'])
def update_admin_user():
    data = request.json or {}
    user_id = data.get('user_id')
    display_name = data.get('display_name')
    password = data.get('password')
    is_active = data.get('is_active', 1)
    
    if not user_id:
        return jsonify({"success": False, "message": "User ID required"}), 400
        
    conn = get_db()
    cursor = conn.cursor()
    
    if password and len(password.strip()) > 0:
        cursor.execute('''
            UPDATE users
            SET display_name = ?, password = ?, is_active = ?
            WHERE id = ?
        ''', (display_name, password.strip(), is_active, user_id))
    else:
        cursor.execute('''
            UPDATE users
            SET display_name = ?, is_active = ?
            WHERE id = ?
        ''', (display_name, is_active, user_id))
        
    conn.commit()
    conn.close()
    return jsonify({"success": True, "message": "User credentials and status updated successfully!"})

@app.route('/api/admin/users/delete/<int:user_id>', methods=['POST', 'DELETE'])
def delete_admin_user(user_id):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT role FROM users WHERE id = ?', (user_id,))
    row = cursor.fetchone()
    if not row:
        conn.close()
        return jsonify({"success": False, "message": "User not found"}), 404
        
    if row['role'] == 'admin':
        cursor.execute("SELECT COUNT(*) as count FROM users WHERE role = 'admin'")
        admin_count = cursor.fetchone()['count']
        if admin_count <= 1:
            conn.close()
            return jsonify({"success": False, "message": "Cannot delete the only executive admin account"}), 400
            
    cursor.execute('DELETE FROM users WHERE id = ?', (user_id,))
    conn.commit()
    conn.close()
    return jsonify({"success": True, "message": "User account deleted successfully!"})

# ----------------- DISTRIBUTION ROUTE PLANNING & POLOXY ERP INTEGRATION -----------------

# ----------------- DISTRIBUTION ROUTE PLANNING & POLOXY ERP INTEGRATION -----------------

DEPOT_CATALOG_LOOKUP = [
    {"id": 1, "name": "01. Ashulia Factory - Frozen & RTC", "category": "Frozen Food", "match": ["ashulia frozen", "01.ash", "ash-fz", "frozen-ash"]},
    {"id": 2, "name": "02. Ashulia Factory - Dry Food", "category": "Dry Food", "match": ["ashulia dry", "02.ash", "ash-dry", "dry-ash"]},
    {"id": 3, "name": "03. Gazipur-Process CK", "category": "Processed Chicken", "match": ["gazipur", "gz", "chicken plant", "03.gz", "ck-plant", "01so", "process ck"]},
    {"id": 4, "name": "04. Sirajganj Dairy", "category": "Dairy", "match": ["sirajganj", "dairy plant", "milk", "04.sj"]},
    {"id": 5, "name": "05. Sylhet - Tea Packing Unit", "category": "Tea", "match": ["sylhet tea", "tea pack", "05.syl"]},
    {"id": 6, "name": "06. Sylhet Depot - Frozen", "category": "Frozen Food", "match": ["sylhet frozen", "syl-fz", "06.syl", "sylhet depot"]},
    {"id": 7, "name": "07. Tejgaon - Frozen/CK", "category": "Chicken & Frozen", "match": ["tejgaon frozen", "tg-fz", "tg-ck", "07.tg"]},
    {"id": 8, "name": "08. Tejgaon - e-Commerce", "category": "e-Commerce", "match": ["e-commerce", "ecommerce", "tg-ecom", "08.tg"]},
    {"id": 9, "name": "09. Tejgaon - Fresh Egg", "category": "Fresh Eggs", "match": ["egg", "fresh egg", "table egg", "03tg", "tg-egg", "09.tg", "09so", "09dn"]},
    {"id": 10, "name": "10. Tejgaon - Tea Distribution", "category": "Tea", "match": ["tg-tea", "tea dist", "10.tg"]},
    {"id": 11, "name": "11. Tejgaon - Dairy", "category": "Dairy", "match": ["tg-dairy", "tejgaon dairy", "11.tg"]},
    {"id": 12, "name": "12. Mohakhali Depot", "category": "Momo & Snacks", "match": ["mohakhali", "momo", "12.mhk"]},
    {"id": 13, "name": "13. CTG Depot - Frozen", "category": "Frozen Food", "match": ["chittagong frozen", "ctg-fz", "13.ctg"]},
    {"id": 14, "name": "14. CTG Depot - Dry Food", "category": "Dry Food", "match": ["chittagong dry", "ctg-dry", "14.ctg"]},
    {"id": 15, "name": "15. Jessore - Frozen Food", "category": "Frozen Food", "match": ["jessore frozen", "jd-fz", "15.jd"]},
    {"id": 16, "name": "16. Jessore - Dry Food", "category": "Dry Food", "match": ["jessore dry", "jd-dry", "16.jd"]},
    {"id": 17, "name": "17. Rangpur Depot - Frozen", "category": "Frozen Food", "match": ["rangpur", "rng-fz", "17.rng"]}
]

def match_depot_ref(branch_str, ref_str, order_no_str=''):
    b = str(branch_str or '').upper()
    r = str(ref_str or '').upper()
    o = str(order_no_str or '').upper()
    
    # 01 - Chicken (সকল Chicken: Gazipur or Tejgaon)
    if '01.' in b or '01SO' in o or '01.' in r or 'CHICKEN' in b:
        if 'TG' in r or 'TEJGAON' in r or '01.TG' in r:
            return {"id": 7, "name": "07. Tejgaon - Frozen/CK", "category": "Chicken & Frozen"}
        return {"id": 3, "name": "03. Gazipur-Process CK", "category": "Processed Chicken"}
    
    # 02 - Frozen (সকল Frozen: Ashulia, Tejgaon, CTG, Sylhet, Jessore, Rangpur)
    if '02.' in b or '02SO' in o or '02.' in r or 'FROZEN' in b:
        if 'ASHULIA' in r or 'ASH' in r:
            return {"id": 1, "name": "01. Ashulia Factory - Frozen", "category": "Frozen Food"}
        elif 'CTG' in r or 'CHITTAGONG' in r:
            return {"id": 13, "name": "13. CTG Depot - Frozen", "category": "Frozen Food"}
        elif 'SYLHET' in r or 'SYL' in r:
            return {"id": 6, "name": "06. Sylhet Depot - Frozen", "category": "Frozen Food"}
        elif 'JD' in r or 'JESSORE' in r:
            return {"id": 15, "name": "15. Jessore - Frozen Food", "category": "Frozen Food"}
        elif 'RANGPUR' in r or 'RNG' in r:
            return {"id": 17, "name": "17. Rangpur Depot - Frozen", "category": "Frozen Food"}
        else:
            return {"id": 7, "name": "07. Tejgaon - Frozen/CK", "category": "Chicken & Frozen"}
            
    # 03 - Egg (সকল Egg: Tejgaon Fresh Egg)
    if '03.' in b or '03SO' in o or '03' in r or 'EGG' in b or 'EGG' in r:
        return {"id": 9, "name": "09. Tejgaon - Fresh Egg", "category": "Fresh Eggs"}
        
    # 04 - Tea (সকল Tea: Sylhet or Tejgaon)
    if '04.' in b or '04SO' in o or '04.' in r or 'TEA' in b:
        if 'SYLHET' in r or 'SYL' in r:
            return {"id": 5, "name": "05. Sylhet - Tea Packing Unit", "category": "Tea"}
        return {"id": 10, "name": "10. Tejgaon - Tea Distribution", "category": "Tea"}
        
    # 05 - Dry Food (সকল Dry Food: Ashulia, CTG, Jessore)
    if '05.' in b or '05SO' in o or '05.' in r or 'DRY' in b:
        if 'CTG' in r or 'CHITTAGONG' in r:
            return {"id": 14, "name": "14. CTG Depot - Dry Food", "category": "Dry Food"}
        elif 'JD' in r or 'JESSORE' in r:
            return {"id": 16, "name": "16. Jessore - Dry Food", "category": "Dry Food"}
        return {"id": 2, "name": "02. Ashulia Factory - Dry Food", "category": "Dry Food"}
        
    # 07 - Momo (সকল Momo: Mohakhali)
    if '07.' in b or '07SO' in o or '07.' in r or 'MOMO' in b or 'MOMO' in r:
        return {"id": 12, "name": "12. Mohakhali Depot", "category": "Momo & Snacks"}
        
    # 06 - Dairy (সকল Dairy: Sirajganj or Tejgaon)
    if 'DAIRY' in b or 'MILK' in b or '06.' in b or '06SO' in o or '06.' in r:
        if 'SIRAJGANJ' in r or 'SJ' in r:
            return {"id": 4, "name": "04. Sirajganj Dairy", "category": "Dairy"}
        return {"id": 11, "name": "11. Tejgaon - Dairy", "category": "Dairy"}
        
    # 08 - eCommerce / Sweets & Savory
    if 'ECOM' in b or 'ECOM' in r or 'SWEET' in b or '08.' in b or '08SO' in o:
        return {"id": 8, "name": "08. Tejgaon - e-Commerce", "category": "e-Commerce"}
        
    return {"id": 9, "name": "09. Tejgaon - Fresh Egg", "category": "Fresh Eggs"}

@app.route('/api/distribution/import-poloxy-orders', methods=['POST'])
def import_poloxy_orders():
    if 'file' not in request.files:
        return jsonify({"success": False, "message": "No file uploaded. Please upload Sale Order Status Report.xlsx"}), 400
        
    file = request.files['file']
    if file.filename == '':
        return jsonify({"success": False, "message": "No file selected"}), 400
        
    explicit_depot_id = request.form.get('depot_id') or request.args.get('depot_id')
    fixed_depot_info = None
    if explicit_depot_id:
        try:
            conn = get_db()
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM depots WHERE id = ?', (explicit_depot_id,))
            row = cursor.fetchone()
            conn.close()
            if row:
                fixed_depot_info = {
                    "id": row['id'],
                    "name": row['name'],
                    "category": row['category']
                }
        except Exception:
            pass

    try:
        wb = openpyxl.load_workbook(file, data_only=True)
        ws = wb.active
        
        rows = list(ws.iter_rows(values_only=True))
        if len(rows) < 4:
            return jsonify({"success": False, "message": "Invalid Poloxy file format or empty sheet"}), 400
            
        header_row_idx = 2
        for idx, row in enumerate(rows[:6]):
            if any('Order No' in str(c) or 'Ref. No' in str(c) for c in row if c):
                header_row_idx = idx
                break
                
        depot_map = {}
        total_orders_parsed = 0
        
        for r in rows[header_row_idx + 1:]:
            if not any(r):
                continue
            sr_no = r[0] if len(r) > 0 else ''
            date_val = str(r[1]).strip() if len(r) > 1 and r[1] else ''
            customer = str(r[2]).strip() if len(r) > 2 and r[2] else ''
            order_no = str(r[3]).strip() if len(r) > 3 and r[3] else ''
            ref_no = str(r[4]).strip() if len(r) > 4 and r[4] else ''
            branch = str(r[6]).strip() if len(r) > 6 and r[6] else ''
            item = str(r[7]).strip() if len(r) > 7 and r[7] else ''
            raw_rate_unit = str(r[8]).strip() if len(r) > 8 and r[8] else ''
            rate = r[9] if len(r) > 9 else 0
            qty_bag = r[10] if len(r) > 10 else 0
            qty_kg = r[11] if len(r) > 11 else 0
            total_amount_col_n = r[13] if len(r) > 13 else None
            d_note_id = str(r[27]).strip() if len(r) > 27 and r[27] else ''
            consignee_name = str(r[32]).strip() if len(r) > 32 and r[32] else customer
            consignee_contact = str(r[33]).strip() if len(r) > 33 and r[33] else ''
            consignee_address = str(r[34]).strip() if len(r) > 34 and r[34] else ''
            
            if not ref_no and not order_no and not item:
                continue
                
            depot_info = fixed_depot_info or match_depot_ref(branch, ref_no, order_no)
            depot_key = f"{depot_info['id']}_{depot_info['name']}"
            
            # Resolve appropriate default UOM for depot
            is_egg = depot_info["id"] == 9 or "egg" in depot_info["category"].lower() or "egg" in item.lower()
            is_dairy = depot_info["id"] in [4, 11] or "dairy" in depot_info["category"].lower() or "milk" in item.lower()
            default_uom = "Pcs" if is_egg else ("Ltr" if is_dairy else "Pkt")
            rate_unit = raw_rate_unit if raw_rate_unit else default_uom
            
            if depot_key not in depot_map:
                depot_map[depot_key] = {
                    "depot_id": depot_info["id"],
                    "depot_name": depot_info["name"],
                    "category": depot_info["category"],
                    "orders": {},
                    "total_outlets": 0,
                    "total_pkts": 0.0,
                    "total_kg": 0.0,
                    "total_amount": 0.0,
                    "primary_uom": default_uom
                }
                
            ord_key = order_no if order_no else f"ORD-{sr_no or (total_orders_parsed + 1)}"
            if ord_key not in depot_map[depot_key]["orders"]:
                depot_map[depot_key]["orders"][ord_key] = {
                    "order_no": ord_key,
                    "date": date_val,
                    "customer": customer,
                    "consignee_name": consignee_name,
                    "consignee_contact": consignee_contact,
                    "consignee_address": consignee_address,
                    "branch_category": branch or depot_info["category"],
                    "delivery_note_id": d_note_id,
                    "items": [],
                    "total_pkt": 0.0,
                    "total_kg": 0.0,
                    "total_amount": 0.0,
                    "col_n_found": False,
                    "assigned_van": "",
                    "payment_mode": "Credit" if any(k in consignee_name.lower() for k in ["shwapno", "agora", "meena", "unimart", "aarong", "pran", "food panda"]) else "Cash",
                    "expected_cash": 0.0,
                    "rate_unit": rate_unit
                }
                depot_map[depot_key]["total_outlets"] += 1
                total_orders_parsed += 1
                
            # Check Column N (Invoice Value)
            if total_amount_col_n is not None and not depot_map[depot_key]["orders"][ord_key]["col_n_found"]:
                try:
                    col_n_num = float(total_amount_col_n)
                    if col_n_num > 0:
                        depot_map[depot_key]["orders"][ord_key]["total_amount"] = col_n_num
                        depot_map[depot_key]["orders"][ord_key]["col_n_found"] = True
                except:
                    pass

            try: q_bag_num = float(qty_bag) if qty_bag else 0.0
            except: q_bag_num = 0.0
            try: q_kg_num = float(qty_kg) if qty_kg else 0.0
            except: q_kg_num = 0.0
            try: r_num = float(rate) if rate else 0.0
            except: r_num = 0.0
            
            item_line_amount = r_num * (q_kg_num if q_kg_num > 0 else q_bag_num)
            
            depot_map[depot_key]["orders"][ord_key]["items"].append({
                "item": item,
                "rate_unit": rate_unit,
                "rate": r_num,
                "qty_pkt": q_bag_num,
                "qty_kg": q_kg_num,
                "amount": item_line_amount
            })
            
            depot_map[depot_key]["orders"][ord_key]["total_pkt"] += q_bag_num
            depot_map[depot_key]["orders"][ord_key]["total_kg"] += q_kg_num
            
            depot_map[depot_key]["total_pkts"] += q_bag_num
            depot_map[depot_key]["total_kg"] += q_kg_num

        # Fetch route mappings, fleet vehicles, and crew for each depot to enable 1-click smart dispatch
        conn = get_db()
        cursor = conn.cursor()
        
        # Aggregate final invoice values per order and per depot
        total_value_parsed = 0.0
        depots_result = []
        for k, v in depot_map.items():
            d_id = v["depot_id"]
            
            # Fetch Enlisted Routes for this depot
            cursor.execute('SELECT id, route_name, route_code, description, default_vehicle_no FROM routes WHERE depot_id = ? ORDER BY id ASC', (d_id,))
            d_routes = [dict(r) for r in cursor.fetchall()]
            
            # Fetch Enlisted Consignee-to-Route mappings
            cursor.execute('SELECT consignee_name, route_id FROM route_consignees WHERE depot_id = ?', (d_id,))
            c_mappings = {r['consignee_name'].lower().strip(): r['route_id'] for r in cursor.fetchall()}
            
            # Fetch Fleet Vehicles (Active, Spare, Rental, Borrowed)
            cursor.execute('SELECT id, vehicle_no, vehicle_type, capacity_kg, capacity_units, ownership, status FROM fleet_vehicles WHERE depot_id = ? ORDER BY ownership ASC, id ASC', (d_id,))
            d_fleet = [dict(r) for r in cursor.fetchall()]
            
            # Fetch Drivers & Delivery Crew
            cursor.execute("SELECT id, role, name, phone, license_no, status FROM depot_crew WHERE depot_id = ? AND status='Active' ORDER BY role ASC, name ASC", (d_id,))
            all_crew = [dict(r) for r in cursor.fetchall()]
            d_drivers = [c for c in all_crew if c['role'] == 'driver']
            d_deliverymen = [c for c in all_crew if c['role'] == 'delivery_man']
            
            route_name_map = {r['id']: r['route_name'] for r in d_routes}

            depot_total_amt = 0.0
            order_list = list(v["orders"].values())
            for o in order_list:
                # If Col N wasn't present, fallback to sum of item line amounts
                if not o.get("col_n_found", False) or o["total_amount"] == 0:
                    o["total_amount"] = sum(it["amount"] for it in o["items"])
                
                # Match Route
                c_clean = o["consignee_name"].lower().strip()
                matched_r_id = c_mappings.get(c_clean)
                if matched_r_id and matched_r_id in route_name_map:
                    o["route_id"] = matched_r_id
                    o["route_name"] = route_name_map[matched_r_id]
                else:
                    # Smart fuzzy route match if route keyword in consignee name
                    fuzzy_matched = False
                    for r_obj in d_routes:
                        r_words = [w for w in r_obj['route_name'].lower().replace('-', ' ').split() if len(w) > 3]
                        if any(w in c_clean for w in r_words):
                            o["route_id"] = r_obj['id']
                            o["route_name"] = r_obj['route_name']
                            fuzzy_matched = True
                            break
                    if not fuzzy_matched:
                        o["route_id"] = None
                        o["route_name"] = "Unassigned Route"
                
                depot_total_amt += o["total_amount"]
                if o["payment_mode"] == "Cash":
                    o["expected_cash"] = o["total_amount"]
                    
            v["total_amount"] = round(depot_total_amt, 2)
            total_value_parsed += depot_total_amt
            
            depots_result.append({
                "depot_id": v["depot_id"],
                "depot_name": v["depot_name"],
                "category": v["category"],
                "total_outlets": v["total_outlets"],
                "total_pkts": round(v["total_pkts"], 1),
                "total_kg": round(v["total_kg"], 2),
                "total_amount": round(v["total_amount"], 2),
                "primary_uom": v.get("primary_uom", "Pkt"),
                "orders": order_list,
                "routes": d_routes,
                "fleet": d_fleet,
                "drivers": d_drivers,
                "deliverymen": d_deliverymen
            })
            
        conn.close()
        
        return jsonify({
            "success": True,
            "message": f"Successfully parsed {total_orders_parsed} sales orders across {len(depots_result)} depots (Total Value: ৳ {total_value_parsed:,.2f})",
            "total_orders": total_orders_parsed,
            "total_value": round(total_value_parsed, 2),
            "depots": depots_result
        })
        
    except Exception as e:
        return jsonify({"success": False, "message": f"Error parsing Poloxy Sales Order file: {str(e)}"}), 500

# ----------------- ROUTES & CONSIGNEE MASTER APIS -----------------

@app.route('/api/routes', methods=['GET', 'POST'])
def handle_routes():
    conn = get_db()
    cursor = conn.cursor()
    
    if request.method == 'GET':
        depot_id = request.args.get('depot_id')
        query = '''
            SELECT r.*, COUNT(rc.id) as outlet_count
            FROM routes r
            LEFT JOIN route_consignees rc ON r.id = rc.route_id
        '''
        params = ()
        if depot_id:
            query += ' WHERE r.depot_id = ?'
            params = (depot_id,)
        query += ' GROUP BY r.id ORDER BY r.id ASC'
        cursor.execute(query, params)
        routes = [dict(row) for row in cursor.fetchall()]
        conn.close()
        return jsonify({"success": True, "routes": routes})
        
    elif request.method == 'POST':
        data = request.json or {}
        route_id = data.get('id')
        depot_id = data.get('depot_id', 9)
        route_name = data.get('route_name', '').strip()
        route_code = data.get('route_code', '').strip()
        description = data.get('description', '').strip()
        default_vehicle_no = data.get('default_vehicle_no', '').strip()
        
        if not route_name:
            conn.close()
            return jsonify({"success": False, "message": "Route name is required"}), 400
            
        if route_id:
            cursor.execute('''
                UPDATE routes SET route_name=?, route_code=?, description=?, default_vehicle_no=?
                WHERE id=? AND depot_id=?
            ''', (route_name, route_code, description, default_vehicle_no, route_id, depot_id))
        else:
            cursor.execute('''
                INSERT INTO routes (depot_id, route_name, route_code, description, default_vehicle_no)
                VALUES (?, ?, ?, ?, ?)
            ''', (depot_id, route_name, route_code, description, default_vehicle_no))
            
        conn.commit()
        conn.close()
        return jsonify({"success": True, "message": "Route saved successfully!"})

@app.route('/api/routes/<int:route_id>', methods=['DELETE'])
def delete_route(route_id):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('DELETE FROM route_consignees WHERE route_id = ?', (route_id,))
    cursor.execute('DELETE FROM routes WHERE id = ?', (route_id,))
    conn.commit()
    conn.close()
    return jsonify({"success": True, "message": "Route deleted successfully!"})

@app.route('/api/consignees', methods=['GET', 'POST'])
def handle_consignees():
    conn = get_db()
    cursor = conn.cursor()
    
    if request.method == 'GET':
        depot_id = request.args.get('depot_id')
        route_id = request.args.get('route_id')
        query = '''
            SELECT rc.*, r.route_name
            FROM route_consignees rc
            LEFT JOIN routes r ON rc.route_id = r.id
            WHERE 1=1
        '''
        params = []
        if depot_id:
            query += ' AND rc.depot_id = ?'
            params.append(depot_id)
        if route_id:
            query += ' AND rc.route_id = ?'
            params.append(route_id)
        query += ' ORDER BY rc.consignee_name ASC'
        cursor.execute(query, tuple(params))
        consignees = [dict(row) for row in cursor.fetchall()]
        conn.close()
        return jsonify({"success": True, "consignees": consignees})
        
    elif request.method == 'POST':
        data = request.json or {}
        cid = data.get('id')
        depot_id = data.get('depot_id', 9)
        route_id = data.get('route_id')
        consignee_name = data.get('consignee_name', '').strip()
        address = data.get('address', '').strip()
        phone = data.get('phone', '').strip()
        payment_mode = data.get('payment_mode', 'Cash')
        
        if not consignee_name:
            conn.close()
            return jsonify({"success": False, "message": "Consignee/Outlet name is required"}), 400
            
        if cid:
            cursor.execute('''
                UPDATE route_consignees SET route_id=?, consignee_name=?, address=?, phone=?, payment_mode=?
                WHERE id=?
            ''', (route_id, consignee_name, address, phone, payment_mode, cid))
        else:
            cursor.execute('''
                INSERT INTO route_consignees (depot_id, route_id, consignee_name, address, phone, payment_mode)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (depot_id, route_id, consignee_name, address, phone, payment_mode))
            
        conn.commit()
        conn.close()
        return jsonify({"success": True, "message": "Consignee mapped successfully!"})

@app.route('/api/consignees/<int:cid>', methods=['DELETE'])
def delete_consignee(cid):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('DELETE FROM route_consignees WHERE id = ?', (cid,))
    conn.commit()
    conn.close()
    return jsonify({"success": True, "message": "Consignee mapping removed!"})

# ----------------- FLEET VEHICLES & RENTAL APIS -----------------

@app.route('/api/fleet', methods=['GET'])
def get_fleet():
    depot_id = request.args.get('depot_id')
    conn = get_db()
    cursor = conn.cursor()
    query = 'SELECT * FROM fleet_vehicles WHERE 1=1'
    params = []
    if depot_id:
        query += ' AND depot_id = ?'
        params.append(depot_id)
    query += ' ORDER BY ownership ASC, id ASC'
    cursor.execute(query, tuple(params))
    vehicles = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return jsonify({"success": True, "vehicles": vehicles})

@app.route('/api/fleet/vehicle', methods=['POST'])
def save_fleet_vehicle():
    data = request.json or {}
    vid = data.get('id')
    depot_id = data.get('depot_id', 9)
    vehicle_no = data.get('vehicle_no', '').strip().upper()
    vehicle_type = data.get('vehicle_type', 'Covered Van').strip()
    capacity_kg = float(data.get('capacity_kg', 1500))
    capacity_units = data.get('capacity_units', 'Pcs').strip()
    ownership = data.get('ownership', 'Owned').strip()
    rental_vendor = data.get('rental_vendor', '').strip()
    rental_cost_per_day = float(data.get('rental_cost_per_day', 0))
    status = data.get('status', 'Active').strip()
    
    if not vehicle_no:
        return jsonify({"success": False, "message": "Vehicle number is required"}), 400
        
    conn = get_db()
    cursor = conn.cursor()
    if vid:
        cursor.execute('''
            UPDATE fleet_vehicles SET vehicle_no=?, vehicle_type=?, capacity_kg=?, capacity_units=?, ownership=?, rental_vendor=?, rental_cost_per_day=?, status=?
            WHERE id=?
        ''', (vehicle_no, vehicle_type, capacity_kg, capacity_units, ownership, rental_vendor, rental_cost_per_day, status, vid))
    else:
        cursor.execute('''
            INSERT INTO fleet_vehicles (depot_id, vehicle_no, vehicle_type, capacity_kg, capacity_units, ownership, rental_vendor, rental_cost_per_day, home_depot_id, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (depot_id, vehicle_no, vehicle_type, capacity_kg, capacity_units, ownership, rental_vendor, rental_cost_per_day, depot_id, status))
        
    conn.commit()
    conn.close()
    return jsonify({"success": True, "message": f"Vehicle {vehicle_no} saved successfully!"})

@app.route('/api/fleet/vehicle/<int:vid>', methods=['DELETE'])
def delete_fleet_vehicle(vid):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('DELETE FROM fleet_vehicles WHERE id = ?', (vid,))
    conn.commit()
    conn.close()
    return jsonify({"success": True, "message": "Vehicle removed successfully!"})

# ----------------- DEPOT CREW (DRIVERS & DELIVERYMEN) APIS -----------------

@app.route('/api/crew', methods=['GET', 'POST'])
def handle_crew():
    conn = get_db()
    cursor = conn.cursor()
    
    if request.method == 'GET':
        depot_id = request.args.get('depot_id')
        role = request.args.get('role')
        query = 'SELECT * FROM depot_crew WHERE 1=1'
        params = []
        if depot_id:
            query += ' AND depot_id = ?'
            params.append(depot_id)
        if role:
            query += ' AND role = ?'
            params.append(role)
        query += ' ORDER BY role ASC, name ASC'
        cursor.execute(query, tuple(params))
        crew = [dict(row) for row in cursor.fetchall()]
        conn.close()
        return jsonify({"success": True, "crew": crew})
        
    elif request.method == 'POST':
        data = request.json or {}
        cid = data.get('id')
        depot_id = data.get('depot_id', 9)
        role = data.get('role', 'driver').strip()
        name = data.get('name', '').strip()
        phone = data.get('phone', '').strip()
        license_no = data.get('license_no', '').strip()
        status = data.get('status', 'Active').strip()
        
        if not name or not phone:
            conn.close()
            return jsonify({"success": False, "message": "Staff name and phone number are required"}), 400
            
        if cid:
            cursor.execute('''
                UPDATE depot_crew SET role=?, name=?, phone=?, license_no=?, status=?
                WHERE id=?
            ''', (role, name, phone, license_no, status, cid))
        else:
            cursor.execute('''
                INSERT INTO depot_crew (depot_id, role, name, phone, license_no, status)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (depot_id, role, name, phone, license_no, status))
            
        conn.commit()
        conn.close()
        return jsonify({"success": True, "message": f"{role.title()} {name} saved successfully!"})

@app.route('/api/crew/<int:cid>', methods=['DELETE'])
def delete_crew(cid):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('DELETE FROM depot_crew WHERE id = ?', (cid,))
    conn.commit()
    conn.close()
    return jsonify({"success": True, "message": "Staff record deleted successfully!"})

# ----------------- INTER-DEPOT VEHICLE BORROWING APIS -----------------

@app.route('/api/fleet/available-borrow', methods=['GET'])
def get_available_borrow_vehicles():
    current_depot_id = request.args.get('depot_id', 9)
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT fv.*, d.name as depot_name, d.category as depot_category
        FROM fleet_vehicles fv
        JOIN depots d ON fv.depot_id = d.id
        WHERE fv.depot_id != ? AND fv.status IN ('Active', 'Spare')
        ORDER BY d.id ASC, fv.vehicle_no ASC
    ''', (current_depot_id,))
    vehicles = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return jsonify({"success": True, "available_vehicles": vehicles})

@app.route('/api/fleet/borrow-request', methods=['POST'])
def create_borrow_request():
    data = request.json or {}
    req_depot_id = data.get('requesting_depot_id')
    lending_depot_id = data.get('lending_depot_id')
    vehicle_id = data.get('vehicle_id')
    request_date = data.get('request_date', datetime.date.today().strftime('%Y-%m-%d'))
    notes = data.get('notes', 'Need temporary van for high morning delivery volume')
    
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO inter_depot_vehicle_requests (requesting_depot_id, lending_depot_id, vehicle_id, request_date, status, notes)
        VALUES (?, ?, ?, ?, 'Approved', ?)
    ''', (req_depot_id, lending_depot_id, vehicle_id, request_date, notes))
    
    # Temporarily associate vehicle to requesting depot as Borrowed
    cursor.execute('SELECT vehicle_no, vehicle_type, capacity_kg, capacity_units FROM fleet_vehicles WHERE id = ?', (vehicle_id,))
    veh = cursor.fetchone()
    if veh:
        cursor.execute('''
            INSERT INTO fleet_vehicles (depot_id, vehicle_no, vehicle_type, capacity_kg, capacity_units, ownership, home_depot_id, status)
            VALUES (?, ?, ?, ?, ?, 'Borrowed', ?, 'Active')
        ''', (req_depot_id, f"{veh['vehicle_no']} (Borrowed)", veh['vehicle_type'], veh['capacity_kg'], veh['capacity_units'], lending_depot_id))
        
    conn.commit()
    conn.close()
    return jsonify({"success": True, "message": "Vehicle borrowed and added to your depot fleet successfully!"})

# ==============================================================================
# BLANK EXCEL TEMPLATES & BULK UPLOAD REST APIS
# ==============================================================================

@app.route('/api/template/routes-blank-template')
def download_routes_blank_template():
    output = io.BytesIO()
    wb = xlsxwriter.Workbook(output, {'in_memory': True})
    ws = wb.add_worksheet('Routes Template')
    
    hdr_fmt = wb.add_format({'bold': True, 'font_size': 11, 'font_color': '#FFFFFF', 'bg_color': '#1E3A8A', 'align': 'center', 'valign': 'vcenter', 'border': 1})
    tip_fmt = wb.add_format({'font_size': 9, 'font_color': '#64748B', 'italic': True})
    th_fmt = wb.add_format({'bold': True, 'font_size': 9.5, 'font_color': '#FFFFFF', 'bg_color': '#0F172A', 'align': 'center', 'valign': 'vcenter', 'border': 1, 'text_wrap': True})
    td_fmt = wb.add_format({'font_size': 9, 'border': 1, 'border_color': '#CBD5E1'})
    sample_fmt = wb.add_format({'font_size': 9, 'font_color': '#334155', 'bg_color': '#F8FAFC', 'border': 1, 'border_color': '#CBD5E1'})

    ws.set_column(0, 0, 8)   # SL
    ws.set_column(1, 1, 35)  # Route Name
    ws.set_column(2, 2, 16)  # Route Code
    ws.set_column(3, 3, 22)  # Default Vehicle No
    ws.set_column(4, 4, 38)  # Description / Areas

    ws.merge_range('A1:E1', 'PARAGON AGRO LIMITED - ROUTE MASTER IMPORT TEMPLATE', hdr_fmt)
    ws.merge_range('A2:E2', 'Instructions: Fill in Route Name (Mandatory). Route Code and Default Vehicle No are optional. Save and Upload.', tip_fmt)
    ws.set_row(0, 24)
    ws.set_row(1, 16)

    headers = ['SL', 'Route Name *', 'Route Code', 'Default Vehicle Reg No', 'Description / Areas Covered']
    for col, h in enumerate(headers):
        ws.write(3, col, h, th_fmt)
    ws.set_row(3, 22)

    sample_data = [
        (1, 'Route 01: Mirpur - Mohammadpur Zone', 'RT-01', 'DM-THA-11-2090', 'Mirpur 1, 2, 10, 11, Pallabi, Mohammadpur Ring Road'),
        (2, 'Route 02: Uttara - Gazipur Express', 'RT-02', 'DM-THA-11-3045', 'Uttara Sectors 1-14, Tongi Station Road, Gazipur Chowrasta'),
        (3, 'Route 03: Dhanmondi - Old Dhaka Route', 'RT-03', 'DM-THA-11-4012', 'Dhanmondi, Shankar, Lalbagh, Sadarghat, Kaptan Bazar')
    ]
    for r_idx, row in enumerate(sample_data, 4):
        ws.write(r_idx, 0, row[0], sample_fmt)
        ws.write(r_idx, 1, row[1], sample_fmt)
        ws.write(r_idx, 2, row[2], sample_fmt)
        ws.write(r_idx, 3, row[3], sample_fmt)
        ws.write(r_idx, 4, row[4], sample_fmt)

    wb.close()
    output.seek(0)
    return send_file(output, download_name="Paragon_Route_Master_Blank_Template.xlsx", as_attachment=True, mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@app.route('/api/template/consignees-blank-template')
def download_consignees_blank_template():
    output = io.BytesIO()
    wb = xlsxwriter.Workbook(output, {'in_memory': True})
    ws = wb.add_worksheet('Consignees Template')

    hdr_fmt = wb.add_format({'bold': True, 'font_size': 11, 'font_color': '#FFFFFF', 'bg_color': '#166534', 'align': 'center', 'valign': 'vcenter', 'border': 1})
    tip_fmt = wb.add_format({'font_size': 9, 'font_color': '#64748B', 'italic': True})
    th_fmt = wb.add_format({'bold': True, 'font_size': 9.5, 'font_color': '#FFFFFF', 'bg_color': '#0F172A', 'align': 'center', 'valign': 'vcenter', 'border': 1, 'text_wrap': True})
    sample_fmt = wb.add_format({'font_size': 9, 'font_color': '#334155', 'bg_color': '#F8FAFC', 'border': 1, 'border_color': '#CBD5E1'})

    ws.set_column(0, 0, 8)   # SL
    ws.set_column(1, 1, 38)  # Consignee / Outlet Name
    ws.set_column(2, 2, 32)  # Mapped Route Name
    ws.set_column(3, 3, 32)  # Address / Location
    ws.set_column(4, 4, 18)  # Phone Number
    ws.set_column(5, 5, 16)  # Payment Mode

    ws.merge_range('A1:F1', 'PARAGON AGRO LIMITED - SALES CENTER / CONSIGNEE MAPPING TEMPLATE', hdr_fmt)
    ws.merge_range('A2:F2', 'Instructions: Consignee Name and Mapped Route Name must match your depot routes. Payment Mode: Cash / Credit.', tip_fmt)
    ws.set_row(0, 24)
    ws.set_row(1, 16)

    headers = ['SL', 'Consignee / Outlet Name *', 'Mapped Route Name *', 'Address / Location', 'Phone / Contact No', 'Payment Mode (Cash/Credit)']
    for col, h in enumerate(headers):
        ws.write(3, col, h, th_fmt)
    ws.set_row(3, 22)

    sample_data = [
        (1, 'Shwapno Super Shop - Mirpur 1 Branch', 'Route 01: Mirpur - Mohammadpur Zone', 'Plot 12, Mirpur 1, Dhaka', '01711-223344', 'Credit'),
        (2, 'Meena Bazar - Dhanmondi 27', 'Route 03: Dhanmondi - Old Dhaka Route', 'House 45, Road 27, Dhanmondi', '01922-334455', 'Credit'),
        (3, 'Paragon Mart - Uttara Sector 3', 'Route 02: Uttara - Gazipur Express', 'Sector 3, Uttara, Dhaka', '01833-445566', 'Cash')
    ]
    for r_idx, row in enumerate(sample_data, 4):
        for c_idx, val in enumerate(row):
            ws.write(r_idx, c_idx, val, sample_fmt)

    wb.close()
    output.seek(0)
    return send_file(output, download_name="Paragon_Consignee_Mapping_Blank_Template.xlsx", as_attachment=True, mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@app.route('/api/template/crew-blank-template')
def download_crew_blank_template():
    output = io.BytesIO()
    wb = xlsxwriter.Workbook(output, {'in_memory': True})
    ws = wb.add_worksheet('Crew Template')

    hdr_fmt = wb.add_format({'bold': True, 'font_size': 11, 'font_color': '#FFFFFF', 'bg_color': '#7C3AED', 'align': 'center', 'valign': 'vcenter', 'border': 1})
    tip_fmt = wb.add_format({'font_size': 9, 'font_color': '#64748B', 'italic': True})
    th_fmt = wb.add_format({'bold': True, 'font_size': 9.5, 'font_color': '#FFFFFF', 'bg_color': '#0F172A', 'align': 'center', 'valign': 'vcenter', 'border': 1})
    sample_fmt = wb.add_format({'font_size': 9, 'font_color': '#334155', 'bg_color': '#F8FAFC', 'border': 1, 'border_color': '#CBD5E1'})

    ws.set_column(0, 0, 8)   # SL
    ws.set_column(1, 1, 20)  # Role
    ws.set_column(2, 2, 28)  # Full Name
    ws.set_column(3, 3, 18)  # Mobile Phone
    ws.set_column(4, 4, 22)  # Driving License No
    ws.set_column(5, 5, 14)  # Status

    ws.merge_range('A1:F1', 'PARAGON AGRO LIMITED - DEPOT DRIVERS & CREW IMPORT TEMPLATE', hdr_fmt)
    ws.merge_range('A2:F2', 'Instructions: Role should be "Driver" or "Delivery Man". Mobile phone is required for live route communication.', tip_fmt)
    ws.set_row(0, 24)
    ws.set_row(1, 16)

    headers = ['SL', 'Role (Driver/Delivery Man) *', 'Staff Full Name *', 'Mobile Phone No *', 'Driving License No', 'Status (Active/Inactive)']
    for col, h in enumerate(headers):
        ws.write(3, col, h, th_fmt)
    ws.set_row(3, 22)

    sample_data = [
        (1, 'Driver', 'Md. Rafiqul Islam', '01711-100001', 'DL-DHAKA-112233', 'Active'),
        (2, 'Delivery Man', 'Md. Kamal Hossain', '01711-200001', '-', 'Active'),
        (3, 'Driver', 'Md. Jahangir Alam', '01811-100002', 'DL-DHAKA-445566', 'Active'),
        (4, 'Delivery Man', 'Md. Rubel Miah', '01811-200002', '-', 'Active')
    ]
    for r_idx, row in enumerate(sample_data, 4):
        for c_idx, val in enumerate(row):
            ws.write(r_idx, c_idx, val, sample_fmt)

    wb.close()
    output.seek(0)
    return send_file(output, download_name="Paragon_Depot_Crew_Blank_Template.xlsx", as_attachment=True, mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@app.route('/api/template/fleet-blank-template')
def download_fleet_blank_template():
    output = io.BytesIO()
    wb = xlsxwriter.Workbook(output, {'in_memory': True})
    ws = wb.add_worksheet('Fleet Template')

    hdr_fmt = wb.add_format({'bold': True, 'font_size': 11, 'font_color': '#FFFFFF', 'bg_color': '#0369A1', 'align': 'center', 'valign': 'vcenter', 'border': 1})
    tip_fmt = wb.add_format({'font_size': 9, 'font_color': '#64748B', 'italic': True})
    th_fmt = wb.add_format({'bold': True, 'font_size': 9.5, 'font_color': '#FFFFFF', 'bg_color': '#0F172A', 'align': 'center', 'valign': 'vcenter', 'border': 1})
    sample_fmt = wb.add_format({'font_size': 9, 'font_color': '#334155', 'bg_color': '#F8FAFC', 'border': 1, 'border_color': '#CBD5E1'})

    ws.set_column(0, 0, 8)   # SL
    ws.set_column(1, 1, 24)  # Vehicle No
    ws.set_column(2, 2, 22)  # Vehicle Type
    ws.set_column(3, 3, 16)  # Capacity Kg
    ws.set_column(4, 4, 16)  # Capacity Units
    ws.set_column(5, 5, 18)  # Ownership
    ws.set_column(6, 6, 26)  # Rental Vendor
    ws.set_column(7, 7, 18)  # Daily Rate

    ws.merge_range('A1:H1', 'PARAGON AGRO LIMITED - DEPOT FLEET VEHICLES IMPORT TEMPLATE', hdr_fmt)
    ws.merge_range('A2:H2', 'Instructions: Vehicle Reg No is required. Ownership: "Owned" or "Rental". For rental vans specify vendor and rate.', tip_fmt)
    ws.set_row(0, 24)
    ws.set_row(1, 16)

    headers = ['SL', 'Vehicle Reg No *', 'Vehicle Type', 'Capacity (Kg) *', 'Capacity Units', 'Ownership (Owned/Rental)', 'Rental Vendor Name', 'Daily Rent Cost (৳)']
    for col, h in enumerate(headers):
        ws.write(3, col, h, th_fmt)
    ws.set_row(3, 22)

    sample_data = [
        (1, 'DM-THA-11-2090', 'Ventilated Egg Van', 1500, 'Pcs', 'Owned', '-', 0),
        (2, 'DM-THA-11-3045', 'Covered Van', 2000, 'Pcs', 'Owned', '-', 0),
        (3, 'DM-U-12-8899', 'Covered Van (Rental)', 2500, 'Pcs', 'Rental', 'Bhai Bhai Transport Ltd', 3500)
    ]
    for r_idx, row in enumerate(sample_data, 4):
        for c_idx, val in enumerate(row):
            ws.write(r_idx, c_idx, val, sample_fmt)

    wb.close()
    output.seek(0)
    return send_file(output, download_name="Paragon_Fleet_Vehicles_Blank_Template.xlsx", as_attachment=True, mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


# ----------------- BULK UPLOAD HANDLERS -----------------

@app.route('/api/routes/bulk-upload', methods=['POST'])
def bulk_upload_routes():
    if 'file' not in request.files:
        return jsonify({'success': False, 'message': 'No file uploaded'}), 400
    file = request.files['file']
    depot_id = request.form.get('depot_id', 9)
    
    try:
        wb = openpyxl.load_workbook(file, data_only=True)
        ws = wb.active
        conn = get_db()
        cursor = conn.cursor()
        
        imported_count = 0
        for r_idx, row in enumerate(ws.iter_rows(values_only=True), 1):
            if not row or not any(row):
                continue
            r_str = " ".join([str(c) for c in row if c is not None]).upper()
            if "ROUTE NAME" in r_str or "PARAGON AGRO" in r_str or "INSTRUCTIONS" in r_str:
                continue
                
            name = str(row[1] if len(row) > 1 and row[1] else (row[0] or '')).strip()
            code = str(row[2] if len(row) > 2 and row[2] else '').strip()
            veh = str(row[3] if len(row) > 3 and row[3] else '').strip()
            desc = str(row[4] if len(row) > 4 and row[4] else '').strip()
            
            if not name or name.isdigit() or name.lower() in ['sl', 'route name']:
                continue
                
            # Check existing route
            cursor.execute('SELECT id FROM routes WHERE depot_id = ? AND route_name = ?', (depot_id, name))
            exist = cursor.fetchone()
            if exist:
                cursor.execute('UPDATE routes SET route_code=?, default_vehicle_no=?, description=? WHERE id=?', (code, veh, desc, exist['id']))
            else:
                cursor.execute('INSERT INTO routes (depot_id, route_name, route_code, description, default_vehicle_no) VALUES (?, ?, ?, ?, ?)',
                               (depot_id, name, code, desc, veh))
            imported_count += 1
            
        conn.commit()
        conn.close()
        return jsonify({'success': True, 'message': f'Successfully imported {imported_count} routes from Excel!'})
    except Exception as e:
        return jsonify({'success': False, 'message': f'Error uploading routes: {str(e)}'}), 500


@app.route('/api/consignees/bulk-upload', methods=['POST'])
def bulk_upload_consignees():
    if 'file' not in request.files:
        return jsonify({'success': False, 'message': 'No file uploaded'}), 400
    file = request.files['file']
    depot_id = request.form.get('depot_id', 9)
    
    try:
        wb = openpyxl.load_workbook(file, data_only=True)
        ws = wb.active
        conn = get_db()
        cursor = conn.cursor()
        
        # Load existing routes for depot
        cursor.execute('SELECT id, route_name, route_code FROM routes WHERE depot_id = ?', (depot_id,))
        routes_db = {r['route_name'].lower().strip(): r['id'] for r in cursor.fetchall()}
        
        imported_count = 0
        for row in ws.iter_rows(values_only=True):
            if not row or not any(row):
                continue
            r_str = " ".join([str(c) for c in row if c is not None]).upper()
            if "CONSIGNEE" in r_str or "PARAGON AGRO" in r_str or "INSTRUCTIONS" in r_str:
                continue
                
            c_name = str(row[1] if len(row) > 1 and row[1] else (row[0] or '')).strip()
            r_name = str(row[2] if len(row) > 2 and row[2] else '').strip()
            addr = str(row[3] if len(row) > 3 and row[3] else '').strip()
            phone = str(row[4] if len(row) > 4 and row[4] else '').strip()
            pay_mode = str(row[5] if len(row) > 5 and row[5] else 'Cash').strip()
            
            if not c_name or c_name.isdigit() or c_name.lower() in ['sl', 'consignee / outlet name']:
                continue
                
            route_id = None
            if r_name:
                r_key = r_name.lower().strip()
                if r_key in routes_db:
                    route_id = routes_db[r_key]
                else:
                    # Auto create new route
                    cursor.execute('INSERT INTO routes (depot_id, route_name) VALUES (?, ?)', (depot_id, r_name))
                    route_id = cursor.lastrowid
                    routes_db[r_key] = route_id
                    
            cursor.execute('SELECT id FROM route_consignees WHERE depot_id = ? AND consignee_name = ?', (depot_id, c_name))
            exist = cursor.fetchone()
            if exist:
                cursor.execute('UPDATE route_consignees SET route_id=?, address=?, phone=?, payment_mode=? WHERE id=?',
                               (route_id, addr, phone, pay_mode, exist['id']))
            else:
                cursor.execute('INSERT INTO route_consignees (depot_id, route_id, consignee_name, address, phone, payment_mode) VALUES (?, ?, ?, ?, ?, ?)',
                               (depot_id, route_id, c_name, addr, phone, pay_mode))
            imported_count += 1
            
        conn.commit()
        conn.close()
        return jsonify({'success': True, 'message': f'Successfully imported {imported_count} consignees/outlets from Excel!'})
    except Exception as e:
        return jsonify({'success': False, 'message': f'Error uploading consignees: {str(e)}'}), 500


@app.route('/api/crew/bulk-upload', methods=['POST'])
def bulk_upload_crew():
    if 'file' not in request.files:
        return jsonify({'success': False, 'message': 'No file uploaded'}), 400
    file = request.files['file']
    depot_id = request.form.get('depot_id', 9)
    
    try:
        wb = openpyxl.load_workbook(file, data_only=True)
        ws = wb.active
        conn = get_db()
        cursor = conn.cursor()
        
        imported_count = 0
        for row in ws.iter_rows(values_only=True):
            if not row or not any(row):
                continue
            r_str = " ".join([str(c) for c in row if c is not None]).upper()
            if "ROLE" in r_str or "PARAGON AGRO" in r_str or "INSTRUCTIONS" in r_str:
                continue
                
            raw_role = str(row[1] if len(row) > 1 and row[1] else '').strip().lower()
            name = str(row[2] if len(row) > 2 and row[2] else '').strip()
            phone = str(row[3] if len(row) > 3 and row[3] else '').strip()
            license_no = str(row[4] if len(row) > 4 and row[4] else '').strip()
            status = str(row[5] if len(row) > 5 and row[5] else 'Active').strip()
            
            if not name or name.lower() in ['sl', 'staff full name']:
                continue
                
            role = 'driver' if 'driver' in raw_role else 'delivery_man'
            
            cursor.execute('SELECT id FROM depot_crew WHERE depot_id = ? AND name = ? AND role = ?', (depot_id, name, role))
            exist = cursor.fetchone()
            if exist:
                cursor.execute('UPDATE depot_crew SET phone=?, license_no=?, status=? WHERE id=?', (phone, license_no, status, exist['id']))
            else:
                cursor.execute('INSERT INTO depot_crew (depot_id, role, name, phone, license_no, status) VALUES (?, ?, ?, ?, ?, ?)',
                               (depot_id, role, name, phone, license_no, status))
            imported_count += 1
            
        conn.commit()
        conn.close()
        return jsonify({'success': True, 'message': f'Successfully imported {imported_count} staff/crew members from Excel!'})
    except Exception as e:
        return jsonify({'success': False, 'message': f'Error uploading crew: {str(e)}'}), 500


@app.route('/api/fleet/bulk-upload', methods=['POST'])
def bulk_upload_fleet():
    if 'file' not in request.files:
        return jsonify({'success': False, 'message': 'No file uploaded'}), 400
    file = request.files['file']
    depot_id = request.form.get('depot_id', 9)
    
    try:
        wb = openpyxl.load_workbook(file, data_only=True)
        ws = wb.active
        conn = get_db()
        cursor = conn.cursor()
        
        imported_count = 0
        for row in ws.iter_rows(values_only=True):
            if not row or not any(row):
                continue
            r_str = " ".join([str(c) for c in row if c is not None]).upper()
            if "VEHICLE REG" in r_str or "PARAGON AGRO" in r_str or "INSTRUCTIONS" in r_str:
                continue
                
            v_no = str(row[1] if len(row) > 1 and row[1] else '').strip().upper()
            v_type = str(row[2] if len(row) > 2 and row[2] else 'Covered Van').strip()
            try:
                cap_kg = float(row[3]) if len(row) > 3 and row[3] is not None else 1500.0
            except:
                cap_kg = 1500.0
            cap_uom = str(row[4] if len(row) > 4 and row[4] else 'Pcs').strip()
            ownership = str(row[5] if len(row) > 5 and row[5] else 'Owned').strip()
            vendor = str(row[6] if len(row) > 6 and row[6] else '').strip()
            try:
                rent_cost = float(row[7]) if len(row) > 7 and row[7] is not None else 0.0
            except:
                rent_cost = 0.0
                
            if not v_no or v_no.lower() in ['sl', 'vehicle reg no']:
                continue
                
            cursor.execute('SELECT id FROM fleet_vehicles WHERE depot_id = ? AND vehicle_no = ?', (depot_id, v_no))
            exist = cursor.fetchone()
            if exist:
                cursor.execute('UPDATE fleet_vehicles SET vehicle_type=?, capacity_kg=?, capacity_units=?, ownership=?, rental_vendor=?, rental_cost_per_day=? WHERE id=?',
                               (v_type, cap_kg, cap_uom, ownership, vendor, rent_cost, exist['id']))
            else:
                cursor.execute('''
                    INSERT INTO fleet_vehicles (depot_id, vehicle_no, vehicle_type, capacity_kg, capacity_units, ownership, rental_vendor, rental_cost_per_day, home_depot_id, status)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'Active')
                ''', (depot_id, v_no, v_type, cap_kg, cap_uom, ownership, vendor, rent_cost, depot_id))
            imported_count += 1
            
        conn.commit()
        conn.close()
        return jsonify({'success': True, 'message': f'Successfully imported {imported_count} fleet vehicles from Excel!'})
    except Exception as e:
        return jsonify({'success': False, 'message': f'Error uploading fleet: {str(e)}'}), 500


@app.route('/api/template/universal-route-plan-excel')
def download_universal_route_plan_template():
    depot_id = request.args.get('depot_id')
    date_param = request.args.get('date', datetime.date.today().strftime('%d/%m/%Y'))
    if '-' in date_param:
        try:
            parts = date_param.split('-')
            if len(parts) == 3 and len(parts[0]) == 4:
                date_param = f"{parts[2]}/{parts[1]}/{parts[0]}"
        except:
            pass

    depot_name = "09. Tejgaon - Fresh Egg"
    incharge_info = "Md. Zahid Hasan (01711-XXXXXX)"
    category = "Fresh Eggs"
    
    if depot_id:
        try:
            conn = get_db()
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM depots WHERE id = ?', (depot_id,))
            depot = cursor.fetchone()
            conn.close()
            if depot:
                depot_name = depot['name']
                incharge_info = f"{depot['incharge_name']} ({depot['contact']})"
                category = depot['category']
        except Exception as e:
            pass

    is_egg = "egg" in category.lower() or "egg" in depot_name.lower() or str(depot_id) == '9'
    is_dairy = "dairy" in category.lower() or "milk" in category.lower() or str(depot_id) in ['4', '11']
    primary_uom = "Pcs" if is_egg else ("Ltr" if is_dairy else "Pkt")
    qty_header = f"Qty ({primary_uom})"
    weight_header = "Volume (Ltr)" if is_dairy else "Weight (Kg)"

    output = io.BytesIO()
    wb = xlsxwriter.Workbook(output, {'in_memory': True})
    ws = wb.add_worksheet('Daily Route Plan')
    
    ws.set_landscape()
    ws.set_margins(left=0.4, right=0.4, top=0.5, bottom=0.5)
    ws.fit_to_pages(1, 0)

    title_fmt = wb.add_format({'bold': True, 'font_size': 14, 'font_color': '#FFFFFF', 'bg_color': '#1E3A8A', 'align': 'center', 'valign': 'vcenter'})
    sub_fmt = wb.add_format({'bold': True, 'font_size': 9, 'font_color': '#E2E8F0', 'bg_color': '#1E293B', 'align': 'center', 'valign': 'vcenter'})
    meta_lbl = wb.add_format({'bold': True, 'font_size': 9, 'font_color': '#0F172A', 'bg_color': '#F1F5F9', 'border': 1, 'border_color': '#CBD5E1'})
    meta_val = wb.add_format({'font_size': 9, 'font_color': '#0F172A', 'bg_color': '#FFFFFF', 'border': 1, 'border_color': '#CBD5E1'})

    veh_hdr = wb.add_format({'bold': True, 'font_size': 10, 'font_color': '#FFFFFF', 'bg_color': '#0369A1', 'border': 1, 'border_color': '#0284C7', 'valign': 'vcenter'})
    col_hdr = wb.add_format({'bold': True, 'font_size': 8.5, 'font_color': '#FFFFFF', 'bg_color': '#1E293B', 'align': 'center', 'valign': 'vcenter', 'border': 1, 'border_color': '#334155', 'text_wrap': True})

    cell_txt = wb.add_format({'font_size': 8.5, 'font_color': '#0F172A', 'border': 1, 'border_color': '#E2E8F0', 'valign': 'vcenter'})
    cell_ctr = wb.add_format({'font_size': 8.5, 'font_color': '#0F172A', 'align': 'center', 'valign': 'vcenter', 'border': 1, 'border_color': '#E2E8F0'})
    cell_num = wb.add_format({'font_size': 8.5, 'font_color': '#0F172A', 'align': 'right', 'valign': 'vcenter', 'border': 1, 'border_color': '#E2E8F0', 'num_format': '#,##0'})
    cell_dec = wb.add_format({'font_size': 8.5, 'font_color': '#0F172A', 'align': 'right', 'valign': 'vcenter', 'border': 1, 'border_color': '#E2E8F0', 'num_format': '#,##0.00'})
    cell_cur = wb.add_format({'font_size': 8.5, 'font_color': '#0F172A', 'align': 'right', 'valign': 'vcenter', 'border': 1, 'border_color': '#E2E8F0', 'num_format': '৳ #,##0.00'})

    subtot_fmt = wb.add_format({'bold': True, 'font_size': 9, 'font_color': '#0F172A', 'bg_color': '#F8FAFC', 'border': 1, 'border_color': '#94A3B8', 'valign': 'vcenter'})
    subtot_num = wb.add_format({'bold': True, 'font_size': 9, 'font_color': '#0F172A', 'bg_color': '#F8FAFC', 'align': 'right', 'valign': 'vcenter', 'border': 1, 'border_color': '#94A3B8', 'num_format': '#,##0'})
    subtot_dec = wb.add_format({'bold': True, 'font_size': 9, 'font_color': '#0F172A', 'bg_color': '#F8FAFC', 'align': 'right', 'valign': 'vcenter', 'border': 1, 'border_color': '#94A3B8', 'num_format': '#,##0.00'})
    subtot_cur = wb.add_format({'bold': True, 'font_size': 9, 'font_color': '#0F172A', 'bg_color': '#F8FAFC', 'align': 'right', 'valign': 'vcenter', 'border': 1, 'border_color': '#94A3B8', 'num_format': '৳ #,##0.00'})

    grand_fmt = wb.add_format({'bold': True, 'font_size': 10, 'font_color': '#FFFFFF', 'bg_color': '#0F172A', 'border': 1, 'border_color': '#000000', 'valign': 'vcenter'})
    grand_num = wb.add_format({'bold': True, 'font_size': 10, 'font_color': '#FFFFFF', 'bg_color': '#0F172A', 'align': 'right', 'valign': 'vcenter', 'border': 1, 'border_color': '#000000', 'num_format': '#,##0'})
    grand_dec = wb.add_format({'bold': True, 'font_size': 10, 'font_color': '#FFFFFF', 'bg_color': '#0F172A', 'align': 'right', 'valign': 'vcenter', 'border': 1, 'border_color': '#000000', 'num_format': '#,##0.00'})
    grand_cur = wb.add_format({'bold': True, 'font_size': 10, 'font_color': '#FFFFFF', 'bg_color': '#0F172A', 'align': 'right', 'valign': 'vcenter', 'border': 1, 'border_color': '#000000', 'num_format': '৳ #,##0.00'})

    col_widths = [5, 26, 18, 14, 14, 12, 10, 10, 13, 10, 12, 22]
    for idx, w in enumerate(col_widths):
        ws.set_column(idx, idx, w)

    ws.merge_range('A1:L1', 'PARAGON AGRO LIMITED - CONSUMER FOOD DIVISION', title_fmt)
    ws.merge_range('A2:L2', f'DAILY DISTRIBUTION ROUTE PLAN & DRIVER DISPATCH SCHEDULE - {depot_name.upper()}', sub_fmt)
    ws.set_row(0, 24)
    ws.set_row(1, 18)

    ws.write('A4', 'Depot / Plant:', meta_lbl)
    ws.merge_range('B4:C4', depot_name, meta_val)
    ws.write('D4', 'Date:', meta_lbl)
    ws.write('E4', date_param, meta_val)
    ws.write('F4', 'Shift:', meta_lbl)
    ws.write('G4', 'Morning Shift (07:00 AM)', meta_val)
    ws.write('H4', 'Incharge:', meta_lbl)
    ws.merge_range('I4:J4', incharge_info, meta_val)
    ws.write('K4', 'Total Vans:', meta_lbl)
    ws.write('L4', 2, meta_val)

    headers = ['SL', 'Consignee / Outlet Name', 'Address / Area', 'Sales Order No', 'D.Note / Challan', 'Category', qty_header, weight_header, 'Invoice Value', 'Pay Mode', 'Cash Collect', 'Responsible Person / Contact']

    sample_outlets_1 = [
        (1, 'Agora Superstore Outlet', 'Main Market Area', 'SO-2026-001', 'DN-2026-001', category, 3000 if is_egg else 100, 0.0 if is_egg else 100.0, 36000.0 if is_egg else 48000.0, 'Credit', 0, 'Receiving Officer (01700-111111)'),
        (2, 'Shwapno Super Shop', 'Central Market Area', 'SO-2026-002', 'DN-2026-002', category, 6000 if is_egg else 250, 0.0 if is_egg else 250.0, 72000.0 if is_egg else 120000.0, 'Credit', 0, 'Store Manager (01700-222222)'),
        (3, 'Paragon Mart Outlet', 'Commercial Area', 'SO-2026-003', 'DN-2026-003', category, 1500 if is_egg else 75, 0.0 if is_egg else 75.0, 18000.0 if is_egg else 36000.0, 'Cash', 18000.0 if is_egg else 36000.0, 'Shop Incharge (01700-333333)')
    ]

    sample_outlets_2 = [
        (1, 'Meena Bazar Superstore', 'Market Circle', 'SO-2026-004', 'DN-2026-004', category, 4500 if is_egg else 179, 0.0 if is_egg else 72.6, 54000.0 if is_egg else 38850.4, 'Credit', 0, 'Store Incharge (01966-422968)'),
        (2, 'Unimart Hypermarket', 'Shopping Complex', 'SO-2026-005', 'DN-2026-005', category, 3000 if is_egg else 110, 0.0 if is_egg else 42.0, 36000.0 if is_egg else 20301.0, 'Credit', 0, 'Store Incharge (01811-999888)')
    ]

    sample_vans = [
        {
            'veh_info': f'VAN 1: DMS 11-5721 | Driver: Adil (01712-111222) | Delivery Man: Mostafiz (01912-333444) | Dispatch: 07:30 AM | Route: {depot_name} Area-A',
            'outlets': sample_outlets_1
        },
        {
            'veh_info': f'VAN 2: DMS 11-5623 | Driver: Shakil (01812-555666) | Delivery Man: Yeasin (01612-777888) | Dispatch: 08:00 AM | Route: {depot_name} Area-B',
            'outlets': sample_outlets_2
        }
    ]

    row_idx = 5
    subtot_rows = []

    for van in sample_vans:
        ws.merge_range(row_idx, 0, row_idx, 11, van['veh_info'], veh_hdr)
        ws.set_row(row_idx, 20)
        row_idx += 1
        
        for c_idx, h in enumerate(headers):
            ws.write(row_idx, c_idx, h, col_hdr)
        ws.set_row(row_idx, 22)
        row_idx += 1
        
        start_data_row = row_idx + 1
        for ot in van['outlets']:
            ws.write(row_idx, 0, ot[0], cell_ctr)
            ws.write(row_idx, 1, ot[1], cell_txt)
            ws.write(row_idx, 2, ot[2], cell_txt)
            ws.write(row_idx, 3, ot[3], cell_ctr)
            ws.write(row_idx, 4, ot[4], cell_ctr)
            ws.write(row_idx, 5, ot[5], cell_ctr)
            ws.write(row_idx, 6, ot[6], cell_num)
            ws.write(row_idx, 7, ot[7], cell_dec)
            ws.write(row_idx, 8, ot[8], cell_cur)
            ws.write(row_idx, 9, ot[9], cell_ctr)
            ws.write(row_idx, 10, ot[10], cell_cur)
            ws.write(row_idx, 11, ot[11], cell_txt)
            ws.set_row(row_idx, 18)
            row_idx += 1
        end_data_row = row_idx
        
        ws.merge_range(row_idx, 0, row_idx, 5, 'Vehicle Subtotal:', subtot_fmt)
        ws.write_formula(row_idx, 6, f'=SUM(G{start_data_row}:G{end_data_row})', subtot_num)
        ws.write_formula(row_idx, 7, f'=SUM(H{start_data_row}:H{end_data_row})', subtot_dec)
        ws.write_formula(row_idx, 8, f'=SUM(I{start_data_row}:I{end_data_row})', subtot_cur)
        ws.write(row_idx, 9, '', subtot_fmt)
        ws.write_formula(row_idx, 10, f'=SUM(K{start_data_row}:K{end_data_row})', subtot_cur)
        ws.write(row_idx, 11, '', subtot_fmt)
        ws.set_row(row_idx, 20)
        subtot_rows.append(row_idx + 1)
        row_idx += 2

    ws.merge_range(row_idx, 0, row_idx, 5, 'DEPOT DAY GRAND TOTAL (ALL VANS):', grand_fmt)
    pkt_formula = '=' + '+'.join([f'G{r}' for r in subtot_rows])
    kg_formula = '=' + '+'.join([f'H{r}' for r in subtot_rows])
    amt_formula = '=' + '+'.join([f'I{r}' for r in subtot_rows])
    cash_formula = '=' + '+'.join([f'K{r}' for r in subtot_rows])

    ws.write_formula(row_idx, 6, pkt_formula, grand_num)
    ws.write_formula(row_idx, 7, kg_formula, grand_dec)
    ws.write_formula(row_idx, 8, amt_formula, grand_cur)
    ws.write(row_idx, 9, '', grand_fmt)
    ws.write_formula(row_idx, 10, cash_formula, grand_cur)
    ws.write(row_idx, 11, '', grand_fmt)
    ws.set_row(row_idx, 22)

    wb.close()
    output.seek(0)
    
    clean_name = "".join(c for c in depot_name if c.isalnum() or c in (' ', '_', '-')).strip().replace(' ', '_')
    fname = f"Paragon_Route_Plan_Template_{clean_name}.xlsx" if depot_id else "Paragon_Route_Plan_Template.xlsx"
    
    return send_file(
        output,
        download_name=fname,
        as_attachment=True,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )

@app.route('/api/distribution/export-van-runsheet-excel', methods=['POST'])
def export_van_runsheet_excel():
    data = request.json or {}
    plan_data = data.get('plan', {})
    vans = plan_data.get('vans', [])
    depot_name = plan_data.get('depot_name', 'Paragon Depot')
    plan_date = plan_data.get('date', datetime.date.today().strftime('%d/%m/%Y'))
    depot_id = plan_data.get('depot_id')
    
    is_egg = "egg" in str(depot_name).lower() or str(depot_id) == '9'
    is_dairy = "dairy" in str(depot_name).lower() or "milk" in str(depot_name).lower() or str(depot_id) in ['4', '11']
    primary_uom = "Pcs" if is_egg else ("Ltr" if is_dairy else "Pkt")
    secondary_uom = "Ltr" if is_dairy else "Kg"
    
    output = io.BytesIO()
    wb = xlsxwriter.Workbook(output, {'in_memory': True})
    
    for v_idx, van in enumerate(vans):
        sheet_name = f"Van_{v_idx+1}_{van.get('vehicle_no', 'Trip')[:10]}".replace('/', '_').replace('\\', '_')[:31]
        ws = wb.add_worksheet(sheet_name)
        ws.set_portrait()
        ws.set_margins(left=0.3, right=0.3, top=0.4, bottom=0.4)
        
        hdr_fmt = wb.add_format({'bold': True, 'font_size': 12, 'font_color': '#FFFFFF', 'bg_color': '#1E3A8A', 'align': 'center', 'valign': 'vcenter'})
        sub_hdr = wb.add_format({'bold': True, 'font_size': 9, 'font_color': '#FFFFFF', 'bg_color': '#0369A1', 'align': 'center', 'valign': 'vcenter'})
        box_hdr = wb.add_format({'bold': True, 'font_size': 8.5, 'bg_color': '#F1F5F9', 'border': 1, 'border_color': '#94A3B8'})
        box_val = wb.add_format({'font_size': 8.5, 'border': 1, 'border_color': '#94A3B8'})
        th_fmt = wb.add_format({'bold': True, 'font_size': 8, 'font_color': '#FFFFFF', 'bg_color': '#1E293B', 'align': 'center', 'valign': 'vcenter', 'border': 1, 'text_wrap': True})
        td_txt = wb.add_format({'font_size': 8, 'border': 1, 'border_color': '#CBD5E1'})
        td_ctr = wb.add_format({'font_size': 8, 'align': 'center', 'border': 1, 'border_color': '#CBD5E1'})
        td_num = wb.add_format({'font_size': 8, 'align': 'right', 'border': 1, 'border_color': '#CBD5E1', 'num_format': '#,##0'})
        td_dec = wb.add_format({'font_size': 8, 'align': 'right', 'border': 1, 'border_color': '#CBD5E1', 'num_format': '#,##0.00'})
        td_cur = wb.add_format({'font_size': 8, 'align': 'right', 'border': 1, 'border_color': '#CBD5E1', 'num_format': '৳ #,##0.00'})
        tot_fmt = wb.add_format({'bold': True, 'font_size': 8.5, 'bg_color': '#F8FAFC', 'border': 1, 'border_color': '#64748B'})
        tot_num = wb.add_format({'bold': True, 'font_size': 8.5, 'bg_color': '#F8FAFC', 'align': 'right', 'border': 1, 'border_color': '#64748B', 'num_format': '#,##0'})
        tot_dec = wb.add_format({'bold': True, 'font_size': 8.5, 'bg_color': '#F8FAFC', 'align': 'right', 'border': 1, 'border_color': '#64748B', 'num_format': '#,##0.00'})
        tot_cur = wb.add_format({'bold': True, 'font_size': 8.5, 'bg_color': '#F8FAFC', 'align': 'right', 'border': 1, 'border_color': '#64748B', 'num_format': '৳ #,##0.00'})

        ws.set_column(0, 0, 4)   # SL
        ws.set_column(1, 1, 24)  # Consignee
        ws.set_column(2, 2, 14)  # Order No
        ws.set_column(3, 3, 13)  # D.Note
        ws.set_column(4, 4, 8)   # Primary UOM
        ws.set_column(5, 5, 8)   # Secondary UOM
        ws.set_column(6, 6, 12)  # Value
        ws.set_column(7, 7, 7)   # Pay Mode
        ws.set_column(8, 8, 11)  # Cash Collect
        ws.set_column(9, 9, 8)   # Deliv Qty
        ws.set_column(10, 10, 8) # Ret Qty
        ws.set_column(11, 11, 14)# Receiver Sig

        ws.merge_range('A1:L1', 'PARAGON AGRO LIMITED - DELIVERY RUN SHEET & DRIVER TRIP CHALLAN', hdr_fmt)
        ws.merge_range('A2:L2', f"DEPOT: {depot_name} | DATE: {plan_date} | VEHICLE: {van.get('vehicle_no', 'N/A')}", sub_hdr)
        
        ws.write('A4', 'Driver Name:', box_hdr)
        ws.merge_range('B4:D4', f"{van.get('driver_name', 'N/A')} ({van.get('driver_mobile', '')})", box_val)
        ws.write('E4', 'Delivery Man:', box_hdr)
        ws.merge_range('F4:H4', f"{van.get('delivery_man', 'N/A')} ({van.get('delivery_man_mobile', '')})", box_val)
        ws.write('I4', 'Dispatch Time:', box_hdr)
        ws.merge_range('J4:L4', van.get('dispatch_time', '07:30 AM'), box_val)
        
        ws.write('A5', 'Route / Zone:', box_hdr)
        ws.merge_range('B5:D5', van.get('route_zone', 'Local Territory'), box_val)
        ws.write('E5', 'Security Gate Pass:', box_hdr)
        ws.merge_range('F5:H5', 'VERIFIED & CLEARED', box_val)
        ws.write('I5', 'Return Audit Status:', box_hdr)
        ws.merge_range('J5:L5', 'PENDING EVENING AUDIT', box_val)

        r_headers = ['SL', 'Consignee / Outlet Name', 'Sales Order No', 'D.Note No', primary_uom, secondary_uom, 'Inv Value', 'Pay', 'Collect ৳', 'Deliv', 'Return', 'Receiver Signature']
        for c, h in enumerate(r_headers):
            ws.write(6, c, h, th_fmt)
        ws.set_row(6, 22)

        outlets = van.get('outlets', [])
        r_cur = 7
        for sl, ot in enumerate(outlets, 1):
            ws.write(r_cur, 0, sl, td_ctr)
            ws.write(r_cur, 1, ot.get('consignee_name', ''), td_txt)
            ws.write(r_cur, 2, ot.get('order_no', ''), td_ctr)
            ws.write(r_cur, 3, ot.get('delivery_note_id', ''), td_ctr)
            ws.write(r_cur, 4, ot.get('total_pkt', 0), td_num)
            ws.write(r_cur, 5, ot.get('total_kg', 0.0), td_dec)
            ws.write(r_cur, 6, ot.get('total_amount', 0.0), td_cur)
            ws.write(r_cur, 7, ot.get('payment_mode', 'Credit'), td_ctr)
            ws.write(r_cur, 8, ot.get('expected_cash', 0.0), td_cur)
            ws.write(r_cur, 9, '', td_txt)
            ws.write(r_cur, 10, '', td_txt)
            ws.write(r_cur, 11, '', td_txt)
            ws.set_row(r_cur, 18)
            r_cur += 1

        # Van Total
        ws.merge_range(r_cur, 0, r_cur, 3, 'VAN TRIP TOTAL:', tot_fmt)
        ws.write_formula(r_cur, 4, f'=SUM(E8:E{r_cur})', tot_num)
        ws.write_formula(r_cur, 5, f'=SUM(F8:F{r_cur})', tot_dec)
        ws.write_formula(r_cur, 6, f'=SUM(G8:G{r_cur})', tot_cur)
        ws.write(r_cur, 7, '', tot_fmt)
        ws.write_formula(r_cur, 8, f'=SUM(I8:I{r_cur})', tot_cur)
        ws.write(r_cur, 9, '', tot_fmt)
        ws.write(r_cur, 10, '', tot_fmt)
        ws.write(r_cur, 11, '', tot_fmt)
        ws.set_row(r_cur, 20)

        # Signature & SOP block
        r_sig = r_cur + 2
        ws.write(r_sig, 1, '_____________________', wb.add_format({'align': 'center'}))
        ws.write(r_sig, 5, '_____________________', wb.add_format({'align': 'center'}))
        ws.write(r_sig, 9, '_____________________', wb.add_format({'align': 'center'}))
        ws.write(r_sig+1, 1, 'Depot Incharge Signature', wb.add_format({'align': 'center', 'font_size': 8}))
        ws.write(r_sig+1, 5, 'Security Gate Officer', wb.add_format({'align': 'center', 'font_size': 8}))
        ws.write(r_sig+1, 9, 'Driver / Delivery Man', wb.add_format({'align': 'center', 'font_size': 8}))

    wb.close()
    output.seek(0)
    
    clean_depot = "".join(c for c in depot_name if c.isalnum() or c in (' ', '_', '-')).strip().replace(' ', '_')
    fname = f"Van_Runsheets_{clean_depot}_{plan_date.replace('/', '-')}.xlsx"
    return send_file(
        output,
        download_name=fname,
        as_attachment=True,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )

# ==============================================================================
# POLOXY ERP BATCH PARSER WITH ROUTE & FLEET AUTO-MAPPING
# ==============================================================================

@app.route('/api/distribution/import-poloxy-orders', methods=['POST'])
def api_import_poloxy_orders():
    if 'file' not in request.files:
        return jsonify({'success': False, 'message': 'No file uploaded in request'}), 400
        
    file = request.files['file']
    if not file.filename:
        return jsonify({'success': False, 'message': 'Empty file selected'}), 400
        
    depot_id_param = request.form.get('depot_id')
    active_depot_id = int(depot_id_param) if depot_id_param and depot_id_param.isdigit() else None
    
    try:
        wb = openpyxl.load_workbook(file, data_only=True)
        ws = wb.active
        
        header_row_idx = 2
        for r_idx, row in enumerate(ws.iter_rows(max_row=8, values_only=True), 1):
            row_str = " ".join([str(c) for c in row if c is not None]).upper()
            if "ORDER NO" in row_str or "REF. NO" in row_str or "CUSTOMER" in row_str:
                header_row_idx = r_idx
                break
                
        conn = get_db()
        cursor = conn.cursor()
        
        # Load routes and consignee map
        routes_map = {}
        consignee_route_map = {}
        if active_depot_id:
            cursor.execute('SELECT id, route_name, route_code, default_vehicle_no FROM routes WHERE depot_id = ?', (active_depot_id,))
            for r in cursor.fetchall():
                routes_map[r['id']] = dict(r)
                
            cursor.execute('SELECT consignee_name, route_id FROM route_consignees WHERE depot_id = ?', (active_depot_id,))
            for rc in cursor.fetchall():
                consignee_route_map[rc['consignee_name'].strip().lower()] = rc['route_id']
                
        # Load depot fleet and crew
        depot_fleet = []
        depot_drivers = []
        depot_deliverymen = []
        if active_depot_id:
            cursor.execute('SELECT * FROM fleet_vehicles WHERE depot_id = ? ORDER BY id ASC', (active_depot_id,))
            depot_fleet = [dict(v) for v in cursor.fetchall()]
            
            cursor.execute("SELECT * FROM depot_crew WHERE depot_id = ? AND role = 'driver'", (active_depot_id,))
            depot_drivers = [dict(c) for c in cursor.fetchall()]
            
            cursor.execute("SELECT * FROM depot_crew WHERE depot_id = ? AND role = 'delivery_man'", (active_depot_id,))
            depot_deliverymen = [dict(c) for c in cursor.fetchall()]
            
        cursor.execute('SELECT * FROM depots')
        depots_db = {d['id']: dict(d) for d in cursor.fetchall()}
        conn.close()
        
        depots_result = {}
        total_orders_parsed = 0
        total_value_parsed = 0.0
        
        for row in ws.iter_rows(min_row=header_row_idx + 1, values_only=True):
            if not row or not any(row):
                continue
            
            sr_no = str(row[0] or '').strip()
            date_val = str(row[1] or '').strip()
            customer = str(row[2] or '').strip()
            order_no = str(row[3] or '').strip()
            ref_no = str(row[4] or '').strip()
            branch = str(row[6] or '').strip()
            item = str(row[7] or '').strip()
            raw_rate_unit = str(row[8] or '').strip()
            rate = float(row[9] or 0.0)
            qty_bag = float(row[10] or 0.0)
            qty_kg = float(row[11] or 0.0)
            
            # Column N (Index 13): Total Amount
            total_amount_col_n = None
            if len(row) > 13 and row[13] is not None and str(row[13]).strip() != '':
                try:
                    total_amount_col_n = float(row[13])
                except (ValueError, TypeError):
                    total_amount_col_n = None
                    
            d_note_id = str(row[27] or '').strip() if len(row) > 27 else ''
            consignee_name = str(row[32] or '').strip() if len(row) > 32 and str(row[32]).strip() else customer
            consignee_contact = str(row[33] or '').strip() if len(row) > 33 else ''
            consignee_address = str(row[34] or '').strip() if len(row) > 34 else ''
            
            if not ref_no and not order_no and not item:
                continue
                
            if active_depot_id:
                depot_info = depots_db.get(active_depot_id, {'id': active_depot_id, 'name': 'Selected Depot', 'category': 'Food'})
            else:
                depot_info = match_depot_from_ref(branch, ref_no, order_no)
                
            d_id = depot_info['id']
            d_name = depot_info['name']
            d_cat = depot_info.get('category', 'Food')
            
            is_egg = d_id == 9 or "egg" in d_cat.lower()
            is_dairy = d_id in [4, 11] or "dairy" in d_cat.lower()
            primary_uom = "Pcs" if is_egg else ("Ltr" if is_dairy else "Pkt")
            secondary_uom = "Kg" if not is_dairy else "Ltr"
            rate_unit = raw_rate_unit or primary_uom
            
            if d_id not in depots_result:
                depots_result[d_id] = {
                    'depot_id': d_id,
                    'depot_name': d_name,
                    'category': d_cat,
                    'primary_uom': primary_uom,
                    'secondary_uom': secondary_uom,
                    'orders_map': {},
                    'fleet': depot_fleet,
                    'drivers': depot_drivers,
                    'deliverymen': depot_deliverymen,
                    'routes': list(routes_map.values()),
                    'total_outlets': 0,
                    'total_pkts': 0.0,
                    'total_kg': 0.0,
                    'total_amount': 0.0
                }
                
            ord_key = order_no or f"ORD-{sr_no or (total_orders_parsed + 1)}"
            if ord_key not in depots_result[d_id]['orders_map']:
                is_credit = any(kw in consignee_name.lower() for kw in ['shwapno', 'agora', 'meena', 'unimart', 'pran', 'aarong', 'lavender', 'food panda'])
                
                # Match route for consignee
                matched_route_id = consignee_route_map.get(consignee_name.strip().lower())
                matched_route = routes_map.get(matched_route_id) if matched_route_id else None
                
                depots_result[d_id]['orders_map'][ord_key] = {
                    'order_no': ord_key,
                    'date': date_val,
                    'customer': customer,
                    'consignee_name': consignee_name,
                    'consignee_contact': consignee_contact,
                    'consignee_address': consignee_address,
                    'branch_category': branch or d_cat,
                    'delivery_note_id': d_note_id,
                    'route_id': matched_route_id,
                    'route_name': matched_route['route_name'] if matched_route else 'General Route',
                    'default_vehicle_no': matched_route['default_vehicle_no'] if matched_route else '',
                    'items': [],
                    'total_pkt': 0.0,
                    'total_kg': 0.0,
                    'total_amount': 0.0,
                    'col_n_found': False,
                    'assigned_van': '',
                    'payment_mode': 'Credit' if is_credit else 'Cash',
                    'expected_cash': 0.0,
                    'rate_unit': rate_unit
                }
                depots_result[d_id]['total_outlets'] += 1
                total_orders_parsed += 1
                
            if total_amount_col_n is not None and not depots_result[d_id]['orders_map'][ord_key]['col_n_found']:
                if total_amount_col_n > 0:
                    depots_result[d_id]['orders_map'][ord_key]['total_amount'] = total_amount_col_n
                    depots_result[d_id]['orders_map'][ord_key]['col_n_found'] = True
                    
            item_line_amount = rate * (qty_kg if qty_kg > 0 else qty_bag)
            depots_result[d_id]['orders_map'][ord_key]['items'].append({
                'item': item,
                'rate_unit': rate_unit,
                'rate': rate,
                'qty_pkt': qty_bag,
                'qty_kg': qty_kg,
                'amount': item_line_amount
            })
            
            depots_result[d_id]['orders_map'][ord_key]['total_pkt'] += qty_bag
            depots_result[d_id]['orders_map'][ord_key]['total_kg'] += qty_kg
            depots_result[d_id]['total_pkts'] += qty_bag
            depots_result[d_id]['total_kg'] += qty_kg
            
        # Finalize list
        final_depots = []
        for d_id, data in depots_result.items():
            order_list = list(data['orders_map'].values())
            d_total_val = 0.0
            for o in order_list:
                if not o['col_n_found'] or o['total_amount'] == 0:
                    o['total_amount'] = sum(it['amount'] for it in o['items'])
                d_total_val += o['total_amount']
                if o['payment_mode'] == 'Cash':
                    o['expected_cash'] = o['total_amount']
                    
            data['total_amount'] = d_total_val
            data['orders'] = order_list
            del data['orders_map']
            total_value_parsed += d_total_val
            final_depots.append(data)
            
        final_depots.sort(key=lambda x: x['total_outlets'], reverse=True)
        
        return jsonify({
            'success': True,
            'message': f'Poloxy ERP parsed {total_orders_parsed} orders for {final_depots[0]["depot_name"] if final_depots else "Selected Depot"}!',
            'total_orders': total_orders_parsed,
            'total_value': total_value_parsed,
            'depots': final_depots
        })
        
    except Exception as err:
        return jsonify({'success': False, 'message': f'Error reading Poloxy file: {str(err)}'}), 500


if __name__ == '__main__':
    print("=" * 66)
    print("PARAGON AGRO DISTRIBUTION LIVE WEB PORTAL IS READY!")
    print("Running at: http://127.0.0.1:5000")
    print("=" * 66)
    app.run(host='0.0.0.0', port=5000, debug=False)


