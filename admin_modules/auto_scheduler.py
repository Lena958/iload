# auto_scheduler.py
from flask import Blueprint, render_template, request, redirect, url_for, flash, session
import mysql.connector
import random
from collections import deque
from datetime import datetime, timedelta
from .conflicts import detect_and_save_conflicts  # ensure this exists

auto_scheduler_bp = Blueprint('auto_scheduler', __name__, url_prefix='/admin/auto_scheduler')

# ---------- DB config (edit for your environment) ----------
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

@auto_scheduler_bp.context_processor
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
# ---------- Patterns ----------
PATTERNS = {
    'MWF': ['Monday', 'Wednesday', 'Friday'],
    'TTh': ['Tuesday', 'Thursday'],
    'OneDay': ['Monday']
}

def sessions_for_subject(subj):
    """
    Map subject units to pattern and session count.
    units >= 3 -> MWF (3)
    units == 2 -> TTh (2)
    units == 1 -> OneDay (1)
    Default: 3 -> MWF
    """
    try:
        units = int(subj.get('units', 3))
    except Exception:
        units = 3
    if units >= 3:
        return ('MWF', 3)
    elif units == 2:
        return ('TTh', 2)
    else:
        return ('OneDay', 1)

# ---------- Time helpers ----------
def parse_time_str(t):
    if not t:
        return None
    if isinstance(t, str):
        for fmt in ("%H:%M:%S", "%H:%M"):
            try:
                dt = datetime.strptime(t, fmt)
                return dt.strftime("%H:%M")
            except Exception:
                continue
    return None

def intervals_overlap(s1, e1, s2, e2):
    """
    s1,e1,s2,e2 are "HH:MM" strings
    """
    fmt = "%H:%M"
    a1 = datetime.strptime(s1, fmt)
    b1 = datetime.strptime(e1, fmt)
    a2 = datetime.strptime(s2, fmt)
    b2 = datetime.strptime(e2, fmt)
    return not (b1 <= a2 or b2 <= a1)

# ---------- Constraints (operate on groups) ----------
def group_conflicts(group_a, group_b):
    """
    group_a and group_b are lists of session dicts (each session dict contains
    subject_id, instructor_id, room_id, day_of_week, start_time, end_time)
    Return True if groups conflict (cannot coexist), False if they can coexist.
    """
    # For every pair of sessions (one from group_a, one from group_b), check conflicts
    for a in group_a:
        for b in group_b:
            # If same day, check time overlap
            if a['day_of_week'] == b['day_of_week']:
                if intervals_overlap(a['start_time'], a['end_time'], b['start_time'], b['end_time']):
                    # Instructor conflict
                    if a.get('instructor_id') and b.get('instructor_id') and a['instructor_id'] == b['instructor_id']:
                        return True
                    # Room conflict
                    if a.get('room_id') and b.get('room_id') and a['room_id'] == b['room_id']:
                        return True
            # If same subject (shouldn't compare same variable, but just in case), enforce
            if a.get('subject_id') == b.get('subject_id'):
                # must be same instructor and same room and days must be distinct
                if a.get('instructor_id') != b.get('instructor_id'):
                    return True
                if a.get('room_id') != b.get('room_id'):
                    return True
                if a['day_of_week'] == b['day_of_week']:
                    return True
    return False

def groups_compatible(group_a, group_b):
    """
    Return True if groups are compatible (no conflicts), False otherwise.
    We invert group_conflicts for clearer naming.
    """
    return not group_conflicts(group_a, group_b)

# ---------- AC-3 / CSP helpers ----------
def ac3(domains):
    """
    domains: dict var_name -> list of groups (each group = list of sessions)
    """
    queue = deque((xi, xj) for xi in domains for xj in domains if xi != xj)
    while queue:
        xi, xj = queue.popleft()
        if revise(domains, xi, xj):
            if not domains[xi]:
                return False
            for xk in domains:
                if xk != xi and xk != xj:
                    queue.append((xk, xi))
    return True

def revise(domains, xi, xj):
    """
    Remove values from xi that have no compatible value in xj
    """
    revised = False
    to_remove = []
    for val_x in domains[xi]:
        # val_x is a group; need at least one val_y in domains[xj] s.t. compatible
        if not any(groups_compatible(val_x, val_y) for val_y in domains[xj]):
            to_remove.append(val_x)
    for v in to_remove:
        domains[xi].remove(v)
        revised = True
    return revised

