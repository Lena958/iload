import os
import re
from flask import Blueprint, render_template, session, redirect, url_for, request, flash, current_app
import mysql.connector
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, time, timedelta

instructor_bp = Blueprint('instructor', __name__, url_prefix='/instructor')

db_config = {
    'host': 'localhost',
    'user': 'root',
    'password': '',
    'database': 'iload'
}

ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}

ALL_DAYS = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']

def format_time_12hr(time_obj):
    return time_obj.strftime('%I:%M %p')

def normalize_day(day_str):
    return day_str.capitalize()

def is_instructor():
    return session.get('role') == 'instructor'

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def get_instructor_name(username):
    try:
        connection = mysql.connector.connect(**db_config)
        cursor = connection.cursor(dictionary=True)
        query = "SELECT name FROM instructors WHERE username = %s"
        cursor.execute(query, (username,))
        result = cursor.fetchone()
        return result['name'] if result else None
    except mysql.connector.Error as err:
        print(f"Database error: {err}")
        return None
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()


def get_db_connection():
    return mysql.connector.connect(**db_config)

def is_admin():
    return session.get('role') == 'instructor'

# Inject instructor's name for sidebar
@instructor_bp.context_processor
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



@instructor_bp.route('/profile', methods=['GET', 'POST'])
def profile():
    if not is_instructor():
        return redirect(url_for('login'))

    username = session.get('username')

    try:
        connection = mysql.connector.connect(**db_config)
        cursor = connection.cursor(dictionary=True)

        if request.method == 'POST':
            new_name = request.form.get('name')
            new_department = request.form.get('department')
            new_max_load = request.form.get('max_load_units')

            current_password = request.form.get('current_password')
            new_password = request.form.get('new_password')
            confirm_password = request.form.get('confirm_password')

            image_file = request.files.get('image')
            image_filename = None

            if image_file and image_file.filename != '':
                if allowed_file(image_file.filename):
                    filename = secure_filename(image_file.filename)
                    upload_folder = os.path.join(current_app.root_path, 'static/uploads')
                    os.makedirs(upload_folder, exist_ok=True)
                    image_path = os.path.join(upload_folder, filename)
                    image_file.save(image_path)
                    image_filename = filename
                else:
                    flash('Invalid image format! Allowed types: png, jpg, jpeg, gif', 'danger')
                    return redirect(url_for('instructor.profile'))

            hashed_password = None
            if current_password or new_password or confirm_password:
                cursor.execute("SELECT password FROM instructors WHERE username = %s", (username,))
                user = cursor.fetchone()
                if not user:
                    flash('User not found.', 'danger')
                    return redirect(url_for('instructor.profile'))
                if not current_password or not check_password_hash(user['password'], current_password):
                    flash('Current password is incorrect.', 'danger')
                    return redirect(url_for('instructor.profile'))
                if new_password != confirm_password:
                    flash('New password and confirmation do not match.', 'danger')
                    return redirect(url_for('instructor.profile'))

                def is_valid_password(pw):
                    if len(pw) < 8:
                        return False
                    if not re.search(r'[A-Z]', pw):
                        return False
                    if not re.search(r'[a-z]', pw):
                        return False
                    if not re.search(r'[0-9]', pw):
                        return False
                    if not re.search(r'[!@#$%^&*(),.?":{}|<>]', pw):
                        return False
                    return True

                if not is_valid_password(new_password):
                    flash('Password must be at least 8 characters long and include uppercase, lowercase, number, and special symbol.', 'danger')
                    return redirect(url_for('instructor.profile'))

                hashed_password = generate_password_hash(new_password)

            update_fields = ['name = %s', 'department = %s', 'max_load_units = %s']
            update_values = [new_name, new_department, new_max_load]

            if image_filename:
                update_fields.append('image = %s')
                update_values.append(image_filename)

            if hashed_password:
                update_fields.append('password = %s')
                update_values.append(hashed_password)

            update_values.append(username)

            update_query = f"UPDATE instructors SET {', '.join(update_fields)} WHERE username = %s"
            cursor.execute(update_query, tuple(update_values))
            connection.commit()

            flash('Profile updated successfully!', 'success')
            return redirect(url_for('instructor.profile'))
        else:
            select_query = "SELECT name, department, max_load_units, username, image FROM instructors WHERE username = %s"
            cursor.execute(select_query, (username,))
            instructor = cursor.fetchone()
            if not instructor:
                flash('Instructor profile not found.', 'danger')
                return redirect(url_for('login'))

            return render_template(
                'instructor/profile.html',
                instructor_name=instructor['name'],
                instructor_username=instructor['username'],
                instructor_department=instructor['department'],
                instructor_max_load=instructor['max_load_units'],
                instructor_image=instructor['image']
            )
    except mysql.connector.Error as err:
        print(f"Database error: {err}")
        flash('An error occurred while processing your request.', 'danger')
        return redirect(url_for('instructor.profile'))
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

