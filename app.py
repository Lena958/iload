from flask import Flask, render_template, request, redirect, url_for, session, flash, g
from werkzeug.security import check_password_hash
from db import get_db_connection

# Import blueprints
from admin_modules import (
    admin_bp, instructors_bp, subjects_bp, rooms_bp,
    schedules_bp, auto_scheduler_bp, conflicts_bp,
    feedback_bp, load_bp, dashboard_bp, courses_bp
)
from instructor_module import instructor_bp, room_bp, instructor_dashboard_bp  # Instructor blueprints



app = Flask(__name__)
app.secret_key = 'your_secret_key_here'

# Register blueprints
app.register_blueprint(admin_bp)
app.register_blueprint(instructors_bp)
app.register_blueprint(subjects_bp)
app.register_blueprint(rooms_bp)
app.register_blueprint(schedules_bp)
app.register_blueprint(auto_scheduler_bp)
app.register_blueprint(conflicts_bp)
app.register_blueprint(feedback_bp)
app.register_blueprint(load_bp)
app.register_blueprint(dashboard_bp)
app.register_blueprint(instructor_bp)
app.register_blueprint(room_bp)
app.register_blueprint(courses_bp)
app.register_blueprint(instructor_dashboard_bp)

# Fetch instructor name for sidebar
@app.before_request
def load_instructor_name():
    g.instructor_name = None
    user_id = session.get('user_id')
    if user_id:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT name FROM instructors WHERE instructor_id = %s", (user_id,))
        user = cursor.fetchone()
        cursor.close()
        conn.close()
        if user:
            g.instructor_name = user.get('name')

# Home route
@app.route('/')
def home():
    return render_template('home.html')

# Login route
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']

        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT * FROM instructors WHERE username = %s", (username,))
        user = cursor.fetchone()
        cursor.close()
        conn.close()

        if user and check_password_hash(user['password'], password):
            session['user_id'] = user['instructor_id']
            session['username'] = user['username']
            session['role'] = user['role']

            # Role-based redirect
            if user['role'] == 'admin':
                return redirect(url_for('dashboard.admin_dashboard'))  # matches dashboard_bp
            else:
                return redirect(url_for('instructor_dashboard.dashboard'))  # matches instructor_bp

        flash("Invalid username or password")
    return render_template('login.html')

# Logout route
@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

if __name__ == '__main__':
    app.run(debug=True)
