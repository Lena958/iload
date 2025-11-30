from flask import Flask, render_template, request, redirect, url_for, session, flash, g, abort
from werkzeug.security import check_password_hash
from db import get_db_connection
import re
import logging
from functools import wraps
import time

# Import blueprints
from admin_modules import (
    admin_bp, instructors_bp, subjects_bp, rooms_bp,
    schedules_bp, auto_scheduler_bp, conflicts_bp,
    feedback_bp, load_bp, dashboard_bp, courses_bp
)
from instructor_module import instructor_bp, room_bp, instructor_dashboard_bp

# ==================== CONFIGURATION CLASSES ====================
class SecurityConfig:
    MAX_LOGIN_ATTEMPTS = 5
    LOCKOUT_TIME = 900
    USERNAME_PATTERN = r'^[a-zA-Z0-9_]{3,50}$'

class AppConfig:
    SECRET_KEY = 'your_secret_key_here'

# ==================== VALIDATION SERVICE ====================
class InputValidator:
    """SRP: Only handles input validation"""
    
    @staticmethod
    def validate_username(username):
        if not username or len(username) > 50:
            return False
        return bool(re.match(SecurityConfig.USERNAME_PATTERN, username))
    
    @staticmethod
    def sanitize_input(input_string, max_length=255):
        if not input_string:
            return ""
        sanitized = re.sub(r'[<>"\']', '', input_string)
        return sanitized[:max_length]
    
    @staticmethod
    def validate_login_inputs(username, password):
        if not username or not password:
            return "Username and password are required."
        if not InputValidator.validate_username(username):
            return "Invalid username format."
        return None

# ==================== RATE LIMITING SERVICE ====================
class RateLimiter:
    """SRP: Only handles rate limiting logic"""
    
    @staticmethod
    def check_attempts(session):
        attempts = session.get('login_attempts', 0)
        lockout_time = session.get('lockout_time', 0)
        
        if lockout_time > time.time():
            remaining_time = int((lockout_time - time.time()) / 60)
            return False, f"Too many login attempts. Try again in {remaining_time} minutes."
        
        if attempts >= SecurityConfig.MAX_LOGIN_ATTEMPTS:
            session['lockout_time'] = time.time() + SecurityConfig.LOCKOUT_TIME
            return False, "Too many login attempts. Account temporarily locked."
        
        return True, ""
    
    @staticmethod
    def increment_attempts(session):
        session['login_attempts'] = session.get('login_attempts', 0) + 1

# ==================== DATABASE SERVICE ====================
class DatabaseService:
    """SRP: Only handles database operations"""
    
    @staticmethod
    def get_connection():
        try:
            return get_db_connection()
        except Exception as e:
            logging.getLogger(__name__).error(f"Database connection failed: {type(e).__name__}")
            return None
    
    @staticmethod
    def close_connection(conn, cursor=None):
        try:
            if cursor:
                cursor.close()
            if conn:
                conn.close()
        except Exception as e:
            logging.getLogger(__name__).error(f"Error closing connection: {type(e).__name__}")
    
    @staticmethod
    def get_user_by_username(username):
        conn = DatabaseService.get_connection()
        if not conn:
            return None, None
        
        try:
            cursor = conn.cursor(dictionary=True)
            cursor.execute(
                "SELECT instructor_id, username, password, role FROM instructors WHERE username = %s", 
                (username,)
            )
            user = cursor.fetchone()
            return user, (conn, cursor)
        except Exception as e:
            DatabaseService.close_connection(conn)
            logging.getLogger(__name__).error(f"Database query error: {type(e).__name__}")
            return None, None
    
    @staticmethod
    def get_instructor_name(user_id):
        conn = DatabaseService.get_connection()
        if not conn:
            return None
        
        try:
            cursor = conn.cursor(dictionary=True)
            cursor.execute("SELECT name FROM instructors WHERE instructor_id = %s", (user_id,))
            user = cursor.fetchone()
            return user.get('name') if user else None
        except Exception as e:
            logging.getLogger(__name__).error(f"Error getting instructor name: {type(e).__name__}")
            return None
        finally:
            DatabaseService.close_connection(conn, cursor)

# ==================== SESSION SERVICE ====================
class SessionService:
    """SRP: Only handles session management"""
    
    @staticmethod
    def setup_user_session(user, session):
        session.clear()
        session['user_id'] = user['instructor_id']
        session['username'] = user['username']
        session['role'] = user['role']
        session['last_activity'] = time.time()
        SessionService.clear_login_attempts(session)
    
    @staticmethod
    def clear_login_attempts(session):
        session.pop('login_attempts', None)
        session.pop('lockout_time', None)
    
    @staticmethod
    def is_user_logged_in(session):
        return 'user_id' in session
    
    @staticmethod
    def get_user_role(session):
        return session.get('role')

