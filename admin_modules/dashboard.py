from flask import Blueprint, render_template, session, redirect, url_for
from .admin_routes import is_admin, get_instructor_name, db_config
import mysql.connector

dashboard_bp = Blueprint('dashboard', __name__, url_prefix='/admin')

def get_db_connection():
    return mysql.connector.connect(**db_config)

def is_admin():
    return session.get('role') == 'admin'


# Inject instructor's name for sidebar
@dashboard_bp.context_processor
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

@dashboard_bp.route('/dashboard')
def admin_dashboard():
    if not is_admin():
        return redirect(url_for('login'))

    username = session.get('username')
    instructor_name = get_instructor_name(username)

    stats = {
        "total_instructors": 0,
        "total_rooms": 0,
        "total_subjects": 0,
        "conflicts": 0,
        "schedules": 0,
        "satisfied_feedback": 0,
        "unsatisfied_feedback": 0,
    }

    instructor_load = []
    room_usage = []
    recent_conflicts = []
    top_instructors = []

    try:
        conn = mysql.connector.connect(**db_config)
        cursor = conn.cursor(dictionary=True)

        # Instructors count
        cursor.execute("SELECT COUNT(*) AS cnt FROM instructors")
        stats["total_instructors"] = cursor.fetchone()["cnt"]

        # Rooms count
        cursor.execute("SELECT COUNT(*) AS cnt FROM rooms")
        stats["total_rooms"] = cursor.fetchone()["cnt"]

        # Subjects count
        cursor.execute("SELECT COUNT(*) AS cnt FROM subjects")
        stats["total_subjects"] = cursor.fetchone()["cnt"]

        # Conflicts count
        try:
            cursor.execute("SELECT COUNT(*) AS cnt FROM conflicts")
            stats["conflicts"] = cursor.fetchone()["cnt"]
        except mysql.connector.Error:
            stats["conflicts"] = 0

        # Total schedules
        cursor.execute("SELECT COUNT(*) AS cnt FROM schedules")
        stats["schedules"] = cursor.fetchone()["cnt"]

        # Feedback stats
        cursor.execute(
            "SELECT rating, COUNT(*) AS cnt FROM room_feedback GROUP BY rating"
        )
        for row in cursor.fetchall():
            if row["rating"] == "Satisfied":
                stats["satisfied_feedback"] = row["cnt"]
            elif row["rating"] == "Unsatisfied":
                stats["unsatisfied_feedback"] = row["cnt"]

        # Instructor load (units assigned vs max load)
        cursor.execute("""
            SELECT i.name, i.max_load_units,
                   IFNULL(SUM(sb.units), 0) AS current_units
            FROM instructors i
            LEFT JOIN subjects sb ON i.instructor_id = sb.instructor_id
            GROUP BY i.instructor_id
        """)
        instructor_load = cursor.fetchall()

        # Room usage (count how many schedules per room)
        cursor.execute("""
            SELECT r.room_number, r.room_type, COUNT(sc.schedule_id) AS schedules_count
            FROM rooms r
            LEFT JOIN schedules sc ON r.room_id = sc.room_id
            GROUP BY r.room_id
        """)
        room_usage = cursor.fetchall()

        # Recent conflicts (last 5)
        try:
            cursor.execute("""
                SELECT conflict_type, description, status
                FROM conflicts
                ORDER BY conflict_id DESC
                LIMIT 5
            """)
            recent_conflicts = cursor.fetchall()
        except mysql.connector.Error:
            recent_conflicts = []

        # Top 5 instructors by load utilization
        cursor.execute("""
            SELECT i.instructor_id, i.name, i.image, i.max_load_units,
                   IFNULL(SUM(sb.units), 0) AS current_units
            FROM instructors i
            LEFT JOIN subjects sb ON i.instructor_id = sb.instructor_id
            GROUP BY i.instructor_id
            ORDER BY current_units DESC
            LIMIT 5
        """)
        top_instructors = cursor.fetchall()

       # Top 5 instructors by load utilization
        cursor.execute("""
            SELECT i.instructor_id, i.name, i.image, i.max_load_units,
                IFNULL(SUM(sb.units), 0) AS current_units
            FROM instructors i
            LEFT JOIN subjects sb ON i.instructor_id = sb.instructor_id
            GROUP BY i.instructor_id
            ORDER BY current_units DESC
            LIMIT 4
        """)
        top_instructors = cursor.fetchall()

        # Fetch all subjects per instructor
        for ti in top_instructors:
            cursor.execute("""
                SELECT name
                FROM subjects
                WHERE instructor_id = %s
            """, (ti["instructor_id"],))
            ti["subjects"] = [row["name"] for row in cursor.fetchall()]
    except mysql.connector.Error as err:
        print(f"DB Error (dashboard): {err}")

    finally:
        if 'cursor' in locals() and cursor:
            cursor.close()
        if 'conn' in locals() and conn:
            conn.close()

    return render_template(
        'admin/admin_dashboard.html',
        username=username,
        instructor_name=instructor_name,
        stats=stats,
        instructor_load=instructor_load,
        room_usage=room_usage,
        recent_conflicts=recent_conflicts,
        top_instructors=top_instructors
    )
