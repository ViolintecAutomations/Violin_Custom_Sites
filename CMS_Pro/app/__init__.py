import os
import sys
import platform
from flask import Flask, render_template, request, session
from flask_mysqldb import MySQL
from flask_login import LoginManager, UserMixin
from flask_bootstrap import Bootstrap
from flask_wtf import CSRFProtect

# Explicitly add user site-packages path for stubborn module imports
if platform.system() == "Windows":
    user_site_packages = r'c:\users\vtgs_lap_01\appdata\local\packages\pythonsoftwarefoundation.python.3.13_qbz5n2kfra8p0\localcache\local-packages\python313\site-packages'
    if user_site_packages not in sys.path:
        sys.path.insert(0, user_site_packages) # Insert at the beginning to prioritize
from flask_babel import Babel
from datetime import datetime
import pymysql # Import pymysql to access DictCursor
from CSV_Param import CSV_Proj_Params # Import the new config loader

Curr_Proj_Name = 'CMS' 

# Initialize extensions
mysql = MySQL()
login_manager = LoginManager()
bootstrap = Bootstrap()
csrf = CSRFProtect()
babel = Babel()

# User class
class User(UserMixin):
    def __init__(self, id, name='Guest', email=None, role=None, department=None, location=None, employee_id=None):
        self.id = id
        self.name = name
        self.email = email
        self.role = role
        self.department = department
        self.location = location
        self.employee_id = employee_id

# User loader
@login_manager.user_loader
def load_user(user_id):
    try:
        cur = mysql.connection.cursor()
        cur.execute("SELECT * FROM employees WHERE id=%s", (user_id,))
        user = cur.fetchone()
        if user:
            role_map = {1: 'Employee', 2: 'Staff', 3: 'Supervisor', 4: 'HR', 5: 'Accounts', 6: 'Admin'}
            role = role_map.get(user['role_id'], 'Employee')
            department = None
            if 'department_id' in user and user['department_id']:
                cur.execute("SELECT name FROM departments WHERE id=%s", (user['department_id'],))
                dept = cur.fetchone()
                if dept:
                    department = dept['name']
            location = None
            if 'location_id' in user and user['location_id']:
                cur.execute("SELECT name FROM locations WHERE id=%s", (user['location_id'],))
                loc = cur.fetchone()
                if loc:
                    location = loc['name']
            return User(user['id'], name=user['name'], email=user['email'], role=role, department=department, location=location, employee_id=user['employee_id'])
    except Exception as e:
        import traceback
        with open("app_errors.log", "a") as log_file:
            log_file.write(f"[{datetime.now()}] Error loading user: {e}\n")
            traceback.print_exc(file=log_file)
        print(f"Error loading user: {e}") # Keep print for local dev if not suppressed
        traceback.print_exc() # Print to console as well
        return None

# App factory
def create_app():
    print("Flask application creation started.") # Very early print statement
    app = Flask(__name__, static_folder='static', static_url_path=f"/static" "")
    app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'change-this-in-production')
    app.config['LANGUAGES'] = ['en', 'ta', 'hi']
    app.config['BABEL_DEFAULT_LOCALE'] = 'en'
    app.debug = True # Enable debug mode for detailed error messages
    app.config['UPLOAD_FOLDER'] = os.path.join(app.root_path, 'static', 'uploads')
    app.config['ALLOWED_EXTENSIONS'] = {'pdf'}

    # Load MySQL config from CSV
    proj_params = CSV_Proj_Params(Curr_Proj_Name)
    app.config['MYSQL_HOST'] = os.environ.get('MYSQL_HOST', proj_params.get('MYSQL_HOST'))
    app.config['MYSQL_PORT'] = int(os.environ.get('MYSQL_PORT', proj_params.get('MYSQL_PORT')))
    app.config['MYSQL_USER'] = os.environ.get('MYSQL_USER', proj_params.get('MYSQL_USER'))
    app.config['MYSQL_PASSWORD'] = os.environ.get('MYSQL_PASSWORD', proj_params.get('MYSQL_PASSWORD'))
    app.config['MYSQL_DB'] = os.environ.get('MYSQL_DB', proj_params.get('MYSQL_DB'))
    app.config['MYSQL_CURSORCLASS'] = proj_params.get('MYSQL_CURSORCLASS', 'DictCursor')

    mysql.init_app(app)
    login_manager.init_app(app)
    bootstrap.init_app(app)
    csrf.init_app(app)

    def get_locale():
        if request.args.get('lang'):
            session['lang'] = request.args.get('lang')
        return session.get('lang', app.config['BABEL_DEFAULT_LOCALE'])

    babel.init_app(app, locale_selector=get_locale)

    # Register blueprints
    from .cms import cms_blueprint
    from .employee import employee_bp
    from .staff import staff_bp
    from .admin import admin_bp, init_admin_config

    # Initialize admin config with app settings
    init_admin_config(app)

    # Register blueprints with their intended internal prefixes
    url_prefix = ""
    app.register_blueprint(cms_blueprint, url_prefix='/') # CMS home is at the root of its app
    app.register_blueprint(employee_bp, url_prefix='/employee')
    app.register_blueprint(staff_bp, url_prefix='/staff')
    app.register_blueprint(admin_bp, url_prefix='/admin')

    @app.errorhandler(404)
    def not_found_error(error):
        return render_template('404.html'), 404

    @app.errorhandler(500)
    def internal_error(error):
        import traceback
        tb = traceback.format_exc()
        print(f"Unhandled Internal Server Error: {error}")
        print(tb)
        return f"<h1>Internal Server Error</h1><pre>{tb}</pre>", 500

    @app.errorhandler(Exception)
    def unhandled_exception(e):
        import traceback
        tb = traceback.format_exc()
        print(f"Caught unhandled exception: {e}")
        print(tb)
        return f"<h1>Unhandled Exception</h1><pre>{tb}</pre>", 500

    @app.route('/') # Index route for the CMS app itself
    def index():
        current_lang = session.get('lang', app.config['BABEL_DEFAULT_LOCALE'])
        return render_template('index.html', current_lang=current_lang)

    return app
