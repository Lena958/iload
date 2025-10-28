from flask import Blueprint, render_template, request, redirect, url_for, flash, session
import mysql.connector

subjects_bp = Blueprint('subjects', __name__, url_prefix='/admin/subjects')

# Database config
db_config = {
    'host': 'localhost',
    'user': 'root',
    'password': '',
    'database': 'iload'
}

def get_db_connection():
    return mysql.connector.connect(**db_config)

def query_db(query, args=(), one=False, dictionary=True):
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=dictionary)
    try:
        cursor.execute(query, args)
        if query.strip().upper().startswith("SELECT"):
            rv = cursor.fetchall()
            return (rv[0] if rv else None) if one else rv
        else:
            conn.commit()
    finally:
        conn.close()

def is_admin():
    return session.get('role') == 'admin'

# Context processor to inject instructor name
@subjects_bp.context_processor
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
# List all subjects (no schedule status)
@subjects_bp.route('/')
def list_subjects():
    if not is_admin():
        return redirect(url_for('login'))

    subjects = query_db("""
        SELECT s.*, i.name AS instructor_name
        FROM subjects s
        LEFT JOIN instructors i ON s.instructor_id = i.instructor_id
    """)

    return render_template("admin/subjects.html", subjects=subjects)

# Add subject
@subjects_bp.route('/add', methods=['GET', 'POST'])
def add_subject():
    if not is_admin():
        return redirect(url_for('login'))

    instructors = query_db("SELECT instructor_id, name FROM instructors")

    if request.method == 'POST':
        code = request.form['code']
        name = request.form['name']
        units = request.form['units']
        year_level = request.form['year_level']
        section = request.form['section']
        course = request.form['course']
        instructor_id = request.form.get('instructor_id') or None

        query_db("""
            INSERT INTO subjects (code, name, units, year_level, section, course, instructor_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """, (code, name, units, year_level, section, course, instructor_id))

        flash("Subject added successfully")
        return redirect(url_for('subjects.list_subjects'))

    return render_template("admin/add_subject.html", instructors=instructors)

# Edit subject
@subjects_bp.route('/edit/<int:subject_id>', methods=['GET', 'POST'])
def edit_subject(subject_id):
    if not is_admin():
        return redirect(url_for('login'))

    instructors = query_db("SELECT instructor_id, name FROM instructors")

    if request.method == 'POST':
        code = request.form['code']
        name = request.form['name']
        units = request.form['units']
        year_level = request.form['year_level']
        section = request.form['section']
        course = request.form['course']
        instructor_id = request.form.get('instructor_id') or None

        query_db("""
            UPDATE subjects
            SET code=%s, name=%s, units=%s, year_level=%s, section=%s, course=%s, instructor_id=%s
            WHERE subject_id=%s
        """, (code, name, units, year_level, section, course, instructor_id, subject_id))

        flash("Subject updated successfully")
        return redirect(url_for('subjects.list_subjects'))

    subject = query_db("SELECT * FROM subjects WHERE subject_id = %s", (subject_id,), one=True)
    return render_template("admin/edit_subject.html", subject=subject, instructors=instructors)

# Delete subject
@subjects_bp.route('/delete/<int:subject_id>', methods=['POST'])
def delete_subject(subject_id):
    if not is_admin():
        return redirect(url_for('login'))

    query_db("DELETE FROM subjects WHERE subject_id = %s", (subject_id,))
    flash("Subject deleted successfully")
    return redirect(url_for('subjects.list_subjects'))

# View subject details (no schedule status)
@subjects_bp.route('/view/<int:subject_id>')
def view_subject(subject_id):
    if not is_admin():
        return redirect(url_for('login'))

    subject = query_db("""
        SELECT s.*, i.name AS instructor_name
        FROM subjects s
        LEFT JOIN instructors i ON s.instructor_id = i.instructor_id
        WHERE s.subject_id = %s
    """, (subject_id,), one=True)

    return render_template("admin/view_subject.html", subject=subject)