def forward_check(assignment, domains, var, value):
    """
    assignment: var -> chosen group
    var: the var just assigned
    value: the chosen group (list of sessions)
    Return backup dict for restoration or False if a domain becomes empty
    """
    backup = {}
    for other_var in domains:
        if other_var in assignment or other_var == var:
            continue
        filtered = [g for g in domains[other_var] if groups_compatible(value, g)]
        if not filtered:
            # restore changed domains
            for dv, vals in backup.items():
                domains[dv] = vals
            return False
        if len(filtered) < len(domains[other_var]):
            backup[other_var] = domains[other_var]
            domains[other_var] = filtered
    return backup

def is_consistent_assignment(assignment, candidate_group):
    """
    candidate_group must be compatible with all groups already assigned.
    """
    for other_group in assignment.values():
        if not groups_compatible(candidate_group, other_group):
            return False
    return True

def select_unassigned_variable(domains, assignment):
    unassigned = [v for v in domains if v not in assignment]
    # Minimum Remaining Values heuristic
    return min(unassigned, key=lambda v: len(domains[v]))

def count_group_sessions(group):
    return len(group)

def backtrack(assignment, domains, instructor_load, max_loads):
    """
    assignment: var -> chosen group
    instructor_load: dict instructor_id -> used load units (sessions)
    max_loads: dict instructor_id -> max load units
    """
    if len(assignment) == len(domains):
        return assignment

    var = select_unassigned_variable(domains, assignment)

    # iterate domain values (groups). randomize for variety
    for group in domains[var]:
        # group is a list of sessions; all sessions share same instructor by construction
        if not group:
            continue
        instr = group[0].get('instructor_id')
        if instr is None:
            continue

        sessions_needed = count_group_sessions(group)
        if instructor_load.get(instr, 0) + sessions_needed > max_loads.get(instr, 0):
            continue

        if is_consistent_assignment(assignment, group):
            # assign
            assignment[var] = group
            instructor_load[instr] = instructor_load.get(instr, 0) + sessions_needed

            backup = forward_check(assignment, domains, var, group)
            if backup is not False:
                result = backtrack(assignment, domains, instructor_load, max_loads)
                if result:
                    return result

            # backtrack
            del assignment[var]
            instructor_load[instr] -= sessions_needed
            if instructor_load[instr] == 0:
                del instructor_load[instr]
            if backup:
                for dv, vals in backup.items():
                    domains[dv] = vals
    return None

# ---------- Time-slot generator (deterministic) ----------
def generate_time_slots_fixed(start_time_dt, end_time_dt, session_length_minutes=90, step_minutes=30):
    slots = []
    current = start_time_dt
    while True:
        slot_end = current + timedelta(minutes=session_length_minutes)
        if slot_end > end_time_dt:
            break
        slots.append((current.strftime("%H:%M"), slot_end.strftime("%H:%M")))
        current = current + timedelta(minutes=step_minutes)
        if current >= end_time_dt:
            break
    return slots

# ---------- Conflicts helper ----------
def get_conflicting_schedule_ids():
    detect_and_save_conflicts()
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT DISTINCT schedule1_id, schedule2_id FROM conflicts")
    rows = cur.fetchall()
    cur.close()
    conn.close()
    ids = set()
    for r in rows:
        if r[0] is not None:
            ids.add(r[0])
        if r[1] is not None:
            ids.add(r[1])
    return list(ids)

