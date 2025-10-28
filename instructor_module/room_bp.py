from flask import Blueprint, render_template, g, request, redirect, url_for, flash, session
from db import get_db_connection
from datetime import datetime, time, timedelta
import mysql.connector
from .instructor_bp import is_instructor, get_instructor_name, db_config
from flask import render_template


room_bp = Blueprint('room', __name__, url_prefix='/rooms')

def get_db_connection():
    return mysql.connector.connect(**db_config)

def is_admin():
    return session.get('role') == 'instructor'

# Inject instructor's name for sidebar
@room_bp.context_processor
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

@room_bp.route('/')
def list_rooms():
    instructor_id = session.get('user_id')
    instructor_name = g.get('instructor_name')

    if not instructor_id:
        flash("Instructor ID missing from session.")
        return redirect(url_for('home'))

    conn = get_db_connection()
    try:
        cursor = conn.cursor(dictionary=True, buffered=True)

        # Fetch rooms assigned to this instructor including image
        cursor.execute(
            """
            SELECT DISTINCT r.room_id, r.room_number, r.room_type, r.image
            FROM rooms r
            JOIN schedules s ON r.room_id = s.room_id
            WHERE s.instructor_id = %s
            """,
            (instructor_id,)
        )
        rooms = cursor.fetchall() or []

        # Fetch all feedback/comments for these rooms, including feedback_date
        cursor.execute(
            """
            SELECT room_id, rating, comments, feedback_date
            FROM room_feedback
            WHERE instructor_id = %s
            ORDER BY feedback_date DESC
            """,
            (instructor_id,)
        )
        feedback_rows = cursor.fetchall()

        # Organize feedback per room
        feedback_dict = {}
        for fb in feedback_rows:
            feedback_dict.setdefault(fb['room_id'], []).append(fb)

    finally:
        cursor.close()
        conn.close()

    return render_template(
        'instructor/room.html',
        rooms=rooms,
        instructor_name=instructor_name,
        feedback=feedback_dict
    )


@room_bp.route('/feedback/<int:room_id>', methods=['POST'])
def submit_feedback(room_id):
    instructor_id = session.get('user_id')

    if not instructor_id:
        flash("Instructor ID missing from session.")
        return redirect(url_for('home'))

    rating = request.form.get('satisfaction')
    comments = request.form.get('comments', '').strip()

    if not rating and not comments:
        flash('Please select a rating or write a comment.')
        return redirect(url_for('room.list_rooms'))

    conn = get_db_connection()
    try:
        cursor = conn.cursor(dictionary=True, buffered=True)

        # Ensure room belongs to this instructor
        cursor.execute(
            "SELECT room_id FROM schedules WHERE room_id = %s AND instructor_id = %s",
            (room_id, instructor_id)
        )
        room = cursor.fetchone()
        if not room:
            flash('You cannot submit feedback for this room.')
            return redirect(url_for('room.list_rooms'))

        # Insert feedback/comment
        cursor.execute(
            """
            INSERT INTO room_feedback (room_id, rating, instructor_id, comments, feedback_date)
            VALUES (%s, %s, %s, %s, NOW())
            """,
            (room_id, rating if rating else None, instructor_id, comments)
        )
        conn.commit()
        flash('Feedback/comment submitted successfully!')

    finally:
        cursor.close()
        conn.close()

    return redirect(url_for('room.list_rooms'))


from datetime import time, timedelta

@room_bp.route('/availability')
def view_availability():
    conn = get_db_connection()
    try:
        cursor = conn.cursor(dictionary=True)

        # Fetch all rooms
        cursor.execute("SELECT room_id, room_number, room_type FROM rooms")
        rooms = cursor.fetchall()

        # Fetch all schedules with subject and instructor info
        cursor.execute("""
            SELECT s.room_id, s.subject_id, s.instructor_id, s.day_of_week, s.start_time, s.end_time,
                   i.name AS instructor_name,
                   sub.code AS subject_code, sub.section, sub.year_level
            FROM schedules s
            JOIN instructors i ON s.instructor_id = i.instructor_id
            JOIN subjects sub ON s.subject_id = sub.subject_id
        """)
        schedules = cursor.fetchall()

        # Convert timedelta to datetime.time if necessary
        for s in schedules:
            for key in ['start_time', 'end_time']:
                if isinstance(s[key], timedelta):
                    total_seconds = s[key].total_seconds()
                    s[key] = time(hour=int(total_seconds // 3600),
                                  minute=int((total_seconds % 3600) // 60))

        # Initialize availability dict
        days_of_week = ['Monday','Tuesday','Wednesday','Thursday','Friday','Saturday','Sunday']
        availability = {r['room_id']: {day: [] for day in days_of_week} for r in rooms}

        # Define 30-min time blocks from 08:00 to 16:30
        time_blocks = [time(hour=h, minute=m) for h in range(8, 17) for m in (0,30)]

        # Build availability with merged schedules
        for r in rooms:
            for day in days_of_week:
                i = 0
                while i < len(time_blocks):
                    t = time_blocks[i]
                    # Find a schedule starting at this block
                    sched = next((s.copy() for s in schedules
                                  if s['room_id'] == r['room_id']
                                  and s['day_of_week'] == day
                                  and s['start_time'] == t), None)
                    if sched:
                        # Convert times to string
                        sched['start_time'] = sched['start_time'].strftime('%H:%M')
                        sched['end_time'] = sched['end_time'].strftime('%H:%M')

                        # Calculate rowspan
                        start_h, start_m = map(int, sched['start_time'].split(':'))
                        end_h, end_m = map(int, sched['end_time'].split(':'))
                        start_total = start_h*60 + start_m
                        end_total = end_h*60 + end_m
                        block_count = max(1, (end_total - start_total)//30)

                        sched['rowspan'] = block_count
                        availability[r['room_id']][day].append({'type':'schedule', 'data':sched})
                        i += block_count
                    else:
                        # Free slot
                        availability[r['room_id']][day].append({'type':'free', 'time': t})
                        i += 1

    finally:
        cursor.close()
        conn.close()

    return render_template(
        'instructor/room_availability.html',
        rooms=rooms,
        availability=availability,
        days_of_week=days_of_week,
        time_blocks=time_blocks
    )