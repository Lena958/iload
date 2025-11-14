# auto_scheduler.py
from flask import Blueprint, render_template, request, redirect, url_for, flash, session
import mysql.connector
import random
from collections import deque
from datetime import datetime, timedelta
from .conflicts import detect_and_save_conflicts  # ensure this exists

# --- New imports for performance improvements
from functools import lru_cache
from concurrent.futures import ThreadPoolExecutor, as_completed
import time

auto_scheduler_bp = Blueprint('auto_scheduler', __name__, url_prefix='/admin/auto_scheduler')

# ---------- DB config ----------
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

# Memoized interval check
@lru_cache(maxsize=None)
def _intervals_overlap_cached(s1, e1, s2, e2):
    fmt = "%H:%M"
    a1 = datetime.strptime(s1, fmt)
    b1 = datetime.strptime(e1, fmt)
    a2 = datetime.strptime(s2, fmt)
    b2 = datetime.strptime(e2, fmt)
    return not (b1 <= a2 or b2 <= a1)

def intervals_overlap(s1, e1, s2, e2):
    # normalize inputs to strings like "HH:MM"
    return _intervals_overlap_cached(s1, e1, s2, e2)


# ---------- Conflict check ----------
# We'll memoize group compatibility by turning groups into an immutable representation
def _group_to_key(group):
    # Each session becomes a tuple of stable fields. Order matters for pairwise compare, so keep list order.
    return tuple(
        (int(session.get('subject_id') or 0),
         int(session.get('instructor_id') or 0),
         int(session.get('room_id') or 0),
         session.get('room_type') or '',
         session.get('day_of_week') or '',
         session.get('start_time') or '',
         session.get('end_time') or '')
        for session in group
    )

@lru_cache(maxsize=None)
def _groups_compatible_cached(key_a, key_b):
    # key_a and key_b are tuples produced by _group_to_key
    for a in key_a:
        for b in key_b:
            day_a = a[4]; day_b = b[4]
            if day_a == day_b:
                if _intervals_overlap_cached(a[5], a[6], b[5], b[6]):
                    # same instructor or same room at overlapping time is conflict
                    if a[1] == b[1]:
                        return False
                    if a[2] == b[2]:
                        return False
            # additional subject-level mismatch checks (preserve original logic)
            if a[0] == b[0]:
                # same subject assigned to different instructor or different room or same day conflict
                if a[1] != b[1]:
                    return False
                if a[2] != b[2]:
                    return False
                if day_a == day_b:
                    return False
    return True

def group_conflicts(group_a, group_b):
    # Keep original semantics: return True if conflict exists
    ka = _group_to_key(group_a)
    kb = _group_to_key(group_b)
    compatible = _groups_compatible_cached(ka, kb)
    return not compatible

def groups_compatible(group_a, group_b):
    return not group_conflicts(group_a, group_b)


# ---------- CSP helpers ----------
def ac3(domains, trim_large_domains=True):
    # Build initial queue. Trim comparisons from very large domains if desired.
    keys = list(domains.keys())
    if trim_large_domains:
        queue = deque((xi, xj) for xi in keys for xj in keys if xi != xj and len(domains[xi]) < 40)
    else:
        queue = deque((xi, xj) for xi in keys for xj in keys if xi != xj)

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
    revised = False
    # domains contain lists of groups (lists of session dicts)
    to_remove = []
    for val_x in domains[xi]:
        # check if there exists any val_y in domains[xj] compatible with val_x
        found = False
        for val_y in domains[xj]:
            if groups_compatible(val_x, val_y):
                found = True
                break
        if not found:
            to_remove.append(val_x)
    for v in to_remove:
        try:
            domains[xi].remove(v)
            revised = True
        except ValueError:
            # if someone else already removed it, ignore
            pass
    return revised

def forward_check(assignment, domains, var, value):
    backup = {}
    for other_var in domains:
        if other_var in assignment or other_var == var:
            continue
        filtered = [g for g in domains[other_var] if groups_compatible(value, g)]
        if not filtered:
            # restore backups if failure
            for dv, vals in backup.items():
                domains[dv] = vals
            return False
        if len(filtered) < len(domains[other_var]):
            backup[other_var] = domains[other_var]
            domains[other_var] = filtered
    return backup


# ---------- Consistency check ----------
def is_consistent_assignment(assignment, candidate_group):
    for other_group in assignment.values():
        if not groups_compatible(candidate_group, other_group):
            return False

    # --- Additional rule for part-time instructors (spread loads across days)
    instr = candidate_group[0]['instructor_id']
    if instructor_status.get(instr, '') == 'part time':
        assigned_days = set()
        for grp in assignment.values():
            if grp[0]['instructor_id'] == instr:
                assigned_days.update([s['day_of_week'] for s in grp])
        new_days = [s['day_of_week'] for s in candidate_group]
        all_days = assigned_days.union(new_days)
        if len(all_days) == 1:  # all classes in one day
            return False
    return True


