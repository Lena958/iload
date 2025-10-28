from flask import Blueprint, render_template, request, session, redirect, url_for
from functools import wraps
import mysql.connector
from contextlib import contextmanager
from datetime import datetime, timedelta, time

# ------------------------
# DB / blueprint
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

load_bp = Blueprint('load', __name__, url_prefix='/view')

@load_bp.context_processor
def inject_instructor_name():
    if 'user_id' not in session:
        return dict(instructor_name=None, instructor_image=None)
    
    with db_cursor(dictionary=True) as cursor:
        cursor.execute(
            "SELECT name, image FROM instructors WHERE instructor_id = %s", 
            (session['user_id'],)
        )
        instructor = cursor.fetchone()

    return dict(
        instructor_name=instructor['name'] if instructor else None,
        instructor_image=instructor['image'] if instructor and instructor['image'] else None
    )


# ------------------------
# helpers
# ------------------------
def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if session.get('role') != 'admin':
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated

def format_time_12hr(time_obj):
    if not time_obj:
        return ""
    if isinstance(time_obj, str):
        try:
            time_obj = datetime.strptime(time_obj, "%H:%M:%S").time()
        except:
            time_obj = datetime.strptime(time_obj, "%H:%M").time()
    elif isinstance(time_obj, timedelta):
        total_seconds = int(time_obj.total_seconds())
        hours = total_seconds // 3600
        minutes = (total_seconds % 3600) // 60
        time_obj = datetime.strptime(f"{hours}:{minutes:02d}", "%H:%M").time()
    return time_obj.strftime("%I:%M %p")

def normalize_day(d):
    if not d:
        return None
    s = str(d).strip().lower()
    map_ = {
        'm': 'Monday','mon':'Monday','monday':'Monday',
        't': 'Tuesday','tue':'Tuesday','tues':'Tuesday','tuesday':'Tuesday',
        'w': 'Wednesday','wed':'Wednesday','wednesday':'Wednesday',
        'th': 'Thursday','thu':'Thursday','thursday':'Thursday',
        'f': 'Friday','fri':'Friday','friday':'Friday',
        'sat':'Saturday','saturday':'Saturday',
        'sun':'Sunday','sunday':'Sunday'
    }
    return map_.get(s, s.capitalize())

def prettify_search_title(raw_title: str) -> str:
    """Format search titles nicely (capitalize words, strip extra spaces)."""
    if not raw_title:
        return ""
    return " ".join(word.capitalize() for word in raw_title.strip().split())

# ------------------------
# sidebar context (instructor name)
# ------------------------
@load_bp.context_processor
def inject_instructor_name():
    if 'user_id' not in session:
        return dict(instructor_name=None)
    with db_cursor(dictionary=True) as cursor:
        cursor.execute("SELECT name FROM instructors WHERE instructor_id = %s", (session['user_id'],))
        r = cursor.fetchone()
    return dict(instructor_name=(r['name'] if r else None))

# ------------------------
# fetch schedules with joined search
# ------------------------
def fetch_all_schedules(search_query=None):
    sql = """
        SELECT sc.schedule_id, sc.day_of_week, sc.start_time, sc.end_time,
               sb.subject_id, sb.code AS subject_code, sb.name AS subject_name, IFNULL(sb.units,0) AS units,
               sb.year_level, sb.section, sb.course,
               ins.instructor_id, ins.name AS instructor_name,
               rm.room_id, rm.room_number, rm.room_type
        FROM schedules sc
        JOIN subjects sb ON sc.subject_id = sb.subject_id
        JOIN instructors ins ON sc.instructor_id = ins.instructor_id
        JOIN rooms rm ON sc.room_id = rm.room_id
        WHERE sc.approved = 1
          AND sb.name IS NOT NULL
          AND sb.code IS NOT NULL
          AND sb.year_level IS NOT NULL
          AND sb.section IS NOT NULL
          AND sb.course IS NOT NULL
          AND ins.name IS NOT NULL
          AND rm.room_number IS NOT NULL
          AND rm.room_type IS NOT NULL
    """
    params = []

    if search_query:
        # normalize: remove dashes, split into keywords
        keywords = search_query.lower().replace("-", " ").split()
        for kw in keywords:
            kw_like = f"%{kw}%"
            sql += """
              AND (
                  LOWER(ins.name) LIKE %s
                  OR LOWER(rm.room_number) LIKE %s
                  OR LOWER(sb.course) LIKE %s
                  OR LOWER(CONCAT(REPLACE(sb.year_level,'-',''), REPLACE(sb.section,'-',''))) LIKE %s
                  OR LOWER(sb.name) LIKE %s
                  OR LOWER(sb.code) LIKE %s
              )
            """
            params.extend([kw_like]*6)

    sql += " ORDER BY FIELD(sc.day_of_week, 'Monday','Tuesday','Wednesday','Thursday','Friday','Saturday','Sunday'), sc.start_time"

    with db_cursor(dictionary=True) as cursor:
        cursor.execute(sql, params)
        rows = cursor.fetchall()

    for r in rows:
        r['day_of_week'] = normalize_day(r.get('day_of_week'))
        if isinstance(r['start_time'], str):
            try:
                r['start_time'] = datetime.strptime(r['start_time'], '%H:%M:%S').time()
            except:
                r['start_time'] = datetime.strptime(r['start_time'], '%H:%M').time()
        if isinstance(r['end_time'], str):
            try:
                r['end_time'] = datetime.strptime(r['end_time'], '%H:%M:%S').time()
            except:
                r['end_time'] = datetime.strptime(r['end_time'], '%H:%M').time()
        r['start_time_12'] = format_time_12hr(r['start_time'])
        r['end_time_12'] = format_time_12hr(r['end_time'])
    return rows