def timedelta_to_time(td):
    if isinstance(td, timedelta):
        total_seconds = int(td.total_seconds())
        hours = total_seconds // 3600
        minutes = (total_seconds % 3600) // 60
        seconds = total_seconds % 60
        return time(hour=hours, minute=minutes, second=seconds)
    return td

def generate_fixed_time_slots():
    """Generate fixed half-hour time slots from 07:00 AM to 07:30 PM."""
    start = datetime.strptime("07:00 AM", "%I:%M %p")
    end = datetime.strptime("07:30 PM", "%I:%M %p")
    slots = []
    while start <= end:
        slots.append(start.time())
        start += timedelta(minutes=30)
    return slots

def build_schedule_grid(schedules):
    # Always include all days
    days = ALL_DAYS

    # Fixed time slots 7:00 AM to 7:30 PM
    time_slots = generate_fixed_time_slots()

    # Initialize grid
    grid = {day: [None] * len(time_slots) for day in days}

    for s in schedules:
        day = s['day_of_week']
        if day not in grid:
            continue

        start_dt = datetime.combine(datetime.today(), s['start_time'])
        end_dt = datetime.combine(datetime.today(), s['end_time'])

        # Find start index
        start_idx = None
        for i, t in enumerate(time_slots):
            if t >= start_dt.time():
                start_idx = i
                break
        if start_idx is None:
            continue

        # Find end index (make sure it includes the ending half-hour)
        end_idx = None
        for i, t in enumerate(time_slots):
            if t >= end_dt.time():
                end_idx = i
                break
        if end_idx is None:
            end_idx = len(time_slots) - 1

        rowspan = max(1, end_idx - start_idx)

        # Place the schedule in the grid
        grid[day][start_idx] = {
            'subject_code': s['subject_code'],
            'subject_name': s['subject_name'],
            'year_level': s['year_level'],
            'section': s['section'],
            'course': s['course'],
            'room_number': s['room_number'],
            'room_type': s['room_type'],
            'start_time': s['start_time'],
            'end_time': s['end_time'],
            'rowspan': rowspan
        }

        # Mark skipped slots
        for i in range(start_idx + 1, start_idx + rowspan):
            if i < len(time_slots):
                grid[day][i] = 'skip'

    return days, time_slots, grid

@instructor_bp.route('/schedule', methods=['GET'])
def view_my_schedule():
    if not is_instructor():
        return redirect(url_for('login'))

    instructor_id = session.get('user_id')

    connection = None
    cursor = None

    try:
        connection = mysql.connector.connect(**db_config)
        cursor = connection.cursor(dictionary=True)

        sql = """
            SELECT sc.day_of_week, sc.start_time, sc.end_time,
                   sb.code AS subject_code, sb.name AS subject_name,
                   sb.year_level, sb.section, sb.course,
                   rm.room_number, rm.room_type,
                   ins.name AS instructor_name
            FROM schedules sc
            JOIN subjects sb ON sc.subject_id = sb.subject_id
            JOIN rooms rm ON sc.room_id = rm.room_id
            JOIN instructors ins ON sc.instructor_id = ins.instructor_id
            WHERE sc.instructor_id = %s
              AND sc.approved = 1
            ORDER BY FIELD(sc.day_of_week, 'Monday','Tuesday','Wednesday','Thursday','Friday','Saturday','Sunday'),
                     sc.start_time
        """
        cursor.execute(sql, (instructor_id,))
        schedules = cursor.fetchall()

        # Get instructor name
        instructor_name = None
        if schedules:
            instructor_name = schedules[0]['instructor_name']
        else:
            cursor.execute("SELECT name FROM instructors WHERE instructor_id = %s", (instructor_id,))
            result = cursor.fetchone()
            instructor_name = result['name'] if result else None

        # Format times
        for s in schedules:
            s['start_time'] = timedelta_to_time(s['start_time'])
            s['end_time'] = timedelta_to_time(s['end_time'])

            if isinstance(s['start_time'], str):
                try:
                    s['start_time'] = datetime.strptime(s['start_time'], '%H:%M:%S').time()
                except Exception as e:
                    print(f"Start time parse error: {e}")

            if isinstance(s['end_time'], str):
                try:
                    s['end_time'] = datetime.strptime(s['end_time'], '%H:%M:%S').time()
                except Exception as e:
                    print(f"End time parse error: {e}")

            s['start_time_12'] = format_time_12hr(s['start_time'])
            s['end_time_12'] = format_time_12hr(s['end_time'])
            s['day_of_week'] = normalize_day(s['day_of_week'])

        days, time_slots, grid = build_schedule_grid(schedules)

        return render_template(
            'instructor/instructor_schedule.html',
            schedules=schedules,
            instructor_name=instructor_name,
            days=days,
            time_slots=time_slots,
            grid=grid
        )

    except mysql.connector.Error as err:
        print(f"Database error: {err}")
        flash("An error occurred while fetching your schedule.", "danger")
        # Redirect to the dashboard using the correct blueprint
        return redirect(url_for('instructor_dashboard.dashboard'))

    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()