def select_unassigned_variable(domains, assignment):
    unassigned = [v for v in domains if v not in assignment]
    # MRV heuristic: pick variable with smallest domain
    return min(unassigned, key=lambda v: len(domains[v]))


def count_group_sessions(group):
    return len(group)


def backtrack(assignment, domains, instructor_load, max_loads):
    if len(assignment) == len(domains):
        return assignment

    var = select_unassigned_variable(domains, assignment)
    # variable value ordering: try smaller groups first (less load), and shuffle to diversify
    domain_vals = domains[var]
    # sort by group size ascending to prefer smaller session-count options
    domain_vals = sorted(domain_vals, key=count_group_sessions)
    for group in domain_vals:
        if not group:
            continue
        instr = group[0].get('instructor_id')
        if instr is None:
            continue

        sessions_needed = count_group_sessions(group)
        if instructor_load.get(instr, 0) + sessions_needed > max_loads.get(instr, 0):
            continue

        # Early prune: check consistency before forward checking
        if not is_consistent_assignment(assignment, group):
            continue

        assignment[var] = group
        instructor_load[instr] = instructor_load.get(instr, 0) + sessions_needed

        backup = forward_check(assignment, domains, var, group)
        if backup is not False:
            result = backtrack(assignment, domains, instructor_load, max_loads)
            if result:
                return result

        # rollback
        del assignment[var]
        instructor_load[instr] -= sessions_needed
        if instructor_load[instr] == 0:
            del instructor_load[instr]
        if backup:
            for dv, vals in backup.items():
                domains[dv] = vals
    return None


# ---------- Time slots ----------
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


# ---------- Conflicts ----------
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


# ---------- Routes ----------
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