# ------------------------
# view all schedules + search
# ------------------------
@load_bp.route('/', methods=['GET'])
@admin_required
def view_all_schedules():
    q = request.args.get("q", "").strip()
    schedules = fetch_all_schedules(search_query=q if q else None)
    return render_template(
        "schedules/view.html",
        schedules=schedules,
        search_title=q if q else None   # <-- pass to template
    )

# ------------------------
# final grid view (optional timetable)
# ------------------------
@load_bp.route('/final', methods=['GET'])
@admin_required
def view_final_schedule():
    sql = """
        SELECT sc.day_of_week, sc.start_time, sc.end_time,
               sb.code AS subject_code, sb.name AS subject_name, IFNULL(sb.units,0) AS units,
               sb.year_level, sb.section, sb.course,
               ins.name AS instructor_name,
               rm.room_number, rm.room_type
        FROM schedules sc
        JOIN subjects sb ON sc.subject_id = sb.subject_id
        JOIN instructors ins ON sc.instructor_id = ins.instructor_id
        JOIN rooms rm ON sc.room_id = rm.room_id
        WHERE sc.approved = 1
          AND sb.name IS NOT NULL
          AND sb.code IS NOT NULL
          AND sb.year_level IS NOT NULL
          AND sb.section IS NOT NULL
          AND sb.course IS NOT NULL
          AND ins.name IS NOT NULL
          AND rm.room_number IS NOT NULL
          AND rm.room_type IS NOT NULL
    """
    with db_cursor(dictionary=True) as cursor:
        cursor.execute(sql)
        schedules = cursor.fetchall()

    for s in schedules:
        s['day_of_week'] = normalize_day(s['day_of_week'])
        if isinstance(s['start_time'], str):
            try:
                s['start_time'] = datetime.strptime(s['start_time'], '%H:%M:%S').time()
            except:
                s['start_time'] = datetime.strptime(s['start_time'], '%H:%M').time()
        if isinstance(s['end_time'], str):
            try:
                s['end_time'] = datetime.strptime(s['end_time'], '%H:%M:%S').time()
            except:
                s['end_time'] = datetime.strptime(s['end_time'], '%H:%M').time()
        s['start_time_12'] = format_time_12hr(s['start_time'])
        s['end_time_12'] = format_time_12hr(s['end_time'])

    days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
    grid = {d: [] for d in days}
    for s in schedules:
        if s['day_of_week'] in grid:
            grid[s['day_of_week']].append(s)
    time_slots = [{"time": time(h, 0), "label": f"{h:02d}:00"} for h in range(7, 20)]

    return render_template(
        "schedules/view.html",
        grid=grid,
        days=days,
        time_slots=time_slots
    )

# ------------------------
# copy view (searched data only)
# ------------------------
@load_bp.route('/copy', methods=['GET'])
@admin_required
def view_copy():
    q = request.args.get("q", "").strip()
    schedules = fetch_all_schedules(search_query=q if q else None)

    # Normalize start and end times to datetime.time
    for sched in schedules:
        if isinstance(sched['start_time'], timedelta):
            total_seconds = int(sched['start_time'].total_seconds())
            sched['start_time'] = time(total_seconds // 3600, (total_seconds % 3600) // 60)
        if isinstance(sched['end_time'], timedelta):
            total_seconds = int(sched['end_time'].total_seconds())
            sched['end_time'] = time(total_seconds // 3600, (total_seconds % 3600) // 60)

    # Build days and half-hour time slots
    days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    time_slots = []
    for h in range(7, 20):  # 7:00 AM - 7:00 PM
        time_slots.append(time(h, 0))
        time_slots.append(time(h, 30))

    # Initialize grid with placeholders
    grid = {day: [None] * len(time_slots) for day in days}

    for sched in schedules:
        start = sched['start_time']
        end = sched['end_time']
        if not start or not end or sched['day_of_week'] not in days:
            continue

        # Find matching slot indices
        try:
            start_idx = next(i for i, t in enumerate(time_slots) if t >= start)
        except StopIteration:
            continue
        try:
            end_idx = next(i for i, t in enumerate(time_slots) if t > end)
        except StopIteration:
            end_idx = len(time_slots)


        duration = end_idx - start_idx
        if duration <= 0:
            continue

        # Insert schedule into grid
        grid[sched['day_of_week']][start_idx] = {**sched, "rowspan": duration}
        for i in range(start_idx + 1, end_idx):
            grid[sched['day_of_week']][i] = "skip"

    return render_template(
        "schedules/copy.html",
        days=days,
        time_slots=time_slots,
        grid=grid,
        search_title=prettify_search_title(q) if q else None
    )

