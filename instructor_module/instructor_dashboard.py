from flask import Blueprint, render_template, session, redirect, url_for
from .instructor_bp import is_instructor, get_instructor_name, db_config
import mysql.connector
from datetime import datetime

# Blueprint
instructor_dashboard_bp = Blueprint(
    'instructor_dashboard', __name__, url_prefix='/instructor'
)

def get_db_connection():
    return mysql.connector.connect(**db_config)

def is_admin():
    return session.get('role') == 'instructor'

# Inject instructor's name for sidebar
@instructor_dashboard_bp.context_processor
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

@instructor_dashboard_bp.route('/dashboard')
def dashboard():
    if not is_instructor():
        return redirect(url_for('login'))

    username = session.get('username')
    instructor_name = get_instructor_name(username)

    stats = {
        "total_subjects": 0,
        "scheduled_classes": 0,
        "available_rooms": 0,
    }

    todays_schedule = []
    upcoming_classes = []
    room_feedback = []
    load_summary = {}

    try:
        conn = mysql.connector.connect(**db_config)
        cursor = conn.cursor(dictionary=True)

        # Get instructor_id from username
        cursor.execute("SELECT instructor_id FROM instructors WHERE username = %s", (username,))
        instr = cursor.fetchone()
        instructor_id = instr["instructor_id"] if instr else None

        if instructor_id:
            # Total subjects
            cursor.execute(
                "SELECT COUNT(*) AS cnt FROM subjects WHERE instructor_id = %s",
                (instructor_id,),
            )
            stats["total_subjects"] = cursor.fetchone()["cnt"]

            # Scheduled classes
            cursor.execute("""
                SELECT COUNT(*) AS cnt
                FROM schedules sc
                INNER JOIN subjects sb ON sc.subject_id = sb.subject_id
                WHERE sb.instructor_id = %s
            """, (instructor_id,))
            stats["scheduled_classes"] = cursor.fetchone()["cnt"]

            # Available rooms (not occupied right now)
            now = datetime.now().strftime("%H:%M:%S")
            cursor.execute("""
                SELECT COUNT(*) AS cnt
                FROM rooms r
                WHERE r.room_id NOT IN (
                    SELECT sc.room_id
                    FROM schedules sc
                    WHERE sc.day_of_week = DAYNAME(NOW())
                      AND %s BETWEEN sc.start_time AND sc.end_time
                )
            """, (now,))
            stats["available_rooms"] = cursor.fetchone()["cnt"]

            # Today's schedule
            cursor.execute("""
                SELECT CONCAT(TIME_FORMAT(sc.start_time, '%h:%i %p'), ' - ', TIME_FORMAT(sc.end_time, '%h:%i %p')) AS time,
                       sb.name AS subject,
                       r.room_number AS room
                FROM schedules sc
                INNER JOIN subjects sb ON sc.subject_id = sb.subject_id
                INNER JOIN rooms r ON sc.room_id = r.room_id
                WHERE sb.instructor_id = %s
                  AND sc.day_of_week = DAYNAME(NOW())
                ORDER BY sc.start_time
            """, (instructor_id,))
            todays_schedule = cursor.fetchall()

            # Upcoming classes (next 5 scheduled in the week, ordered by day and time)
            cursor.execute("""
                SELECT sc.day_of_week AS day,
                       CONCAT(TIME_FORMAT(sc.start_time, '%h:%i %p'), ' - ', TIME_FORMAT(sc.end_time, '%h:%i %p')) AS time,
                       sb.name AS subject,
                       r.room_number AS room
                FROM schedules sc
                INNER JOIN subjects sb ON sc.subject_id = sb.subject_id
                INNER JOIN rooms r ON sc.room_id = r.room_id
                WHERE sb.instructor_id = %s
                ORDER BY FIELD(sc.day_of_week,
                      'Monday','Tuesday','Wednesday','Thursday','Friday','Saturday','Sunday'),
                      sc.start_time
                LIMIT 5
            """, (instructor_id,))
            upcoming_classes = cursor.fetchall()

            # Load summary (units assigned vs max load)
            cursor.execute("""
                SELECT i.max_load_units,
                       IFNULL(SUM(sb.units), 0) AS current_units
                FROM instructors i
                LEFT JOIN subjects sb ON i.instructor_id = sb.instructor_id
                WHERE i.instructor_id = %s
                GROUP BY i.instructor_id
            """, (instructor_id,))
            load_summary = cursor.fetchone() or {}

            # Room feedback left by this instructor
            cursor.execute("""
                SELECT rf.room_id, rf.rating, rf.comment, rf.created_at, r.room_number
                FROM room_feedback rf
                INNER JOIN rooms r ON rf.room_id = r.room_id
                WHERE rf.instructor_id = %s
                ORDER BY rf.created_at DESC
                LIMIT 5
            """, (instructor_id,))
            room_feedback = cursor.fetchall()

    except mysql.connector.Error as err:
        print(f"DB Error (instructor dashboard): {err}")

    finally:
        if 'cursor' in locals() and cursor:
            cursor.close()
        if 'conn' in locals() and conn:
            conn.close()

    return render_template(
        'instructor/instructor_dashboard.html',
        instructor_name=instructor_name,
        stats=stats,
        todays_schedule=todays_schedule,
        upcoming_classes=upcoming_classes,
        load_summary=load_summary,
        room_feedback=room_feedback
    )