@auto_scheduler_bp.route('/generate', methods=['POST'])
def generate_schedule():
    global instructor_status

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

    # --- Generate both 60-min and 90-min slots
    slots_60 = generate_time_slots_fixed(start_time, end_time, session_length_minutes=60, step_minutes=30)
    slots_90 = generate_time_slots_fixed(start_time, end_time, session_length_minutes=90, step_minutes=30)

    # merge and deduplicate
    time_slots = []
    seen = set()
    for s, e in slots_60 + slots_90:
        key = f"{s}-{e}"
        if key not in seen:
            seen.add(key)
            time_slots.append((s, e))

    if not time_slots:
        flash("No time slots available.", "warning")
        return redirect(url_for('auto_scheduler.auto_scheduler_home'))

    conn = get_db_connection()
    cur = conn.cursor(dictionary=True)

    # --- JOIN subjects with courses to get course_type
    cur.execute("""
        SELECT sb.subject_id, sb.name, sb.code, sb.instructor_id, sb.units, sb.course,
               c.course_type
        FROM subjects sb
        LEFT JOIN courses c ON sb.code = c.course_code
        LEFT JOIN schedules sc ON sb.subject_id = sc.subject_id 
            AND sc.semester = %s AND sc.school_year = %s AND sc.approved = 1
        WHERE sb.instructor_id IS NOT NULL
          AND (sc.schedule_id IS NULL)
    """, (semester, school_year))
    subjects = cur.fetchall()

    cur.execute("SELECT instructor_id, name, status, max_load_units FROM instructors")
    instructors = cur.fetchall()

    cur.execute("SELECT room_id, room_number, room_type FROM rooms")
    rooms = cur.fetchall()

    # --- Room-to-program mapping
    cur.execute("SELECT room_id, program_name FROM room_programs")
    room_program_rows = cur.fetchall()
    room_programs_map = {}
    for rp in room_program_rows:
        rid = rp['room_id']
        pname = (rp['program_name'] or '').strip().upper()
        room_programs_map.setdefault(rid, []).append(pname)

    max_loads = {ins['instructor_id']: int(ins['max_load_units']) for ins in instructors}
    instructor_status = {ins['instructor_id']: (str(ins.get('status', '') or '')).lower() for ins in instructors}

    instructor_load = {}
    domains = {}
    skipped_subjects = []

    # map logical types to DB values
    ROOM_TYPE_MAP = {
        'lecture': 'Lecture',
        'laboratory': 'Lab',
        'lab': 'Lab'
    }

    # Control randomness for reproducibility during testing
    try:
        random.seed(time.time())
    except Exception:
        random.seed(0)

    # ---------- Helper for building domain for one subject (used for parallel build) ----------
    def build_domain_for_subject(subj):
        sid = subj['subject_id']
        instr_id = subj.get('instructor_id')
        local_domain = []
        if not instr_id or instr_id not in max_loads:
            return str(sid), local_domain  # caller will detect empty domain and handle skipping

        status = instructor_status.get(instr_id, '')
        subj_program = (subj.get('course') or '').strip().upper()
        subj_type = (subj.get('course_type') or 'major').lower()

        # pattern and days
        pattern_name, n_sessions = sessions_for_subject(subj)
        days = PATTERNS.get(pattern_name, PATTERNS['MWF'])[:n_sessions]

        # --- For major subjects: build lecture groups and lab groups separately,
        # then combine them into paired domain options (lecture+lab)
        if subj_type == 'major':
            lecture_candidates = []
            lab_candidates = []

            desired_room_type = ROOM_TYPE_MAP['lecture']
            lecture_rooms = [r for r in rooms if r['room_type'] == desired_room_type]
            for room in lecture_rooms:
                allowed_programs = [p.upper() for p in room_programs_map.get(room['room_id'], [])]
                if allowed_programs and subj_program not in allowed_programs:
                    continue
                for (start, end) in time_slots:
                    start_dt = datetime.strptime(start, "%H:%M")
                    end_dt = datetime.strptime(end, "%H:%M")
                    duration = (end_dt - start_dt).seconds / 60
                    if not (45 <= duration <= 70):
                        continue
                    if status == 'permanent' and intervals_overlap(start, end, "12:00", "13:00"):
                        continue
                    group = []
                    for day in days:
                        group.append({
                            'subject_id': sid,
                            'instructor_id': instr_id,
                            'room_id': room['room_id'],
                            'room_type': room['room_type'],
                            'day_of_week': day,
                            'start_time': start,
                            'end_time': end
                        })
                    if group:
                        lecture_candidates.append(group)

            desired_room_type_lab = ROOM_TYPE_MAP['laboratory']
            lab_rooms = [r for r in rooms if r['room_type'] == desired_room_type_lab]
            if not lab_rooms:
                lab_rooms = [r for r in rooms if r['room_type'] == ROOM_TYPE_MAP['lecture']]

            for room in lab_rooms:
                allowed_programs = [p.upper() for p in room_programs_map.get(room['room_id'], [])]
                if allowed_programs and subj_program not in allowed_programs:
                    continue
                for (start, end) in time_slots:
                    start_dt = datetime.strptime(start, "%H:%M")
                    end_dt = datetime.strptime(end, "%H:%M")
                    duration = (end_dt - start_dt).seconds / 60
                    if not (75 <= duration <= 110):
                        continue
                    if status == 'permanent' and intervals_overlap(start, end, "12:00", "13:00"):
                        continue
                    group = []
                    for day in days:
                        group.append({
                            'subject_id': sid,
                            'instructor_id': instr_id,
                            'room_id': room['room_id'],
                            'room_type': room['room_type'],
                            'day_of_week': day,
                            'start_time': start,
                            'end_time': end
                        })
                    if group:
                        lab_candidates.append(group)

            # combine lecture + lab
            for lec in lecture_candidates:
                for lab in lab_candidates:
                    valid_pair = True
                    if lec[0].get('instructor_id') != lab[0].get('instructor_id'):
                        valid_pair = False
                    if valid_pair:
                        for a in lec:
                            for b in lab:
                                if a['day_of_week'] == b['day_of_week'] and intervals_overlap(a['start_time'], a['end_time'], b['start_time'], b['end_time']):
                                    valid_pair = False
                                    break
                            if not valid_pair:
                                break
                    if valid_pair:
                        combined_group = lec + lab
                        local_domain.append(combined_group)

            # fallback to single-type groups if no paired options found
            if not local_domain:
                for lec in lecture_candidates:
                    local_domain.append(lec)
                for lab in lab_candidates:
                    local_domain.append(lab)

        else:
            # Non-major: single lecture-only groups according to pattern
            desired_room_type = ROOM_TYPE_MAP['lecture']
            lecture_rooms = [r for r in rooms if r['room_type'] == desired_room_type]
            for room in lecture_rooms:
                allowed_programs = [p.upper() for p in room_programs_map.get(room['room_id'], [])]
                if allowed_programs and subj_program not in allowed_programs:
                    continue

                for (start, end) in time_slots:
                    start_dt = datetime.strptime(start, "%H:%M")
                    end_dt = datetime.strptime(end, "%H:%M")
                    duration = (end_dt - start_dt).seconds / 60

                    if pattern_name == 'MWF' and not (45 <= duration <= 70):
                        continue
                    if pattern_name == 'TTh' and not (75 <= duration <= 110):
                        continue
                    if status == 'permanent' and intervals_overlap(start, end, "12:00", "13:00"):
                        continue

                    group = []
                    for day in days:
                        group.append({
                            'subject_id': sid,
                            'instructor_id': instr_id,
                            'room_id': room['room_id'],
                            'room_type': room['room_type'],
                            'day_of_week': day,
                            'start_time': start,
                            'end_time': end
                        })
                    if group:
                        local_domain.append(group)

        # randomize domain ordering for this subject to avoid worst-case ordering
        random.shuffle(local_domain)
        return str(sid), local_domain

    # ---------- Parallel domain construction ----------
    use_thread_build = True
    start_build = time.time()
    if use_thread_build and len(subjects) > 8:
        with ThreadPoolExecutor(max_workers=6) as executor:
            futures = {executor.submit(build_domain_for_subject, subj): subj for subj in subjects}
            for fut in as_completed(futures):
                try:
                    var_name, dom = fut.result()
                    domains[var_name] = dom
                except Exception:
                    # on failure, fallback to sequential for that subject
                    subj = futures[fut]
                    var_name, dom = build_domain_for_subject(subj)
                    domains[var_name] = dom
    else:
        for subj in subjects:
            var_name, dom = build_domain_for_subject(subj)
            domains[var_name] = dom
    build_time = time.time() - start_build
    print(f"[diagnostic] domain build took {build_time:.2f}s; total subjects: {len(subjects)}")

    # ---------- Pre-filter domains before AC3 ----------
    # Remove options that violate instructor max loads at single-option level
    for var, groups in list(domains.items()):
        filtered = []
        for g in groups:
            instr = g[0].get('instructor_id')
            if instr not in max_loads:
                continue
            if count_group_sessions(g) > max_loads[instr]:
                continue
            filtered.append(g)
        domains[var] = filtered

    zero_domains = [k for k, v in domains.items() if not v]
    domain_summary = {k: len(v) for k, v in domains.items()}
    print("========== DOMAIN DIAGNOSTICS ==========")
    print("DOMAIN SIZE SUMMARY:", domain_summary)
    if zero_domains:
        print("SUBJECTS WITH ZERO DOMAIN OPTIONS:", zero_domains)
        try:
            for sid in zero_domains:
                cur.execute("SELECT subject_id, code, name, course FROM subjects WHERE subject_id = %s", (sid,))
                print("NO-OPTIONS SUBJECT:", cur.fetchone())
        except Exception as e:
            print("Diagnostic DB fetch failed:", e)
    print("========================================")

    if skipped_subjects:
        flash(f"Warning: Skipped subjects with missing instructor: {skipped_subjects}", "warning")

    # Drop empty-domain subjects from domains (can't assign them)
    domains = {k: v for k, v in domains.items() if v}

    # Run AC3 with trimmed queue (faster)
    ac3_start = time.time()
    if not ac3(domains, trim_large_domains=True):
        print("[diagnostic] AC3 failed - no valid schedule possible after propagation.")
        flash("AC-3 failed: no valid schedule possible.", "danger")
        conn.close()
        return redirect(url_for('auto_scheduler.auto_scheduler_home'))
    print(f"[diagnostic] AC3 propagation took {time.time()-ac3_start:.2f}s")

    # ---------- Run backtracking with profiling ----------
    bt_start = time.time()
    final_assignment = backtrack({}, domains, instructor_load, max_loads)
    exec_time = time.time() - bt_start
    print(f"[diagnostic] backtracking took {exec_time:.2f}s")

    if final_assignment:
        subject_ids = list(final_assignment.keys())
        if subject_ids:
            fmt = ','.join(['%s'] * len(subject_ids))
            delete_q = f"""
                DELETE FROM schedules
                WHERE subject_id IN ({fmt})
                AND semester = %s AND school_year = %s
                AND (approved IS NULL OR approved = 0)
            """
            try:
                subject_ids_int = [int(x) for x in subject_ids]
            except Exception:
                subject_ids_int = subject_ids
            cur.execute(delete_q, tuple(subject_ids_int) + (semester, school_year))

        insert_q = """
            INSERT INTO schedules
            (subject_id, instructor_id, room_id, day_of_week, start_time, end_time, semester, school_year)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """
        for var, group in final_assignment.items():
            for s in group:
                cur.execute(insert_q, (
                    s['subject_id'], s['instructor_id'], s['room_id'],
                    s['day_of_week'], s['start_time'], s['end_time'],
                    semester, school_year
                ))

        conn.commit()

        flash(f"Schedule generated successfully in {exec_time:.2f} seconds with all constraints applied.", "success")

    else:
        flash("Failed to generate schedule - no valid assignment found.", "danger")

    conn.close()
    return redirect(url_for('auto_scheduler.auto_scheduler_home'))
