from flask import Blueprint, render_template, request, redirect, url_for, flash, session
from functools import wraps
import mysql.connector

feedback_bp = Blueprint('feedback', __name__, url_prefix='/admin/feedback')

# Database Configuration
db_config = {
    'host': 'localhost',
    'user': 'root',
    'password': '',
    'database': 'iload'
}

# Utility: Get DB connection
def get_db_connection():
    return mysql.connector.connect(**db_config)

# Utility: Admin role check
def is_admin():
    return session.get('role') == 'admin'

# Decorator for admin-only access
def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not is_admin():
            flash("Access denied. Admins only.")
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

# Context processor: Inject instructor name into templates
@feedback_bp.context_processor
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

# Route: List all feedback
@feedback_bp.route('/')
@admin_required
def list_feedback():
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("""
            SELECT f.feedback_id, 
                   r.room_number, 
                   r.room_type,
                   i.name AS instructor_name,
                   f.rating, 
                   f.comments, 
                   f.feedback_date
            FROM room_feedback f
            LEFT JOIN rooms r ON f.room_id = r.room_id
            LEFT JOIN instructors i ON f.instructor_id = i.instructor_id
            ORDER BY f.feedback_date DESC
        """)
        feedbacks = cursor.fetchall()
    except Exception as e:
        flash("Error loading feedback list.")
        print(f"Error fetching feedbacks: {e}")
        feedbacks = []
    finally:
        conn.close()

    return render_template("admin/feedback.html", feedbacks=feedbacks)

# Route: Delete feedback
@feedback_bp.route('/delete/<int:feedback_id>', methods=['POST'])
@admin_required
def delete_feedback(feedback_id):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM room_feedback WHERE feedback_id = %s", (feedback_id,))
        conn.commit()
        flash("Feedback deleted successfully.")
    except Exception as e:
        flash("An error occurred while deleting feedback.")
        print(f"Error deleting feedback ID {feedback_id}: {e}")
    finally:
        conn.close()

    return redirect(url_for('feedback.list_feedback'))