# ---------- Routes: home & approve (same checks as before) ----------
@auto_scheduler_bp.route('/')
def auto_scheduler_home():
    if not is_admin():
        return redirect(url_for('login'))

    conn = get_db_connection()
    cur = conn.cursor(dictionary=True)
    query = """
        SELECT sc.schedule_id, sc.day_of_week, sc.start_time, sc.end_time,
               sb.name AS subject_name, sb.year_level, sb.section, sb.course,
               ins.name AS instructor_name,
               rm.room_number, rm.room_type,
               sc.approved
        FROM schedules sc
        LEFT JOIN subjects sb ON sc.subject_id = sb.subject_id
        LEFT JOIN instructors ins ON sc.instructor_id = ins.instructor_id
        LEFT JOIN rooms rm ON sc.room_id = rm.room_id
        WHERE sc.approved IS NULL OR sc.approved = 0 OR sc.approved = '0'
        ORDER BY FIELD(sc.day_of_week, 'Monday','Tuesday','Wednesday','Thursday','Friday'),
                 sc.start_time
    """
    cur.execute(query)
    schedules = cur.fetchall()
    conn.close()

    for s in schedules:
        try:
            s['start_time_12'] = datetime.strptime(str(s['start_time']), "%H:%M:%S").strftime("%I:%M %p")
        except Exception:
            s['start_time_12'] = str(s['start_time'] or '')
        try:
            s['end_time_12'] = datetime.strptime(str(s['end_time']), "%H:%M:%S").strftime("%I:%M %p")
        except Exception:
            s['end_time_12'] = str(s['end_time'] or '')

    conflicting_schedule_ids = get_conflicting_schedule_ids()
    return render_template("admin/auto_scheduler.html", schedules=schedules, conflicting_schedule_ids=conflicting_schedule_ids)

@auto_scheduler_bp.route('/approve/<int:schedule_id>', methods=['POST'])
def approve_schedule(schedule_id):
    if not is_admin():
        return redirect(url_for('login'))

    conn = get_db_connection()
    cur = conn.cursor(dictionary=True)

    cur.execute("SELECT * FROM schedules WHERE schedule_id = %s", (schedule_id,))
    schedule = cur.fetchone()
    if not schedule:
        flash("❌ Schedule not found.", "danger")
        conn.close()
        return redirect(url_for('auto_scheduler.auto_scheduler_home'))

    cur.execute("""
        SELECT * FROM schedules 
        WHERE schedule_id != %s AND (approved = 1 OR approved IS NULL OR approved = 0)
    """, (schedule_id,))
    others = cur.fetchall()

    def _time_conflict(start1, end1, start2, end2):
        fmt = "%H:%M:%S"
        try:
            s1 = datetime.strptime(str(start1), fmt)
            e1 = datetime.strptime(str(end1), fmt)
            s2 = datetime.strptime(str(start2), fmt)
            e2 = datetime.strptime(str(end2), fmt)
        except Exception:
            try:
                fmt2 = "%H:%M"
                s1 = datetime.strptime(str(start1), fmt2)
                e1 = datetime.strptime(str(end1), fmt2)
                s2 = datetime.strptime(str(start2), fmt2)
                e2 = datetime.strptime(str(end2), fmt2)
            except Exception:
                return False
        return not (e1 <= s2 or e2 <= s1)

    conflicts_found = []
    for o in others:
        if schedule['day_of_week'] != o['day_of_week']:
            continue
        if _time_conflict(schedule['start_time'], schedule['end_time'], o['start_time'], o['end_time']):
            if schedule['instructor_id'] and schedule['instructor_id'] == o['instructor_id']:
                conflicts_found.append(f"Conflict with Instructor: Schedule #{o['schedule_id']} on {o['day_of_week']}")
            if schedule['room_id'] and schedule['room_id'] == o['room_id']:
                conflicts_found.append(f"Conflict with Room: Schedule #{o['schedule_id']} on {o['day_of_week']}")

    if conflicts_found:
        flash("❌ Cannot approve schedule" .join(conflicts_found), "danger")
        conn.close()
        return redirect(url_for('auto_scheduler.auto_scheduler_home'))

    cur.execute("UPDATE schedules SET approved = 1 WHERE schedule_id = %s", (schedule_id,))
    conn.commit()
    conn.close()
    flash("✅ Schedule approved successfully.", "success")
    return redirect(url_for('schedules.list_schedules'))

