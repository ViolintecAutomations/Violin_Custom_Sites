import os
from flask import Blueprint, render_template, redirect, url_for, request, flash, send_file, make_response, current_app
from flask_login import login_required, login_user, logout_user, current_user
from . import mysql, User
import hashlib
from .forms import LoginForm, AddUserForm, VendorForm, AddMenuForm
import csv
import io
import pandas as pd
from MySQLdb import IntegrityError
from datetime import date, timedelta, datetime
from flask_wtf.csrf import generate_csrf
from werkzeug.utils import secure_filename

admin_bp = Blueprint('admin', __name__, url_prefix='/admin')

# These will be initialized after the app context is available
UPLOAD_FOLDER = None
ALLOWED_EXTENSIONS = None

def init_admin_config(app):
    global UPLOAD_FOLDER, ALLOWED_EXTENSIONS
    UPLOAD_FOLDER = app.config['UPLOAD_FOLDER']
    ALLOWED_EXTENSIONS = app.config['ALLOWED_EXTENSIONS']

def allowed_file(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

@admin_bp.route('/login', methods=['GET', 'POST'])
def login():
    form = LoginForm()
    cur = mysql.connection.cursor()
    cur.execute("SELECT message_text FROM special_messages WHERE is_active = TRUE AND DATE(created_at) = CURDATE() ORDER BY created_at DESC LIMIT 1")
    special_message = cur.fetchone()
    if form.validate_on_submit():
        employee_id = form.employee_id.data
        password = form.password.data
        cur.execute("SELECT * FROM employees WHERE employee_id=%s AND role_id IN (5,6) AND is_active=1", (employee_id,))
        user = cur.fetchone()
        if user:
            import hashlib
            password_hash = hashlib.sha256(password.encode()).hexdigest()
            if user['password_hash'] == password_hash or user['password_hash'] == password:
                # Set role based on role_id
                role = 'Admin' if user['role_id'] == 6 else 'Accounts'
                user_obj = User(user['id'], name=user['name'], email=user['email'], role=role)
                login_user(user_obj)
                flash('Login successful!', 'success')
                return redirect(url_for('admin.dashboard'))
            else:
                flash('Invalid password.', 'danger')
        else:
            flash('Invalid employee ID or not an admin/accounts.', 'danger')
    return render_template('admin/login.html', form=form, special_message=special_message)

@admin_bp.route('/logout')
def logout():
    logout_user()
    flash('Logged out successfully.', 'info')
    return redirect(url_for('index'))

@admin_bp.route('/dashboard')
@login_required
def dashboard():
    cur = mysql.connection.cursor()
    today = date.today()
    first_day = today.replace(day=1)
    if today.month == 12:
        next_month = today.replace(year=today.year+1, month=1, day=1)
    else:
        next_month = today.replace(month=today.month+1, day=1)
    last_day = next_month

    # Base query for bookings
    # Base query parts
    base_query_template = "FROM bookings WHERE booking_date >= %s AND booking_date < %s"
    base_query_shift_template = "FROM bookings WHERE status='Booked' AND booking_date >= %s AND booking_date < %s"
    base_query_trends_template = "FROM bookings WHERE booking_date >= CURDATE() - INTERVAL 6 DAY"

    # Parameters for each query
    params_total = [first_day, last_day]
    params_shift = [first_day, last_day]
    params_trends = []

    # Apply unit-wise filter if the user is a unit admin
    location_filter_clause = ""
    if current_user.role == 'Admin' and current_user.employee_id == 'a001':
        # For admin user 'a001', show all unit data
        pass
    elif current_user.role == 'Admin' and current_user.location:
        cur.execute("SELECT id FROM locations WHERE name = %s", (current_user.location,))
        location_id_row = cur.fetchone()
        if location_id_row:
            location_id = location_id_row['id']
            location_filter_clause = " AND location_id = %s"
            params_total.append(location_id)
            params_shift.append(location_id)
            params_trends.append(location_id)

    # Construct final queries
    total_bookings_query = "SELECT COUNT(*) AS total " + base_query_template + location_filter_clause
    consumed_query = "SELECT COUNT(*) AS consumed " + base_query_template + " AND status='Consumed'" + location_filter_clause
    booked_query = "SELECT COUNT(*) AS booked " + base_query_template + " AND status='Booked'" + location_filter_clause
    
    final_shift_query = "SELECT shift, COUNT(*) as count " + base_query_shift_template + location_filter_clause + " GROUP BY shift"
    final_trends_query = "SELECT booking_date, COUNT(*) as count " + base_query_trends_template + location_filter_clause + " GROUP BY booking_date ORDER BY booking_date"

    # Total bookings for current month
    cur.execute(total_bookings_query, tuple(params_total))
    total_bookings = cur.fetchone()['total']

    # Consumed meals for current month
    cur.execute(consumed_query, tuple(params_total))
    consumed_meals = cur.fetchone()['consumed']

    # Booked meals (not yet consumed) for current month
    cur.execute(booked_query, tuple(params_total))
    booked_meals = cur.fetchone()['booked']

    # Booked meals (not yet consumed) - separate by shift for current month
    cur.execute(final_shift_query, tuple(params_shift))
    booked_by_shift = {row['shift']: row['count'] for row in cur.fetchall()}
    booked_breakfast = booked_by_shift.get('Breakfast', 0)
    booked_lunch = booked_by_shift.get('Lunch', 0)
    booked_dinner = booked_by_shift.get('Dinner', 0)

    # Trends (last 7 days bookings)
    cur.execute(final_trends_query, tuple(params_trends))
    trends = cur.fetchall()

    month_label = today.strftime('%B %Y')
    return render_template('admin/dashboard.html',
        total_bookings=total_bookings,
        consumed_meals=consumed_meals,
        booked_meals=booked_meals,
        booked_breakfast=booked_breakfast,
        booked_lunch=booked_lunch,
        booked_dinner=booked_dinner,
        trends=trends,
        month_label=month_label,
        csrf_token=generate_csrf()
    )

@admin_bp.route('/monthly_all_units_report')
@login_required
def monthly_all_units_report():
    if current_user.employee_id != 'a001':
        flash('Access denied: Only the Master Admin can view this report.', 'danger')
        return redirect(url_for('admin.dashboard'))

    cur = mysql.connection.cursor()
    
    # Get filter parameters
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')

    # If no dates provided, default to current month
    if not start_date and not end_date:
        today = date.today()
        start_date = today.replace(day=1).strftime('%Y-%m-%d')
        if today.month == 12:
            end_date = today.replace(year=today.year + 1, month=1, day=1) - timedelta(days=1)
        else:
            end_date = today.replace(month=today.month + 1, day=1) - timedelta(days=1)
        end_date = end_date.strftime('%Y-%m-%d')

    query = '''
        SELECT
            l.name as location,
            COUNT(b.id) as total_bookings,
            SUM(CASE WHEN b.status = 'Consumed' THEN 1 ELSE 0 END) as consumed_meals,
            SUM(CASE WHEN b.status = 'Booked' THEN 1 ELSE 0 END) as booked_meals
        FROM locations l
        LEFT JOIN employees e ON e.location_id = l.id
        LEFT JOIN bookings b ON b.employee_id = e.id
    '''
    params = []
    where_conditions = []

    if start_date:
        where_conditions.append('b.booking_date >= %s')
        params.append(start_date)
    
    if end_date:
        where_conditions.append('b.booking_date <= %s')
        params.append(end_date)
    
    if where_conditions:
        query += ' WHERE ' + ' AND '.join(where_conditions)
    
    query += '''
        GROUP BY l.id, l.name
        ORDER BY l.name
    '''
    
    cur.execute(query, tuple(params))
    monthly_reports = cur.fetchall()

    return render_template('admin/monthly_all_units_report.html',
                           monthly_reports=monthly_reports,
                           start_date=start_date,
                           end_date=end_date)

@admin_bp.route('/monthly_unit_report')
@login_required
def monthly_unit_report():
    if current_user.employee_id == 'a001' or not current_user.location:
        flash('Access denied: This report is for unit-specific admins only.', 'danger')
        return redirect(url_for('admin.dashboard'))

    cur = mysql.connection.cursor()
    
    # Get filter parameters
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')

    # If no dates provided, default to current month
    if not start_date and not end_date:
        today = date.today()
        start_date = today.replace(day=1).strftime('%Y-%m-%d')
        if today.month == 12:
            end_date = today.replace(year=today.year + 1, month=1, day=1) - timedelta(days=1)
        else:
            end_date = today.replace(month=today.month + 1, day=1) - timedelta(days=1)
        end_date = end_date.strftime('%Y-%m-%d')

    query = '''
        SELECT
            l.name as location,
            COUNT(b.id) as total_bookings,
            SUM(CASE WHEN b.status = 'Consumed' THEN 1 ELSE 0 END) as consumed_meals,
            SUM(CASE WHEN b.status = 'Booked' THEN 1 ELSE 0 END) as booked_meals
        FROM locations l
        LEFT JOIN employees e ON e.location_id = l.id
        LEFT JOIN bookings b ON b.employee_id = e.id
        WHERE l.name = %s
    '''
    params = [current_user.location]
    where_conditions = []

    if start_date:
        where_conditions.append('b.booking_date >= %s')
        params.append(start_date)
    
    if end_date:
        where_conditions.append('b.booking_date <= %s')
        params.append(end_date)
    
    if where_conditions:
        query += ' AND ' + ' AND '.join(where_conditions)
    
    query += '''
        GROUP BY l.id, l.name
        ORDER BY l.name
    '''
    
    cur.execute(query, tuple(params))
    monthly_reports = cur.fetchall()

    return render_template('admin/monthly_unit_report.html',
                           monthly_reports=monthly_reports,
                           start_date=start_date,
                           end_date=end_date,
                           unit_name=current_user.location)

@admin_bp.route('/daily_unit_report')
@login_required
def daily_unit_report():
    cur = mysql.connection.cursor()
    
    # Get filter parameter
    report_date_str = request.args.get('report_date')

    # If no date provided, default to today
    if not report_date_str:
        report_date = date.today()
    else:
        try:
            report_date = datetime.strptime(report_date_str, '%Y-%m-%d').date()
        except ValueError:
            flash('Invalid date format. Please use YYYY-MM-DD.', 'danger')
            return redirect(url_for('admin.daily_unit_report'))

    query = '''
        SELECT
            l.name as location,
            COUNT(b.id) as total_bookings,
            SUM(CASE WHEN b.status = 'Consumed' THEN 1 ELSE 0 END) as consumed_meals,
            SUM(CASE WHEN b.status = 'Booked' THEN 1 ELSE 0 END) as booked_meals
        FROM locations l
        LEFT JOIN employees e ON e.location_id = l.id
        LEFT JOIN bookings b ON b.employee_id = e.id
    '''
    
    params = []
    where_conditions = ['b.booking_date = %s']
    params.append(report_date)

    if current_user.role == 'Admin' and current_user.employee_id != 'a001' and current_user.location:
        where_conditions.append('l.name = %s')
        params.append(current_user.location)

    if where_conditions:
        query += ' WHERE ' + ' AND '.join(where_conditions)

    query += '''
        GROUP BY l.id, l.name
        ORDER BY l.name
    '''
    
    cur.execute(query, tuple(params))
    daily_reports = cur.fetchall()

    unit_name = current_user.location if current_user.location else "All Units"

    return render_template('admin/daily_unit_report.html',
                           daily_reports=daily_reports,
                           report_date=report_date.strftime('%Y-%m-%d'),
                           unit_name=unit_name)

@admin_bp.route('/api/booked_meals_by_shift')
@login_required
def api_booked_meals_by_shift():
    cur = mysql.connection.cursor()
    query = """
        SELECT shift, COUNT(*) as count
        FROM bookings
        WHERE status='Booked'
    """
    params = []
    if current_user.role == 'Admin' and current_user.employee_id == 'a001':
        # For admin user 'a001', show all unit data
        pass
    elif current_user.role == 'Admin' and current_user.location:
        cur.execute("SELECT id FROM locations WHERE name = %s", (current_user.location,))
        location_id = cur.fetchone()
        if location_id:
            params.append(location_id['id'])
            query += " AND location_id = %s"
    query += " GROUP BY shift"
    cur.execute(query, tuple(params))
    booked_by_shift = {row['shift']: row['count'] for row in cur.fetchall()}
    return {
        'Breakfast': booked_by_shift.get('Breakfast', 0),
        'Lunch': booked_by_shift.get('Lunch', 0),
        'Dinner': booked_by_shift.get('Dinner', 0)
    }

@admin_bp.route('/employee_reports')
@login_required
def employee_reports():
    cur = mysql.connection.cursor()
    
    # Get filter parameters
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')
    
    # Build the query with optional date filters
    query = '''
        SELECT e.name as employee, d.name as department, l.name as location, e.id as employee_id,
               COUNT(b.id) as meals_booked,
               SUM(CASE WHEN b.status = 'Consumed' THEN 1 ELSE 0 END) as meals_consumed
        FROM employees e
        LEFT JOIN departments d ON e.department_id = d.id
        LEFT JOIN locations l ON e.location_id = l.id
        LEFT JOIN bookings b ON e.id = b.employee_id
    '''
    
    params = []
    where_conditions = []

    if current_user.role == 'Admin' and current_user.employee_id == 'a001':
        # For admin user 'a001', show all unit data
        pass
    elif current_user.role == 'Admin' and current_user.location:
        cur.execute("SELECT id FROM locations WHERE name = %s", (current_user.location,))
        location_id = cur.fetchone()
        if location_id:
            where_conditions.append('e.location_id = %s')
            params.append(location_id['id'])
        else:
            # If location not found, return empty results
            return render_template('admin/employee_reports.html',
                                 employees=[],
                                 start_date=start_date,
                                 end_date=end_date)
    
    if start_date:
        where_conditions.append('b.booking_date >= %s')
        params.append(start_date)
    
    if end_date:
        where_conditions.append('b.booking_date <= %s')
        params.append(end_date)
    
    if where_conditions:
        query += ' WHERE ' + ' AND '.join(where_conditions)
    
    query += '''
        GROUP BY e.id, e.name, d.name, l.name
        ORDER BY e.name
    '''
    
    cur.execute(query, tuple(params))
    employees = cur.fetchall()
    
    return render_template('admin/employee_reports.html', 
                         employees=employees, 
                         start_date=start_date, 
                         end_date=end_date)

@admin_bp.route('/dept_location_reports')
@login_required
def dept_location_reports():
    cur = mysql.connection.cursor()
    
    # Get filter parameters
    department_filter = request.args.get('department')
    location_filter = request.args.get('location')
    
    # Build the query to get department/location reports
    query = '''
        SELECT 
            d.name as department,
            l.name as location,
            COUNT(b.id) as meals_booked,
            SUM(CASE WHEN b.status = 'Consumed' THEN 1 ELSE 0 END) as meals_consumed
        FROM departments d
        CROSS JOIN locations l
        LEFT JOIN employees e ON e.department_id = d.id AND e.location_id = l.id
        LEFT JOIN bookings b ON e.id = b.employee_id


    '''
    
    params = []
    where_conditions = []

    if current_user.role == 'Admin' and current_user.employee_id == 'a001':
        # For admin user 'a001', show all unit data
        pass
    elif current_user.role == 'Admin' and current_user.location:
        cur.execute("SELECT id FROM locations WHERE name = %s", (current_user.location,))
        location_id = cur.fetchone()
        if location_id:
            where_conditions.append('l.id = %s')
            params.append(location_id['id'])
        else:
            # If location not found, return empty results
            return render_template('admin/dept_location_reports.html',
                                 reports=[],
                                 departments=[],
                                 locations=[],
                                 selected_department=department_filter,
                                 selected_location=location_filter)
    
    if department_filter:
        where_conditions.append('d.name = %s')
        params.append(department_filter)
    
    if location_filter:
        where_conditions.append('l.name = %s')
        params.append(location_filter)
    
    if where_conditions:
        query += ' WHERE ' + ' AND '.join(where_conditions)
    
    query += '''
        GROUP BY d.id, l.id, d.name, l.name
        ORDER BY d.name, l.name
    '''
    
    cur.execute(query, tuple(params))
    reports = cur.fetchall()
    
    # Get departments and locations for filter dropdowns
    cur.execute('SELECT name FROM departments ORDER BY name')
    departments = [row['name'] for row in cur.fetchall()]
    
    cur.execute('SELECT name FROM locations ORDER BY name')
    locations = [row['name'] for row in cur.fetchall()]
    
    return render_template('admin/dept_location_reports.html', 
                         reports=reports,
                         departments=departments,
                         locations=locations,
                         selected_department=department_filter,
                         selected_location=location_filter)

@admin_bp.route('/cost_subsidy')
@login_required
def cost_subsidy():
    cur = mysql.connection.cursor()
    cur.execute('SELECT d.name FROM employees e JOIN departments d ON e.department_id = d.id WHERE e.id = %s', (current_user.id,))
    dept = cur.fetchone()
    # Allow 'a001' to access Cost & Subsidy regardless of department
    if not (current_user.employee_id == 'a001' or (dept and dept['name'].lower() == 'finance')):
        flash('Access denied: Only Finance department or Master Admin can access Cost & Subsidy Analysis.', 'danger')
        return redirect(url_for('admin.dashboard'))

    employee_filter = request.args.get('employee', '').strip()
    department_filter = request.args.get('department', '').strip()
    location_filter = request.args.get('unit', '').strip()  # 'unit' in form, but use locations
    
    # Get all departments and locations for dropdowns
    cur.execute('SELECT name FROM departments ORDER BY name')
    departments = cur.fetchall()
    
    cur.execute('SELECT name FROM locations ORDER BY name')
    locations = cur.fetchall()
    
    query = '''
        SELECT e.id, e.name AS employee, d.name AS department, l.name AS location,
               COUNT(b.id) AS meals_booked
        FROM employees e
        LEFT JOIN departments d ON e.department_id = d.id
        LEFT JOIN locations l ON e.location_id = l.id
        LEFT JOIN bookings b ON e.id = b.employee_id
    '''
    params = []
    where_clauses = []
    if employee_filter:
        where_clauses.append('e.name LIKE %s')
        params.append(f"%{employee_filter}%")
    if department_filter:
        where_clauses.append('d.name = %s')
        params.append(department_filter)
    if location_filter:
        where_clauses.append('l.name = %s')
        params.append(location_filter)
    if where_clauses:
        query += ' WHERE ' + ' AND '.join(where_clauses)
    query += ' GROUP BY e.id, e.name, d.name, l.name ORDER BY e.name'
    cur.execute(query, params)
    rows = cur.fetchall()
    meal_price = 20
    cost_subsidy_data = []
    for row in rows:
        meals_booked = row['meals_booked'] or 0
        total_cost = meals_booked * meal_price
        cost_subsidy_data.append({
            'employee': row['employee'],
            'department': row['department'] or 'N/A',
            'unit': row['location'] or 'N/A',
            'meals_booked': meals_booked,
            'total_cost': total_cost
        })
    return render_template('admin/cost_subsidy.html', cost_subsidy_data=cost_subsidy_data, departments=departments, units=locations)

@admin_bp.route('/export_cost_subsidy')
@login_required
def export_cost_subsidy():
    cur = mysql.connection.cursor()
    cur.execute('SELECT d.name FROM employees e JOIN departments d ON e.department_id = d.id WHERE e.id = %s', (current_user.id,))
    dept = cur.fetchone()
    # Allow 'a001' to export Cost & Subsidy regardless of department
    if not (current_user.employee_id == 'a001' or (dept and dept['name'].lower() == 'finance')):
        flash('Access denied: Only Finance department or Master Admin can access Cost & Subsidy Analysis.', 'danger')
        return redirect(url_for('admin.dashboard'))

    employee_filter = request.args.get('employee', '').strip()
    department_filter = request.args.get('department', '').strip()
    location_filter = request.args.get('unit', '').strip()
    
    query = '''
        SELECT e.id, e.name AS employee, d.name AS department, l.name AS location, COUNT(b.id) AS total_meals
        FROM employees e
        LEFT JOIN departments d ON e.department_id = d.id
        LEFT JOIN locations l ON e.location_id = l.id
        LEFT JOIN bookings b ON e.id = b.employee_id AND b.status = 'Consumed'
    '''
    params = []
    where_clauses = []
    if employee_filter:
        where_clauses.append('e.name LIKE %s')
        params.append(f"%{employee_filter}%")
    if department_filter:
        where_clauses.append('d.name = %s')
        params.append(department_filter)
    if location_filter:
        where_clauses.append('l.name = %s')
        params.append(location_filter)
    if where_clauses:
        query += ' WHERE ' + ' AND '.join(where_clauses)
    query += ' GROUP BY e.id, e.name, d.name, l.name ORDER BY e.name'
    cur.execute(query, params)
    rows = cur.fetchall()
    meal_price = 20
    import csv, io
    si = io.StringIO()
    writer = csv.writer(si)
    writer.writerow(['Employee', 'Department', 'Unit', 'Total Cost', 'Company Subsidy', 'Employee Contribution'])
    for row in rows:
        total_meals = row['total_meals'] or 0
        total_cost = total_meals * meal_price
        company_subsidy = 0
        employee_contribution = total_cost
        writer.writerow([
            row['employee'],
            row['department'] or 'N/A',
            row['location'] or 'N/A',
            total_cost,
            company_subsidy,
            employee_contribution
        ])
    output = si.getvalue()
    from flask import make_response
    response = make_response(output)
    response.headers['Content-Disposition'] = 'attachment; filename=cost_subsidy.csv'
    response.headers['Content-type'] = 'text/csv'
    return response



@admin_bp.route('/vendor_report_unit_wise')
@login_required
def vendor_report_unit_wise():
    cur = mysql.connection.cursor()
    
    query = "SELECT name as vendor_name, unit, purpose, count FROM vendors"
    params = []
    where_conditions = []

    if current_user.role == 'Admin' and current_user.employee_id == 'a001':
        # For admin user 'a001', show all unit data
        pass
    elif current_user.role == 'Admin' and current_user.location:
        where_conditions.append('unit = %s')
        params.append(current_user.location)
    
    if where_conditions:
        query += ' WHERE ' + ' AND '.join(where_conditions)
    
    query += " ORDER BY name"
    
    cur.execute(query, tuple(params))
    vendor_reports = cur.fetchall()
    
    cur.execute('SELECT name FROM locations ORDER BY name')
    units = [row['name'] for row in cur.fetchall()]
    cur.execute('SELECT DISTINCT purpose FROM vendors WHERE purpose IS NOT NULL AND purpose != "" ORDER BY purpose')
    purposes = [row['purpose'] for row in cur.fetchall()]
    form = VendorForm()
    return render_template('admin/vendor_report_unit_wise.html',
                         vendor_reports=vendor_reports,
                         units=units,
                         purposes=purposes,
                         selected_unit=None,
                         selected_purpose=None,
                         form=form)

@admin_bp.route('/vendor_report')
@login_required
def vendor_report():
    cur = mysql.connection.cursor()
    
    purpose_filter = request.args.get('purpose')
    unit_filter = request.args.get('unit')

    query = "SELECT name as vendor_name, unit, food_licence_path, agreement_date FROM vendors"
    params = []
    where_conditions = []

    if current_user.role == 'Admin' and current_user.employee_id == 'a001':
        # For master admin 'a001', show all unit data
        pass
    elif current_user.role == 'Admin' and current_user.location:
        where_conditions.append('unit = %s')
        params.append(current_user.location)
        # If a unit admin, and a unit filter is provided, ensure it matches their unit
        if unit_filter and unit_filter != current_user.location:
            flash('Access denied: You can only view reports for your assigned unit.', 'danger')
            return redirect(url_for('admin.dashboard'))
        unit_filter = current_user.location # Ensure the filter is set to their unit

    if purpose_filter:
        where_conditions.append('purpose = %s')
        params.append(purpose_filter)
    
    if unit_filter and not (current_user.role == 'Admin' and current_user.location and unit_filter == current_user.location):
        # Apply unit filter only if not already applied by unit admin logic
        where_conditions.append('unit = %s')
        params.append(unit_filter)

    if where_conditions:
        query += ' WHERE ' + ' AND '.join(where_conditions)
    
    query += " ORDER BY name"
    
    cur.execute(query, tuple(params))
    vendor_reports_raw = cur.fetchall()
    
    vendor_reports = []
    for report in vendor_reports_raw:
        agreement_date = report['agreement_date']
        remaining_days = None
        if agreement_date:
            remaining_days = (agreement_date + timedelta(days=30) - date.today()).days
        
        vendor_reports.append({
            'vendor_name': report['vendor_name'],
            'unit': report['unit'],
            'food_licence_path': report['food_licence_path'],
            'agreement_date': agreement_date,
            'remaining_days': remaining_days
        })

    cur.execute('SELECT name FROM locations ORDER BY name')
    units = [row['name'] for row in cur.fetchall()]
    cur.execute('SELECT DISTINCT purpose FROM vendors WHERE purpose IS NOT NULL AND purpose != "" ORDER BY purpose')
    purposes = [row['purpose'] for row in cur.fetchall()]

    return render_template('admin/vendor_report.html',
                         vendor_reports=vendor_reports,
                         units=units,
                         purposes=purposes,
                         selected_unit=unit_filter,
                         selected_purpose=purpose_filter,
                         csrf_token=generate_csrf())

@admin_bp.route('/update_vendor_details', methods=['POST'])
@login_required
def update_vendor_details():
    if current_user.role != 'Admin':
        flash('Access denied.', 'danger')
        return redirect(url_for('admin.dashboard'))

    vendor_name = request.form.get('vendor_name')
    agreement_date_str = request.form.get('agreement_date')
    
    cur = mysql.connection.cursor()
    
    # Update agreement date
    if agreement_date_str:
        try:
            agreement_date = datetime.strptime(agreement_date_str, '%Y-%m-%d').date()
            cur.execute("UPDATE vendors SET agreement_date = %s WHERE name = %s", (agreement_date, vendor_name))
            mysql.connection.commit()
            flash('Agreement date updated successfully.', 'success')
        except ValueError:
            flash('Invalid date format. Please use YYYY-MM-DD.', 'danger')
        except Exception as e:
            flash(f'Error updating agreement date: {e}', 'danger')

    # Handle file upload
    if 'food_licence' in request.files:
        file = request.files['food_licence']
        if file and file.filename and allowed_file(file.filename):
            filename = secure_filename(file.filename)
            # Store path relative to the static folder
            relative_file_path = os.path.join('uploads/food_licences', filename).replace('\\', '/')
            full_file_path = os.path.join(UPLOAD_FOLDER, filename)
            
            # Ensure the directory exists
            os.makedirs(UPLOAD_FOLDER, exist_ok=True)
            
            file.save(full_file_path)
            
            # Update database with file path
            cur.execute("UPDATE vendors SET food_licence_path = %s WHERE name = %s", (relative_file_path, vendor_name))
            mysql.connection.commit()
            flash('Food licence uploaded successfully.', 'success')
        elif file.filename:
            flash('Invalid file type. Only PDF files are allowed.', 'danger')

    return redirect(url_for('admin.vendor_report', vendor_name=vendor_name))

@admin_bp.route('/update_vendor_report_unit_wise', methods=['POST'])
@login_required
def update_vendor_report_unit_wise():
    form = VendorForm()
    if form.validate_on_submit() or request.form.get('name'):
        vendor_name = form.name.data or request.form.get('name')
        purpose = form.purpose.data or request.form.get('purpose')
        unit = request.form.get('unit')
        count = request.form.get('count')
        original_vendor_name = request.form.get('original_vendor_name')
        cur = mysql.connection.cursor()
        try:
            if original_vendor_name:
                cur.execute('SELECT id FROM vendors WHERE name = %s', (original_vendor_name,))
                vendor = cur.fetchone()
                if vendor:
                    cur.execute('''
                        UPDATE vendors
                        SET name = %s, purpose = %s, unit = %s, count = %s
                        WHERE name = %s
                    ''', (vendor_name, purpose, unit, count, original_vendor_name))
                else:
                    cur.execute('''
                        INSERT INTO vendors (name, unit, purpose, count)
                        VALUES (%s, %s, %s, %s)
                    ''', (vendor_name, unit, purpose, count))
            else:
                cur.execute('''
                    INSERT INTO vendors (name, unit, purpose, count)
                    VALUES (%s, %s, %s, %s)
                ''', (vendor_name, unit, purpose, count))
            mysql.connection.commit()
            flash('Vendor report updated successfully.', 'success')
        except IntegrityError as e:
            if e.args[0] == 1062:
                flash('A vendor with this name already exists. Please use a unique vendor name.', 'danger')
            else:
                flash('Database error: ' + str(e), 'danger')
        return redirect(url_for('admin.vendor_report_unit_wise', unit=unit))
    else:
        flash('Form validation failed. Please check your input.', 'danger')
        return redirect(url_for('admin.vendor_report_unit_wise'))

@admin_bp.route('/export_vendor_report_unit_wise')
@login_required
def export_vendor_report_unit_wise():
    if current_user.role != 'Admin':
        flash('Access denied.', 'danger')
        return redirect(url_for('admin.dashboard'))
    
    # Get filter parameters
    unit_filter = request.args.get('unit')
    purpose_filter = request.args.get('purpose')
    
    cur = mysql.connection.cursor()
    
    # Build the query to get vendor report by units
    query = '''
        SELECT 
            v.name as vendor_name,
            l.name as unit,
            v.purpose,
            COUNT(DISTINCT b.id) as count
        FROM vendors v
        CROSS JOIN locations l
        LEFT JOIN bookings b ON b.location_id = l.id
        LEFT JOIN employees e ON b.employee_id = e.id AND e.location_id = l.id
    '''
    
    params = []
    where_conditions = []
    
    if current_user.role == 'Admin' and current_user.employee_id == 'a001':
        # For admin user 'a001', show all unit data
        pass
    elif current_user.role == 'Admin' and current_user.location:
        where_conditions.append('l.name = %s')
        params.append(current_user.location)
    
    if unit_filter:
        where_conditions.append('l.name = %s')
        params.append(unit_filter)
    
    if purpose_filter:
        where_conditions.append('v.purpose LIKE %s')
        params.append(f'%{purpose_filter}%')
    
    if where_conditions:
        query += ' WHERE ' + ' AND '.join(where_conditions)
    
    query += '''
        GROUP BY v.id, l.id, v.name, l.name, v.purpose
        ORDER BY l.name, v.name
    '''
    
    cur.execute(query, tuple(params))
    rows = cur.fetchall()
    
    # Create Excel file in memory
    df = pd.DataFrame(rows)
    
    # Rename columns for better readability
    df.columns = ['Vendor Name', 'Unit', 'Purpose', 'Count']
    
    # Create Excel file
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        df.to_excel(writer, sheet_name='Vendor Report', index=False)
        
        # Get the workbook and worksheet objects
        workbook = writer.book
        worksheet = writer.sheets['Vendor Report']
        
        # Define formats
        header_format = workbook.add_format({
            'bold': True,
            'text_wrap': True,
            'valign': 'top',
            'fg_color': '#D7E4BC',
            'border': 1
        })
        
        # Apply header format
        for col_num, value in enumerate(df.columns.values):
            worksheet.write(0, col_num, value, header_format)
        
        # Set column widths
        worksheet.set_column('A:A', 25)  # Vendor Name
        worksheet.set_column('B:B', 15)  # Unit
        worksheet.set_column('C:C', 20)  # Purpose
        worksheet.set_column('D:D', 10)  # Count
    
    output.seek(0)
    
    # Create response
    response = make_response(output.getvalue())
    response.headers['Content-Disposition'] = 'attachment; filename=vendor_report.xlsx'
    response.headers['Content-type'] = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    
    return response



@admin_bp.route('/export')
@login_required
def export():
    # Get filter parameters
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')
    department = request.args.get('department')
    location = request.args.get('location')
    cur = mysql.connection.cursor()
    query = '''
        SELECT e.name as employee, d.name as department, l.name as location, b.booking_date, b.shift, b.status
        FROM bookings b
        JOIN employees e ON b.employee_id = e.id
        LEFT JOIN departments d ON e.department_id = d.id
        LEFT JOIN locations l ON b.location_id = l.id
        WHERE 1=1
    '''
    params = []
    if start_date:
        query += ' AND b.booking_date >= %s'
        params.append(start_date)
    if end_date:
        query += ' AND b.booking_date <= %s'
        params.append(end_date)
    if department:
        query += ' AND d.name = %s'
        params.append(department)
    if location:
        query += ' AND l.name = %s'
        params.append(location)
    cur.execute(query, tuple(params))
    rows = cur.fetchall()
    return render_template('admin/export.html',
        rows=rows,
        start_date=start_date,
        end_date=end_date,
        department=department,
        location=location
    )

@admin_bp.route('/export_employee_report')
@login_required
def export_employee_report():
    if current_user.role != 'Admin':
        flash('Access denied.', 'danger')
        return redirect(url_for('admin.dashboard'))
    
    # Get filter parameters
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')
    
    cur = mysql.connection.cursor()
    
    # Build the query with optional date filters
    query = '''
        SELECT e.name as employee, d.name as department, 
               COUNT(b.id) as meals_booked,
               SUM(CASE WHEN b.status = 'Consumed' THEN 1 ELSE 0 END) as meals_consumed
        FROM employees e
        LEFT JOIN departments d ON e.department_id = d.id
        LEFT JOIN bookings b ON e.id = b.employee_id
    '''
    
    params = []
    where_conditions = []
    
    if start_date:
        where_conditions.append('b.booking_date >= %s')
        params.append(start_date)
    
    if end_date:
        where_conditions.append('b.booking_date <= %s')
        params.append(end_date)
    
    if where_conditions:
        query += ' WHERE ' + ' AND '.join(where_conditions)
    
    query += '''
        GROUP BY e.id, d.name
        ORDER BY e.name
    '''
    
    cur.execute(query, tuple(params))
    rows = cur.fetchall()
    
    # Create CSV in memory
    output = []
    header = ['Employee', 'Department', 'Meals Booked', 'Meals Consumed']
    if start_date or end_date:
        header.append('Date Range')
    output.append(header)
    
    for row in rows:
        csv_row = [
            row['employee'],
            row['department'],
            row['meals_booked'],
            row['meals_consumed']
        ]
        if start_date or end_date:
            date_range = f"{start_date or 'All'} to {end_date or 'All'}"
            csv_row.append(date_range)
        output.append(csv_row)
    
    # Convert to CSV string
    si = io.StringIO()
    cw = csv.writer(si)
    cw.writerows(output)
    response = make_response(si.getvalue())
    response.headers['Content-Disposition'] = 'attachment; filename=employee_report.csv'
    response.headers['Content-type'] = 'text/csv'
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response

@admin_bp.route('/export_meal_excel')
@login_required
def export_meal_excel():
    if current_user.role != 'Admin':
        flash('Access denied.', 'danger')
        return redirect(url_for('admin.export'))
    # Get filters from request.args
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')
    department = request.args.get('department')
    location = request.args.get('location')
    cur = mysql.connection.cursor()
    query = '''
        SELECT e.name as employee, d.name as department, l.name as location, b.booking_date, b.shift, b.status
        FROM bookings b
        JOIN employees e ON b.employee_id = e.id
        LEFT JOIN departments d ON e.department_id = d.id
        LEFT JOIN locations l ON b.location_id = l.id
        WHERE 1=1
    '''
    params = []
    if start_date:
        query += ' AND b.booking_date >= %s'
        params.append(start_date)
    if end_date:
        query += ' AND b.booking_date <= %s'
        params.append(end_date)
    if department:
        query += ' AND d.name = %s'
        params.append(department)
    if location:
        query += ' AND l.name = %s'
        params.append(location)
    cur.execute(query, tuple(params))
    rows = cur.fetchall()
    df = pd.DataFrame(rows)
    import io
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        df.to_excel(writer, index=False, sheet_name='Meal Data')
    output.seek(0)
    return send_file(output, as_attachment=True, download_name='meal_data.xlsx', mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

@admin_bp.route('/export_meal_csv')
@login_required
def export_meal_csv():
    if current_user.role != 'Admin':
        flash('Access denied.', 'danger')
        return redirect(url_for('admin.export'))
    # Get filters from request.args
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')
    department = request.args.get('department')
    location = request.args.get('location')
    cur = mysql.connection.cursor()
    query = '''
        SELECT e.name as employee, d.name as department, l.name as location, b.booking_date, b.shift, b.status
        FROM bookings b
        JOIN employees e ON b.employee_id = e.id
        LEFT JOIN departments d ON e.department_id = d.id
        LEFT JOIN locations l ON b.location_id = l.id
        WHERE 1=1
    '''
    params = []
    if start_date:
        query += ' AND b.booking_date >= %s'
        params.append(start_date)
    if end_date:
        query += ' AND b.booking_date <= %s'
        params.append(end_date)
    if department:
        query += ' AND d.name = %s'
        params.append(department)
    if location:
        query += ' AND l.name = %s'
        params.append(location)
    cur.execute(query, tuple(params))
    rows = cur.fetchall()
    import csv
    import io
    si = io.StringIO()
    if rows:
        writer = csv.DictWriter(si, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
    response = make_response(si.getvalue())
    response.headers['Content-Disposition'] = 'attachment; filename=meal_data.csv'
    response.headers['Content-type'] = 'text/csv'
    return response

@admin_bp.route('/add_user', methods=['GET', 'POST'])
@login_required
def add_user():
    if current_user.role not in ['Admin', 'Accounts']:
        flash('Access denied.', 'danger')
        return redirect(url_for('admin.dashboard'))
    form = AddUserForm()
    cur = mysql.connection.cursor()
    # Populate select field choices
    cur.execute("INSERT IGNORE INTO roles (name) VALUES ('Finance')")
    cur.execute('SELECT id, name FROM departments WHERE name != "Admin"')
    departments = cur.fetchall()
    form.department_id.choices = [(d['id'], d['name']) for d in departments]
    
    # Filter locations based on current user's unit if they are a unit admin
    if current_user.role == 'Admin' and current_user.employee_id == 'a001':
        # For admin user 'a001', show all unit data
        cur.execute('SELECT id, name FROM locations')
        locations = cur.fetchall()
        form.location_id.choices = [(l['id'], l['name']) for l in locations]
    elif current_user.role == 'Admin' and current_user.location:
        cur.execute('SELECT id, name FROM locations WHERE name = %s', (current_user.location,))
        locations = cur.fetchall()
        if not locations:
            flash('Error: Your assigned unit location was not found.', 'danger')
            return redirect(url_for('admin.dashboard'))
        form.location_id.choices = [(l['id'], l['name']) for l in locations]
        form.location_id.data = locations[0]['id'] # Pre-select the unit
    else:
        cur.execute('SELECT id, name FROM locations')
        locations = cur.fetchall()
        form.location_id.choices = [(l['id'], l['name']) for l in locations]

    cur.execute('SELECT id, name FROM roles WHERE name IN ("Admin", "Employee", "Staff", "Accounts")')
    roles = cur.fetchall()
    
    # Filter roles if the user is a unit admin
    if current_user.role == 'Admin' and current_user.employee_id == 'a001':
        # For admin user 'a001', show all unit data
        form.role_id.choices = [(r['id'], r['name']) for r in roles]
    elif current_user.role == 'Admin' and current_user.location:
        # Unit admins can only add Employees and Staff within their unit
        form.role_id.choices = [(r['id'], r['name']) for r in roles if r['name'] in ["Employee", "Staff"]]
    else:
        form.role_id.choices = [(r['id'], r['name']) for r in roles]

    if form.validate_on_submit():
        employee_id = form.employee_id.data
        name = form.name.data
        email = form.email.data
        password = form.password.data
        department_id = form.department_id.data
        location_id = form.location_id.data
        role_id = form.role_id.data
        is_active = 1 if form.is_active.data else 0

        # Enforce location for unit admins
        if current_user.role == 'Admin' and current_user.employee_id == 'a001':
            # For admin user 'a001', no location or role restrictions
            pass
        elif current_user.role == 'Admin' and current_user.location:
            cur.execute('SELECT id FROM locations WHERE name = %s', (current_user.location,))
            allowed_location_id = cur.fetchone()
            if not allowed_location_id or allowed_location_id['id'] != location_id:
                flash('You can only add users to your assigned unit.', 'danger')
                return redirect(url_for('admin.add_user'))
            # Also ensure they don't try to add an Admin or Accounts role
            cur.execute('SELECT name FROM roles WHERE id = %s', (role_id,))
            selected_role_name = cur.fetchone()['name']
            if selected_role_name not in ["Employee", "Staff"]:
                flash('You can only add users with Employee or Staff roles.', 'danger')
                return redirect(url_for('admin.add_user'))

        password_hash = hashlib.sha256(password.encode()).hexdigest()
        try:
            cur.execute("INSERT INTO employees (employee_id, name, email, password_hash, department_id, location_id, role_id, is_active) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                (employee_id, name, email, password_hash, department_id, location_id, role_id, is_active))
            mysql.connection.commit()
            flash('User added successfully!', 'success')
            return redirect(url_for('admin.add_user'))
        except Exception as e:
            flash('Error adding user: ' + str(e), 'danger')
    # Debug: print CSRF token value
    print('CSRF token in form:', getattr(form, 'csrf_token', None))
    return render_template('admin/add_user.html', form=form)

@admin_bp.route('/debug_routes')
def debug_routes():
    from flask import current_app
    output = []
    for rule in current_app.url_map.iter_rules():
        output.append(f"{rule.endpoint}: {rule}")
    return '<br>'.join(output)

@admin_bp.route('/special_messages', methods=['GET', 'POST'])
@login_required
def special_messages():
    if current_user.role != 'Admin':
        flash('Access denied: Only Admin can manage special messages.', 'danger')
        return redirect(url_for('admin.dashboard'))

    cur = mysql.connection.cursor()
    message = None
    
    if request.method == 'POST':
        message_text = request.form.get('message_text', '').strip()
        # If the form is submitted from the dashboard, there will be no 'is_active' checkbox.
        # In that case, we default to setting the message as active.
        is_active = request.form.get('is_active') == 'on' if 'is_active' in request.form else True

        if message_text:
            try:
                # Deactivate all existing messages first (or manage active status explicitly)
                cur.execute("UPDATE special_messages SET is_active = FALSE")
                
                # Insert new message or update existing active one
                # For simplicity, let's always insert a new one and mark it active
                # A more complex system might update an existing active one
                cur.execute(
                    "INSERT INTO special_messages (message_text, is_active) VALUES (%s, %s)",
                    (message_text, is_active)
                )
                mysql.connection.commit()
                flash('Special message updated successfully!', 'success')
            except Exception as e:
                mysql.connection.rollback()
                flash(f'Error updating special message: {str(e)}', 'danger')
        else:
            # If message_text is empty, deactivate all messages
            try:
                cur.execute("UPDATE special_messages SET is_active = FALSE")
                mysql.connection.commit()
                flash('All special messages deactivated.', 'info')
            except Exception as e:
                mysql.connection.rollback()
                flash(f'Error deactivating messages: {str(e)}', 'danger')

        return redirect(url_for('admin.dashboard'))

    # GET request: Fetch the current active message
    cur.execute("SELECT message_text, is_active FROM special_messages WHERE is_active = TRUE AND DATE(created_at) = CURDATE() ORDER BY created_at DESC LIMIT 1")
    active_message = cur.fetchone()
    if active_message:
        message = active_message['message_text']
        is_active = active_message['is_active']
    else:
        message = ""
        is_active = False

    return render_template('admin/special_messages.html', message=message, is_active=is_active)

@admin_bp.route('/edit_vendor/<vendor_name>', methods=['GET'])
@login_required
def edit_vendor(vendor_name):
    if current_user.role != 'Admin':
        flash('Access denied.', 'danger')
        return redirect(url_for('admin.dashboard'))

    cur = mysql.connection.cursor()
    cur.execute("SELECT * FROM vendors WHERE name = %s", (vendor_name,))
    vendor_data = cur.fetchone()

    if not vendor_data:
        flash('Vendor not found.', 'danger')
        return redirect(url_for('admin.vendor_report'))

    form = VendorForm(data=vendor_data)

    cur.execute('SELECT name FROM locations ORDER BY name')
    locations = cur.fetchall()
    form.unit.choices = [(l['name'], l['name']) for l in locations]

    cur.execute('SELECT DISTINCT purpose FROM vendors WHERE purpose IS NOT NULL AND purpose != "" ORDER BY purpose')
    purposes = cur.fetchall()
    form.purpose.choices = [(p['purpose'], p['purpose']) for p in purposes]

    # Pre-select values for dropdowns
    form.unit.data = vendor_data['unit']
    form.purpose.data = vendor_data['purpose']

    return render_template('admin/add_vendor_item.html', form=form, vendor_data=vendor_data, csrf_token=generate_csrf())


@admin_bp.route('/add_vendor_item', methods=['GET', 'POST'])
@login_required
def add_vendor_item():
    if current_user.role != 'Admin':
        flash('Access denied.', 'danger')
        return redirect(url_for('admin.dashboard'))

    form = VendorForm()
    cur = mysql.connection.cursor()

    cur.execute('SELECT name FROM locations ORDER BY name')
    locations = cur.fetchall()
    form.unit.choices = [(l['name'], l['name']) for l in locations]

    cur.execute('SELECT DISTINCT purpose FROM vendors WHERE purpose IS NOT NULL AND purpose != "" ORDER BY purpose')
    purposes = cur.fetchall()
    form.purpose.choices = [(p['purpose'], p['purpose']) for p in purposes]

    if form.validate_on_submit():
        vendor_name = form.name.data
        unit = form.unit.data
        purpose = form.purpose.data
        count = form.count.data
        agreement_date = form.agreement_date.data
        original_vendor_name = request.form.get('original_vendor_name') # Get original name for updates
        
        food_licence_path = None
        # If editing, retain existing food_licence_path if no new file is uploaded
        if original_vendor_name:
            cur = mysql.connection.cursor()
            cur.execute("SELECT food_licence_path FROM vendors WHERE name = %s", (original_vendor_name,))
            existing_vendor = cur.fetchone()
            if existing_vendor:
                food_licence_path = existing_vendor['food_licence_path']

        if 'food_licence' in request.files:
            file = request.files['food_licence']
            if file and file.filename and allowed_file(file.filename):
                filename = secure_filename(file.filename)
                relative_file_path = os.path.join('uploads/food_licences', filename).replace('\\', '/')
                full_file_path = os.path.join(UPLOAD_FOLDER, filename)
                os.makedirs(UPLOAD_FOLDER, exist_ok=True)
                file.save(full_file_path)
                food_licence_path = relative_file_path
            elif file.filename:
                flash('Invalid file type for food licence. Only PDF files are allowed.', 'danger')
                # Pass vendor_data back to the template if it's an edit operation
                if original_vendor_name:
                    cur = mysql.connection.cursor()
                    cur.execute("SELECT * FROM vendors WHERE name = %s", (original_vendor_name,))
                    vendor_data = cur.fetchone()
                    return render_template('admin/add_vendor_item.html', form=form, vendor_data=vendor_data, csrf_token=generate_csrf())
                return render_template('admin/add_vendor_item.html', form=form, csrf_token=generate_csrf())

        try:
            cur = mysql.connection.cursor()
            if original_vendor_name:
                # Update existing vendor
                cur.execute("""
                    UPDATE vendors
                    SET name = %s, unit = %s, purpose = %s, count = %s, food_licence_path = %s, agreement_date = %s
                    WHERE name = %s
                """, (vendor_name, unit, purpose, count, food_licence_path, agreement_date, original_vendor_name))
                flash('Vendor item updated successfully!', 'success')
            else:
                # Add new vendor
                cur.execute("INSERT INTO vendors (name, unit, purpose, count, food_licence_path, agreement_date) VALUES (%s, %s, %s, %s, %s, %s)",
                            (vendor_name, unit, purpose, count, food_licence_path, agreement_date))
                flash('Vendor item added successfully!', 'success')
            mysql.connection.commit()
            return redirect(url_for('admin.vendor_report'))
        except IntegrityError as e:
            if e.args[0] == 1062:
                flash('A vendor with this name already exists. Please use a unique vendor name.', 'danger')
            else:
                flash('Database error: ' + str(e), 'danger')
        except Exception as e:
            flash(f'Error processing vendor item: {e}', 'danger')

    return render_template('admin/add_vendor_item.html', form=form, csrf_token=generate_csrf())

@admin_bp.route('/add_menu', methods=['GET', 'POST'])
@login_required
def add_menu():
    if current_user.role != 'Admin':
        flash('Access denied.', 'danger')
        return redirect(url_for('admin.dashboard'))

    form = AddMenuForm()
    # Set default date to tomorrow
    if request.method == 'GET':
        form.menu_date.data = date.today()
    cur = mysql.connection.cursor()

    # Populate location choices
    if current_user.employee_id == 'a001':
        cur.execute("SELECT id, name FROM locations")
    else:
        cur.execute("SELECT id, name FROM locations WHERE name = %s", (current_user.location,))
        locations = cur.fetchall()
        form.location_id.choices = [(l['id'], l['name']) for l in locations]
        if locations:
            form.location_id.data = locations[0]['id']

    if form.validate_on_submit():
        location_id = form.location_id.data
        menu_date = form.menu_date.data
        meal_type = form.meal_type.data
        items = form.items.data

        # Check if a menu for this meal type, date, and location already exists
        cur.execute("SELECT id FROM daily_menus WHERE location_id = %s AND menu_date = %s AND meal_type = %s", (location_id, menu_date, meal_type))
        existing_menu = cur.fetchone()

        if existing_menu:
            flash(f'A menu for {meal_type} on {menu_date} for this location already exists.', 'warning')
            return redirect(url_for('admin.add_menu'))

        try:
            cur.execute("""
                INSERT INTO daily_menus (location_id, menu_date, meal_type, items)
                VALUES (%s, %s, %s, %s)
            """, (location_id, menu_date, meal_type, items))
            mysql.connection.commit()
            flash('Menu added successfully!', 'success')
            return redirect(url_for('admin.add_menu'))
        except Exception as e:
            flash(f'Error adding menu: {e}', 'danger')

    return render_template('admin/add_menu.html', form=form)
