from flask import Blueprint, render_template, request, redirect, url_for, flash, session
from werkzeug.security import generate_password_hash, check_password_hash
import mysql.connector

instructors_bp = Blueprint('instructors', __name__, url_prefix='/admin/instructors')

db_config = {
    'host': 'localhost',
    'user': 'root',
    'password': '',
    'database': 'iload'
}

def get_db_connection():
    return mysql.connector.connect(**db_config)

def is_admin():
    return session.get('role') == 'admin'

# Login route (you can move this elsewhere if needed)
@instructors_bp.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']

        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT * FROM instructors WHERE username = %s", (username,))
        user = cursor.fetchone()
        conn.close()

        if user and check_password_hash(user['password'], password):
            session['user_id'] = user['instructor_id']
            session['role'] = user['role']
            flash("Logged in successfully!", "success")
            return redirect(url_for('instructors.list_instructors'))  # Change to your dashboard if needed

        flash("Invalid username or password", "danger")

    return render_template('login.html')

# Context processor to inject logged-in instructor's name
@instructors_bp.context_processor
def inject_instructor_name():
    if 'user_id' not in session:
        return dict(instructor_name=None, instructor_image=None)

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT name, image FROM instructors WHERE instructor_id = %s", (session['user_id'],))
    instructor = cursor.fetchone()
    conn.close()

    return dict(
        instructor_name=instructor['name'] if instructor else None,
        instructor_image=instructor['image'] if instructor and instructor['image'] else None
    )

# List all instructors
@instructors_bp.route('/')
def list_instructors():
    if not is_admin():
        return redirect(url_for('instructors.login'))

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM instructors")
    instructors = cursor.fetchall()
    conn.close()
    return render_template("admin/instructors.html", instructors=instructors)

# Add instructor
@instructors_bp.route('/add', methods=['GET', 'POST'])
def add_instructor():
    if not is_admin():
        return redirect(url_for('instructors.login'))

    if request.method == 'POST':
        name = request.form['name']
        max_load_units = request.form['max_load_units']
        department = request.form['department']
        username = request.form['username']
        password = request.form['password']
        role = request.form['role']

        hashed_password = generate_password_hash(password)

        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO instructors (name, max_load_units, department, username, password, role)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (name, max_load_units, department, username, hashed_password, role)
        )
        conn.commit()
        conn.close()

        flash("Instructor added successfully")
        return redirect(url_for('instructors.list_instructors'))

    return render_template("admin/add_instructor.html")

# Edit instructor
@instructors_bp.route('/edit/<int:instructor_id>', methods=['GET', 'POST'])
def edit_instructor(instructor_id):
    if not is_admin():
        return redirect(url_for('instructors.login'))

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    if request.method == 'POST':
        name = request.form['name']
        max_load_units = request.form['max_load_units']
        department = request.form['department']

        cursor.execute(
            "UPDATE instructors SET name=%s, max_load_units=%s, department=%s WHERE instructor_id=%s",
            (name, max_load_units, department, instructor_id)
        )
        conn.commit()
        conn.close()
        flash("Instructor updated successfully")
        return redirect(url_for('instructors.list_instructors'))

    cursor.execute("SELECT * FROM instructors WHERE instructor_id = %s", (instructor_id,))
    instructor = cursor.fetchone()
    conn.close()
    return render_template("admin/edit_instructor.html", instructor=instructor)

# Delete instructor
@instructors_bp.route('/delete/<int:instructor_id>', methods=['POST'])
def delete_instructor(instructor_id):
    if not is_admin():
        return redirect(url_for('instructors.login'))

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM instructors WHERE instructor_id = %s", (instructor_id,))
    conn.commit()
    conn.close()
    flash("Instructor deleted successfully")
    return redirect(url_for('instructors.list_instructors'))
