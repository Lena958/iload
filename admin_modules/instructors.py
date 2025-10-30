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

# Login route
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
            return redirect(url_for('instructors.list_instructors'))

        flash("Invalid username or password", "danger")

    return render_template('login.html')

# Context processor for logged-in instructor's info
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

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    # Fetch distinct programs and statuses from DB
    cursor.execute("SELECT DISTINCT program FROM instructors WHERE program IS NOT NULL AND program != ''")
    programs = [row['program'] for row in cursor.fetchall()]

    cursor.execute("SELECT DISTINCT status FROM instructors WHERE status IS NOT NULL AND status != ''")
    statuses = [row['status'] for row in cursor.fetchall()]

    conn.close()

    if request.method == 'POST':
        name = request.form['name']
        max_load_units = request.form['max_load_units']
        department = request.form['department']
        program = request.form['program']
        status = request.form['status']
        username = request.form['username']
        password = request.form['password']
        role = request.form['role']

        hashed_password = generate_password_hash(password)

        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO instructors (name, max_load_units, department, program, status, username, password, role)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (name, max_load_units, department, program, status, username, hashed_password, role)
        )
        conn.commit()
        conn.close()

        flash("Instructor added successfully", "success")
        return redirect(url_for('instructors.list_instructors'))

    return render_template(
        "admin/add_instructor.html",
        programs=programs,
        statuses=statuses
    )

# Edit instructor
@instructors_bp.route('/edit/<int:instructor_id>', methods=['GET', 'POST'])
def edit_instructor(instructor_id):
    if not is_admin():
        return redirect(url_for('instructors.login'))

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    # Fetch distinct programs and statuses from DB
    cursor.execute("SELECT DISTINCT program FROM instructors WHERE program IS NOT NULL AND program != ''")
    programs = [row['program'] for row in cursor.fetchall()]

    cursor.execute("SELECT DISTINCT status FROM instructors WHERE status IS NOT NULL AND status != ''")
    statuses = [row['status'] for row in cursor.fetchall()]

    if request.method == 'POST':
        name = request.form['name']
        max_load_units = request.form['max_load_units']
        department = request.form['department']
        program = request.form['program']
        status = request.form['status']

        cursor.execute(
            """
            UPDATE instructors 
            SET name=%s, max_load_units=%s, department=%s, program=%s, status=%s
            WHERE instructor_id=%s
            """,
            (name, max_load_units, department, program, status, instructor_id)
        )
        conn.commit()
        conn.close()
        flash("Instructor updated successfully", "success")
        return redirect(url_for('instructors.list_instructors'))

    cursor.execute("SELECT * FROM instructors WHERE instructor_id = %s", (instructor_id,))
    instructor = cursor.fetchone()
    conn.close()

    return render_template(
        "admin/edit_instructor.html",
        instructor=instructor,
        programs=programs,
        statuses=statuses
    )

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
    flash("Instructor deleted successfully", "success")
    return redirect(url_for('instructors.list_instructors'))
