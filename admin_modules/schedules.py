from flask import Blueprint, render_template, request, redirect, url_for, flash, session
from functools import wraps
import mysql.connector
from contextlib import contextmanager
from datetime import datetime, timedelta

schedules_bp = Blueprint('schedules', __name__, url_prefix='/admin/schedules')

# ------------------------
# Database Configuration
# ------------------------
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

# ------------------------
# Context Manager for DB
# ------------------------
@contextmanager
def db_cursor(dictionary=False):
    conn = mysql.connector.connect(**db_config)
    cursor = conn.cursor(dictionary=dictionary)
    try:
        yield cursor
        conn.commit()
    finally:
        cursor.close()
        conn.close()

# ------------------------
# Admin Access Control
# ------------------------
def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if session.get('role') != 'admin':
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated

# ------------------------
# Inject Instructor Name
# ------------------------
@schedules_bp.context_processor
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
# ------------------------
# Time Formatting Helpers
# ------------------------
def format_time_12hr(time_obj):
    if not time_obj:
        return ""
    
    if isinstance(time_obj, str):
        time_obj = datetime.strptime(time_obj, "%H:%M:%S").time()
    elif isinstance(time_obj, timedelta):
        total_seconds = int(time_obj.total_seconds())
        hours = total_seconds // 3600
        minutes = (total_seconds % 3600) // 60
        time_obj = datetime.strptime(f"{hours}:{minutes}", "%H:%M").time()
    
    return time_obj.strftime("%I:%M %p")

def format_time_24hr(time_obj):
    if isinstance(time_obj, str):
        time_obj = datetime.strptime(time_obj, "%H:%M:%S").time()
    elif isinstance(time_obj, timedelta):
        total_seconds = int(time_obj.total_seconds())
        hours = total_seconds // 3600
        minutes = (total_seconds % 3600) // 60
        time_obj = datetime.strptime(f"{hours}:{minutes}", "%H:%M").time()
    return time_obj.strftime("%H:%M")

# ------------------------
# Fetch Schedules (All or Filtered)
# ------------------------
def fetch_schedules(approved=None, complete_only=False):
    query = """
        SELECT sc.schedule_id, sc.day_of_week, sc.start_time, sc.end_time,
               sb.subject_id, sb.code AS subject_code, sb.name AS subject_name,
               sb.year_level, sb.section, sb.course,
               ins.instructor_id, ins.name AS instructor_name,
               rm.room_id, rm.room_number, rm.room_type
        FROM schedules sc
        LEFT JOIN subjects sb ON sc.subject_id = sb.subject_id
        LEFT JOIN instructors ins ON sc.instructor_id = ins.instructor_id
        LEFT JOIN rooms rm ON sc.room_id = rm.room_id
    """
    
    conditions = []
    params = []

    if approved is not None:
        conditions.append("sc.approved = %s")
        params.append(approved)

    if complete_only:
        conditions.append("""
            sc.subject_id IS NOT NULL AND sb.subject_id IS NOT NULL AND
            sc.instructor_id IS NOT NULL AND ins.instructor_id IS NOT NULL AND
            sc.room_id IS NOT NULL AND rm.room_id IS NOT NULL AND
            sc.start_time IS NOT NULL AND sc.end_time IS NOT NULL
        """)

    if conditions:
        query += " WHERE " + " AND ".join(conditions)

    query += " ORDER BY sc.day_of_week, sc.start_time"

    with db_cursor(dictionary=True) as cursor:
        cursor.execute(query, tuple(params))
        schedules = cursor.fetchall()

    for sched in schedules:
        sched['start_time_12'] = format_time_12hr(sched['start_time'])
        sched['end_time_12'] = format_time_12hr(sched['end_time'])
    
    return schedules

# ------------------------
# Routes
# ------------------------

@schedules_bp.route('/')
@admin_required
def list_schedules():
    schedules = fetch_schedules(approved=0)
    return render_template("admin/schedules.html", schedules=schedules)

@schedules_bp.route('/view')
@admin_required
def view_all_schedules():
    schedules = fetch_schedules(approved=1, complete_only=True)
    return render_template("schedules/view.html", schedules=schedules)