# ---------- Core: generate route (CSP with group domains) ----------
@auto_scheduler_bp.route('/generate', methods=['POST'])
def generate_schedule():
    if not is_admin():
        return redirect(url_for('login'))

    start_time_str = request.form.get("start_time", "07:00")
    end_time_str = request.form.get("end_time", "19:00")
    semester = request.form.get("semester")
    school_year = request.form.get("school_year")

    if not semester or not school_year:
        flash("Semester and school year are required.", "warning")
        return redirect(url_for('auto_scheduler.auto_scheduler_home'))

    try:
        start_time = datetime.strptime(start_time_str, "%H:%M")
        end_time = datetime.strptime(end_time_str, "%H:%M")
        if start_time >= end_time:
            flash("Start time must be earlier than end time.", "warning")
            return redirect(url_for('auto_scheduler.auto_scheduler_home'))
    except ValueError:
        flash("Invalid time format.", "warning")
        return redirect(url_for('auto_scheduler.auto_scheduler_home'))

    # deterministic slots
    time_slots = generate_time_slots_fixed(start_time, end_time, session_length_minutes=90, step_minutes=30)
    if not time_slots:
        flash("No time slots available with given window and session length.", "warning")
        return redirect(url_for('auto_scheduler.auto_scheduler_home'))

    conn = get_db_connection()
    cur = conn.cursor(dictionary=True)

    # fetch subjects that don't have approved schedules for this sem/sy
    cur.execute("""
        SELECT sb.subject_id, sb.name, sb.instructor_id, sb.units
        FROM subjects sb
        LEFT JOIN schedules sc ON sb.subject_id = sc.subject_id AND sc.semester = %s AND sc.school_year = %s AND sc.approved = 1
        WHERE sb.instructor_id IS NOT NULL
          AND (sc.schedule_id IS NULL)
    """, (semester, school_year))
    subjects = cur.fetchall()

    cur.execute("SELECT instructor_id, name, max_load_units FROM instructors")
    instructors = cur.fetchall()

    cur.execute("SELECT room_id, room_number, room_type FROM rooms")
    rooms = cur.fetchall()

    # maps and state
    max_loads = {ins['instructor_id']: int(ins['max_load_units']) for ins in instructors}
    instructor_load = {}
    domains = {}
    skipped_subjects = []

    # Build domain for each subject. Each domain entry is a 'group' (list of sessions),
    # representing the same time across pattern days for that subject.
    for subj in subjects:
        sid = subj['subject_id']
        instr_id = subj.get('instructor_id')
        if not instr_id or instr_id not in max_loads:
            skipped_subjects.append(sid)
            continue

        pattern_name, n_sessions = sessions_for_subject(subj)
        days = PATTERNS.get(pattern_name, PATTERNS['MWF'])[:n_sessions]

        var_name = str(sid)  # one variable per subject (not per session)
        domains[var_name] = []

        # For each room and each time slot, build the whole group (same start/end across days)
        for room in rooms:
            for (start, end) in time_slots:
                # Build sessions list for the group's days
                group = []
                for day in days:
                    session_entry = {
                        'subject_id': sid,
                        'instructor_id': instr_id,
                        'room_id': room['room_id'],
                        'room_type': room['room_type'],
                        'day_of_week': day,
                        'start_time': start,
                        'end_time': end
                    }
                    group.append(session_entry)
                domains[var_name].append(group)

        # shuffle domain to diversify search
        random.shuffle(domains[var_name])

    if skipped_subjects:
        flash(f"Warning: Skipped subjects with missing instructor: {skipped_subjects}", "warning")

    # prune domains with AC-3
    if not ac3(domains):
        flash("AC-3 failed: no valid schedule possible with current inputs.", "danger")
        conn.close()
        return redirect(url_for('auto_scheduler.auto_scheduler_home'))

    # search
    final_assignment = backtrack({}, domains, instructor_load, max_loads)

    if final_assignment:
        # remove previously generated (unapproved) schedules for these subjects this sem/sy
        subject_ids = list(final_assignment.keys())
        if subject_ids:
            fmt = ','.join(['%s'] * len(subject_ids))
            delete_q = f"""
                DELETE FROM schedules
                WHERE subject_id IN ({fmt})
                AND semester = %s AND school_year = %s
                AND (approved IS NULL OR approved = 0)
            """
            cur.execute(delete_q, tuple(subject_ids) + (semester, school_year))

        insert_q = """
            INSERT INTO schedules
            (subject_id, instructor_id, room_id, day_of_week, start_time, end_time, semester, school_year)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """
        for var, group in final_assignment.items():
            # group is list of sessions for the subject
            for s in group:
                cur.execute(insert_q, (
                    s['subject_id'],
                    s['instructor_id'],
                    s['room_id'],
                    s['day_of_week'],
                    s['start_time'],
                    s['end_time'],
                    semester,
                    school_year
                ))
        conn.commit()
        flash("✅ Schedule generated successfully (MWF / TTh groups with same time).", "success")
    else:
        flash("❌ Failed to generate schedule - no valid assignment found.", "danger")

    conn.close()
    return redirect(url_for('auto_scheduler.auto_scheduler_home'))
