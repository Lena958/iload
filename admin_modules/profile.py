from flask import Blueprint, render_template, session, redirect, url_for, flash
import mysql.connector

profile_bp = Blueprint('profile', __name__, url_prefix='/instructor')

db_config = {
    'host': 'localhost',
    'user': 'root',
    'password': '',
    'database': 'iload'
}

def get_db_connection():
    return mysql.connector.connect(**db_config)

@profile_bp.route('/profile')
def profile():
    if 'user_id' not in session:
        flash("Please log in first.", "warning")
        return redirect(url_for('login'))

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute(
        "SELECT instructor_id, name, max_load_units, department, username, role FROM instructors WHERE instructor_id = %s",
        (session['user_id'],)
    )
    instructor = cursor.fetchone()
    cursor.close()
    conn.close()

    if not instructor:
        flash("Instructor not found.", "danger")
        return redirect(url_for('login'))

    return render_template('admin/profile.html', instructor=instructor)