# ==================== AUTHENTICATION SERVICE ====================
class AuthenticationService:
    """SRP: Only handles authentication logic"""
    
    @staticmethod
    def authenticate(username, password, session):
        user, db_resources = DatabaseService.get_user_by_username(username)
        
        try:
            if user and check_password_hash(user['password'], password):
                SessionService.setup_user_session(user, session)
                logging.getLogger(__name__).info(f"Successful login for user: {username}")
                return user, None
            else:
                RateLimiter.increment_attempts(session)
                logging.getLogger(__name__).warning(f"Failed login attempt for username: {username}")
                return None, "Invalid username or password"
        finally:
            if db_resources:
                DatabaseService.close_connection(*db_resources)
    
    @staticmethod
    def get_redirect_path(role):
        if role == 'admin':
            return redirect(url_for('dashboard.admin_dashboard'))
        return redirect(url_for('instructor_dashboard.dashboard'))

# ==================== LOGIN HANDLER SERVICE ====================
class LoginHandler:
    """SRP: Orchestrates the login process"""
    
    @staticmethod
    def process_login_request(request, session):
        username = InputValidator.sanitize_input(request.form.get('username', '').strip())
        password = request.form.get('password', '')
        
        # Input Validation
        validation_error = InputValidator.validate_login_inputs(username, password)
        if validation_error:
            return validation_error, None
        
        # Rate Limiting
        is_allowed, rate_limit_msg = RateLimiter.check_attempts(session)
        if not is_allowed:
            return rate_limit_msg, None
        
        # Authentication
        user, auth_error = AuthenticationService.authenticate(username, password, session)
        if auth_error:
            return auth_error, None
        
        return None, user

# ==================== FLASK APP SETUP ====================
app = Flask(__name__)
app.secret_key = AppConfig.SECRET_KEY

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ==================== DECORATORS ====================
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not SessionService.is_user_logged_in(session):
            flash("Please log in to access this page.")
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not SessionService.is_user_logged_in(session):
            flash("Please log in to access this page.")
            return redirect(url_for('login'))
        if SessionService.get_user_role(session) != 'admin':
            flash("Access denied. Admin privileges required.")
            logger.warning(f"Unauthorized admin access attempt by user: {session.get('user_id')}")
            abort(403)
        return f(*args, **kwargs)
    return decorated_function

# ==================== BLUEPRINT PROTECTION ====================
def protect_blueprints():
    def require_admin_auth():
        if not SessionService.is_user_logged_in(session):
            flash("Please log in to access this page.")
            return redirect(url_for('login'))
        if SessionService.get_user_role(session) != 'admin':
            flash("Access denied. Admin privileges required.")
            abort(403)
    
    def require_instructor_auth():
        if not SessionService.is_user_logged_in(session):
            flash("Please log in to access this page.")
            return redirect(url_for('login'))
    
    # Admin blueprints
    admin_blueprints = [
        admin_bp, instructors_bp, subjects_bp, rooms_bp,
        schedules_bp, auto_scheduler_bp, conflicts_bp,
        feedback_bp, load_bp, dashboard_bp, courses_bp
    ]
    for bp in admin_blueprints:
        bp.before_request(require_admin_auth)
    
    # Instructor blueprints
    instructor_blueprints = [instructor_bp, room_bp, instructor_dashboard_bp]
    for bp in instructor_blueprints:
        bp.before_request(require_instructor_auth)

# ==================== ROUTES ====================
@app.route('/')
def home():
    return render_template('home.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if SessionService.is_user_logged_in(session):
        return AuthenticationService.get_redirect_path(SessionService.get_user_role(session))
    
    if request.method == 'POST':
        error, user = LoginHandler.process_login_request(request, session)
        if error:
            flash(error)
            return render_template('login.html')
        return AuthenticationService.get_redirect_path(user['role'])
    
    return render_template('login.html')

@app.route('/logout')
def logout():
    user_id = session.get('user_id')
    session.clear()
    logger.info(f"User {user_id} logged out")
    flash("You have been successfully logged out.")
    return redirect(url_for('login'))

@app.before_request
def load_instructor_name():
    g.instructor_name = None
    user_id = session.get('user_id')
    if user_id:
        g.instructor_name = DatabaseService.get_instructor_name(user_id)

# ==================== ERROR HANDLERS ====================
@app.errorhandler(404)
def not_found_error(error):
    return render_template('404.html'), 404

@app.errorhandler(403)
def forbidden_error(error):
    return render_template('403.html'), 403

@app.errorhandler(500)
def internal_error(error):
    logger.error(f"Internal Server Error: {str(error)}")
    return render_template('500.html'), 500

# ==================== APPLICATION INITIALIZATION ====================
def initialize_app():
    protect_blueprints()
    
    blueprints = [
        admin_bp, instructors_bp, subjects_bp, rooms_bp,
        schedules_bp, auto_scheduler_bp, conflicts_bp,
        feedback_bp, load_bp, dashboard_bp, courses_bp,
        instructor_bp, room_bp, instructor_dashboard_bp
    ]
    
    for blueprint in blueprints:
        app.register_blueprint(blueprint)

initialize_app()

if __name__ == '__main__':
    app.run(debug=True)