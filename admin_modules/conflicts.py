from flask import Blueprint, render_template, request, redirect, url_for, flash, session
import mysql.connector
from datetime import datetime, time, timedelta

conflicts_bp = Blueprint('conflicts', __name__, url_prefix='/admin/conflicts')

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

@conflicts_bp.context_processor
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

def timedelta_to_time(td):
    if isinstance(td, timedelta):
        total_seconds = int(td.total_seconds())
        hours = total_seconds // 3600
        minutes = (total_seconds % 3600) // 60
        seconds = total_seconds % 60
        return time(hour=hours, minute=minutes, second=seconds)
    return td

def parse_time(t):
    if isinstance(t, datetime):
        return t.time()
    if isinstance(t, timedelta):
        return timedelta_to_time(t)
    if isinstance(t, str):
        return datetime.strptime(t, "%H:%M:%S").time()
    return t

def save_conflict_to_db(schedule1_id, schedule2_id, conflict_type, description, recommendation):
    conn = get_db_connection()
    cursor = conn.cursor()

    # Avoid duplicate conflicts for the same schedule pair
    cursor.execute("""
        SELECT COUNT(*) FROM conflicts
        WHERE schedule1_id = %s AND schedule2_id = %s
    """, (schedule1_id, schedule2_id))

    if cursor.fetchone()[0] == 0:
        cursor.execute("""
            INSERT INTO conflicts (schedule1_id, schedule2_id, conflict_type, description, recommendation, status)
            VALUES (%s, %s, %s, %s, %s, 'Unresolved')
        """, (schedule1_id, schedule2_id, conflict_type, description, recommendation))
        conn.commit()

    cursor.close()
    conn.close()

def detect_and_save_conflicts():
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("""
        SELECT sc.schedule_id, sc.day_of_week, sc.start_time, sc.end_time,
               sb.name AS subject_name, sb.year_level, sb.section, sb.course,
               ins.name AS instructor_name, ins.instructor_id,
               rm.room_number, rm.room_type, rm.room_id
        FROM schedules sc
        LEFT JOIN subjects sb ON sc.subject_id = sb.subject_id
        LEFT JOIN instructors ins ON sc.instructor_id = ins.instructor_id
        LEFT JOIN rooms rm ON sc.room_id = rm.room_id
        ORDER BY sc.day_of_week, sc.start_time
    """)
    schedules = cursor.fetchall()
    conn.close()

    for i in range(len(schedules)):
        s1 = schedules[i]
        day1 = s1['day_of_week']
        start1 = parse_time(s1['start_time'])
        end1 = parse_time(s1['end_time'])

        for j in range(i + 1, len(schedules)):
            s2 = schedules[j]
            if s1['day_of_week'] != s2['day_of_week']:
                continue

            start2 = parse_time(s2['start_time'])
            end2 = parse_time(s2['end_time'])

            if start1 < end2 and start2 < end1:
                # Instructor conflict
                if s1['instructor_id'] == s2['instructor_id']:
                    description = (
                        f"Instructor {s1['instructor_name']} has overlapping classes: "
                        f"'{s1['subject_name']}' and '{s2['subject_name']}' on {day1} "
                        f"{start1.strftime('%I:%M %p')} - {end1.strftime('%I:%M %p')} and "
                        f"{start2.strftime('%I:%M %p')} - {end2.strftime('%I:%M %p')}"
                    )
                    recommendation = (
                        f"Reassign one of the overlapping classes for {s1['instructor_name']} "
                        f"to another instructor or move it to a different time."
                    )
                    save_conflict_to_db(s1['schedule_id'], s2['schedule_id'],
                                        "Instructor Double Booking", description, recommendation)

                # Room conflict
                if s1['room_id'] == s2['room_id']:
                    description = (
                        f"Room {s1['room_number']} has overlapping classes: "
                        f"'{s1['subject_name']}' and '{s2['subject_name']}' on {day1} "
                        f"{start1.strftime('%I:%M %p')} - {end1.strftime('%I:%M %p')} and "
                        f"{start2.strftime('%I:%M %p')} - {end2.strftime('%I:%M %p')}"
                    )
                    recommendation = (
                        f"Move one of the classes to another available room or adjust the schedule."
                    )
                    save_conflict_to_db(s1['schedule_id'], s2['schedule_id'],
                                        "Room Double Booking", description, recommendation)

@conflicts_bp.route('/')
def list_conflicts():
    if not is_admin():
        return redirect(url_for('login'))

    # Re-run detection so table always reflects current conflicts
    detect_and_save_conflicts()

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("""
        SELECT c.*, 
               s1.subject_id AS s1_subject_id, s1.start_time AS s1_start, s1.end_time AS s1_end,
               s2.subject_id AS s2_subject_id, s2.start_time AS s2_start, s2.end_time AS s2_end
        FROM conflicts c
        JOIN schedules s1 ON c.schedule1_id = s1.schedule_id
        JOIN schedules s2 ON c.schedule2_id = s2.schedule_id
        ORDER BY c.conflict_id DESC
    """)
    conflicts = cursor.fetchall()
    cursor.close()
    conn.close()

    return render_template("admin/conflicts.html", conflicts=conflicts)

@conflicts_bp.route('/resolve/<int:conflict_id>', methods=['POST'])
def resolve_conflict(conflict_id):
    if not is_admin():
        return redirect(url_for('login'))

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE conflicts SET status = 'Resolved' WHERE conflict_id = %s", (conflict_id,))
    conn.commit()
    cursor.close()
    conn.close()

    flash(f"Conflict #{conflict_id} marked as resolved.")
    return redirect(url_for('conflicts.list_conflicts'))