@schedules_bp.route('/edit/<int:schedule_id>', methods=['GET', 'POST'])
@admin_required
def edit_schedule(schedule_id):
    with db_cursor(dictionary=True) as cursor:
        cursor.execute("SELECT * FROM schedules WHERE schedule_id = %s", (schedule_id,))
        schedule = cursor.fetchone()

        if not schedule:
            flash("Schedule not found", "error")
            return redirect(url_for('schedules.list_schedules'))

        cursor.execute("SELECT * FROM subjects WHERE subject_id = %s", (schedule['subject_id'],))
        subject = cursor.fetchone()

        cursor.execute("SELECT instructor_id, name FROM instructors")
        instructors = cursor.fetchall()

        cursor.execute("SELECT room_id, room_number, room_type FROM rooms")
        rooms = cursor.fetchall()

    schedule['start_time'] = format_time_24hr(schedule['start_time'])
    schedule['end_time'] = format_time_24hr(schedule['end_time'])

    if request.method == 'POST':
        subject_code = request.form['subject_code']
        subject_name = request.form['subject_name']
        course = request.form['course']
        year_level = request.form['year_level']
        section = request.form['section']
        instructor_id = request.form['instructor_id']
        room_id = request.form['room_id']
        day_of_week = request.form['day_of_week']
        start_time = request.form['start_time']
        end_time = request.form['end_time']

        with db_cursor() as cursor:
            cursor.execute("""
                UPDATE subjects
                SET code=%s, name=%s, course=%s, year_level=%s, section=%s
                WHERE subject_id=%s
            """, (subject_code, subject_name, course, year_level, section, schedule['subject_id']))

            cursor.execute("""
                UPDATE schedules
                SET instructor_id=%s, room_id=%s, day_of_week=%s, start_time=%s, end_time=%s
                WHERE schedule_id=%s
            """, (instructor_id, room_id, day_of_week, start_time, end_time, schedule_id))

        flash("Schedule updated successfully", "success")
        return redirect(url_for('schedules.list_schedules'))

    return render_template(
        "admin/edit_schedule.html",
        schedule=schedule,
        subject=subject,
        instructors=instructors,
        rooms=rooms
    )

@schedules_bp.route('/delete/<int:schedule_id>', methods=['POST'])
@admin_required
def delete_schedule(schedule_id):
    with db_cursor() as cursor:
        cursor.execute("DELETE FROM schedules WHERE schedule_id = %s", (schedule_id,))
    flash("Schedule deleted successfully", "success")
    return redirect(url_for('schedules.list_schedules'))

# ------------------------
# Conflict-Aware Approval
# ------------------------
@schedules_bp.route('/approve/<int:schedule_id>', methods=['POST'])
@admin_required
def approve_schedule(schedule_id):
    with db_cursor(dictionary=True) as cursor:
        cursor.execute("SELECT * FROM schedules WHERE schedule_id = %s", (schedule_id,))
        schedule = cursor.fetchone()

        if not schedule:
            flash("❌ Schedule not found.", "error")
            return redirect(url_for('schedules.list_schedules'))

        day = schedule['day_of_week']
        start_time = schedule['start_time']
        end_time = schedule['end_time']
        instructor_id = schedule['instructor_id']
        room_id = schedule['room_id']

        cursor.execute("""
            SELECT s.schedule_id, s.start_time, s.end_time, r.room_number, i.name AS instructor_name
            FROM schedules s
            LEFT JOIN instructors i ON s.instructor_id = i.instructor_id
            LEFT JOIN rooms r ON s.room_id = r.room_id
            WHERE s.schedule_id != %s
              AND s.approved = 1
              AND s.day_of_week = %s
              AND (
                  s.room_id = %s OR s.instructor_id = %s
              )
              AND (
                  (s.start_time < %s AND s.end_time > %s) OR
                  (s.start_time >= %s AND s.start_time < %s) OR
                  (s.end_time > %s AND s.end_time <= %s)
              )
        """, (
            schedule_id, day,
            room_id, instructor_id,
            end_time, start_time,
            start_time, end_time,
            start_time, end_time
        ))

        conflicts = cursor.fetchall()

        if conflicts:
            messages = []
            for c in conflicts:
                messages.append(
                    f"• Conflict with Schedule "
                    f"(Room: {c['room_number']}, Instructor: {c['instructor_name']}, "
                    f"{format_time_12hr(c['start_time'])} - {format_time_12hr(c['end_time'])})"
                )
            detailed_conflict_msg = "<br>".join(messages)
            flash(f"❌ Cannot Approve {detailed_conflict_msg}", "danger")
            return redirect(url_for('schedules.list_schedules'))

        # No conflicts, approve schedule
        cursor.execute("UPDATE schedules SET approved = 1 WHERE schedule_id = %s", (schedule_id,))
        flash("✅ Schedule approved successfully.", "success")

    return redirect(url_for('schedules.list_schedules'))
