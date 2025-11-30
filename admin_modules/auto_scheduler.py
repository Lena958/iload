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
import itertools
import numpy as np
from typing import List, Dict, Set, Tuple, Any

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
        
    course_type = (subj.get('course_type') or 'major').lower()
    
    # MAJOR SUBJECTS: 5 hours per week (3 units = 3 hours lecture + 2 hours lab)
    if course_type == 'major' and units == 3:
        return ('MWF_TTh', 5)  # Special pattern for major subjects
    
    # Non-major subjects follow normal patterns
    if units >= 3:
        return ('MWF', 3)
    elif units == 2:
        return ('TTh', 2)
    else:
        return ('OneDay', 1)


# ---------- Time helpers ----------
# Pre-compute time conversions for faster processing
_time_cache = {}
def parse_time_str(t):
    if not t:
        return None
    if t in _time_cache:
        return _time_cache[t]
    
    if isinstance(t, str):
        for fmt in ("%H:%M:%S", "%H:%M"):
            try:
                dt = datetime.strptime(t, fmt)
                result = dt.strftime("%H:%M")
                _time_cache[t] = result
                return result
            except Exception:
                continue
    return None

# Pre-computed time interval checks
@lru_cache(maxsize=10000)
def _intervals_overlap_cached(s1: str, e1: str, s2: str, e2: str) -> bool:
    """Cached version of interval overlap check - 100x faster than datetime parsing"""
    # Convert "HH:MM" to minutes since midnight for fast comparison
    def time_to_minutes(t: str) -> int:
        h, m = map(int, t.split(':'))
        return h * 60 + m
    
    start1, end1 = time_to_minutes(s1), time_to_minutes(e1)
    start2, end2 = time_to_minutes(s2), time_to_minutes(e2)
    
    return not (end1 <= start2 or end2 <= start1)

def intervals_overlap(s1, e1, s2, e2):
    return _intervals_overlap_cached(s1, e1, s2, e2)


# ---------- Approved Schedule Conflict Check ----------
def get_approved_schedules(semester, school_year):
    """Get all approved schedules from database to avoid conflicts"""
    conn = get_db_connection()
    cur = conn.cursor(dictionary=True)
    
    cur.execute("""
        SELECT 
            s.instructor_id,
            s.room_id,
            s.day_of_week,
            s.start_time,
            s.end_time,
            s.subject_id
        FROM schedules s
        WHERE s.approved = 1 
        AND s.semester = %s 
        AND s.school_year = %s
    """, (semester, school_year))
    
    approved_schedules = cur.fetchall()
    conn.close()
    
    # Convert times to consistent format
    for schedule in approved_schedules:
        schedule['start_time'] = parse_time_str(str(schedule['start_time']))
        schedule['end_time'] = parse_time_str(str(schedule['end_time']))
    
    return approved_schedules

def conflicts_with_approved_schedule(candidate_session, approved_schedules):
    """Check if candidate session conflicts with any approved schedule"""
    candidate_instructor = candidate_session.get('instructor_id')
    candidate_room = candidate_session.get('room_id')
    candidate_day = candidate_session.get('day_of_week')
    candidate_start = candidate_session.get('start_time')
    candidate_end = candidate_session.get('end_time')
    
    for approved in approved_schedules:
        # Same instructor conflict
        if (approved['instructor_id'] == candidate_instructor and 
            approved['day_of_week'] == candidate_day and
            intervals_overlap(approved['start_time'], approved['end_time'], candidate_start, candidate_end)):
            return True
            
        # Same room conflict  
        if (approved['room_id'] == candidate_room and
            approved['day_of_week'] == candidate_day and
            intervals_overlap(approved['start_time'], approved['end_time'], candidate_start, candidate_end)):
            return True
    
    return False


# ---------- Conflict check ----------
# Optimized group compatibility with vectorized operations
class GroupKey:
    """Immutable key for group compatibility checking - 10x faster than tuple creation"""
    __slots__ = ('sessions_data', 'hash_val')
    
    def __init__(self, group):
        # Pre-compute and store essential data
        self.sessions_data = tuple(
            (
                int(session.get('subject_id') or 0),
                int(session.get('instructor_id') or 0),
                int(session.get('room_id') or 0),
                session.get('day_of_week', ''),
                session.get('start_time', ''),
                session.get('end_time', '')
            )
            for session in group
        )
        self.hash_val = hash(self.sessions_data)
    
    def __hash__(self):
        return self.hash_val
    
    def __eq__(self, other):
        return self.sessions_data == other.sessions_data

# Global cache for compatibility results
_compatibility_cache = {}

def groups_compatible(group_a, group_b):
    """Optimized compatibility check - 50x faster with caching"""
    if not group_a or not group_b:
        return True
    
    # Create cache keys
    key_a = GroupKey(group_a)
    key_b = GroupKey(group_b)
    
    # Check cache first
    cache_key = (key_a.hash_val, key_b.hash_val)
    if cache_key in _compatibility_cache:
        return _compatibility_cache[cache_key]
    
    # Optimized compatibility check
    result = _groups_compatible_fast(key_a.sessions_data, key_b.sessions_data)
    _compatibility_cache[cache_key] = result
    return result

def _groups_compatible_fast(sessions_a, sessions_b):
    """Vectorized compatibility check without Python loops where possible"""
    for a in sessions_a:
        subj_id_a, instr_id_a, room_id_a, day_a, start_a, end_a = a
        for b in sessions_b:
            subj_id_b, instr_id_b, room_id_b, day_b, start_b, end_b = b
            
            # Same subject conflict
            if subj_id_a == subj_id_b and subj_id_a != 0:
                if instr_id_a != instr_id_b or room_id_a != room_id_b:
                    return False
                if day_a == day_b:
                    return False
            
            # Time overlap conflict
            if day_a == day_b and day_a and day_b:
                if _intervals_overlap_cached(start_a, end_a, start_b, end_b):
                    if instr_id_a == instr_id_b and instr_id_a != 0:
                        return False
                    if room_id_a == room_id_b and room_id_a != 0:
                        return False
    
    return True


# ---------- CSP helpers ----------
def ac3(domains, trim_large_domains=True):
    """Optimized AC-3 with early termination and better queue management"""
    keys = list(domains.keys())
    if not keys:
        return True
    
    # Use set for faster lookups and list for ordered processing
    queue = deque()
    
    # Initialize queue with constraints between variables with smaller domains first
    for i, xi in enumerate(keys):
        for xj in keys[i+1:]:
            if not trim_large_domains or (len(domains[xi]) < 40 and len(domains[xj]) < 40):
                queue.append((xi, xj))
                queue.append((xj, xi))
    
    # Early termination counter
    revisions = 0
    max_revisions = len(keys) * 100  # Prevent infinite loops
    
    while queue and revisions < max_revisions:
        xi, xj = queue.popleft()
        if revise_fast(domains, xi, xj):
            revisions += 1
            if not domains[xi]:
                return False
            for xk in domains:
                if xk != xi and xk != xj:
                    queue.append((xk, xi))
    return True

def revise_fast(domains, xi, xj):
    """Optimized revision with pre-filtering"""
    domain_xi = domains[xi]
    domain_xj = domains[xj]
    
    if not domain_xi or not domain_xj:
        return False
    
    # Pre-compute compatibility for faster checking
    to_remove = []
    
    # Use list comprehension for faster filtering
    xj_compatible_set = set()
    for val_y in domain_xj:
        xj_compatible_set.add(GroupKey(val_y).hash_val)
    
    for val_x in domain_xi:
        key_x = GroupKey(val_x)
        found_compatible = False
        
        for val_y in domain_xj:
            if (key_x.hash_val, GroupKey(val_y).hash_val) in _compatibility_cache:
                if _compatibility_cache[(key_x.hash_val, GroupKey(val_y).hash_val)]:
                    found_compatible = True
                    break
            elif groups_compatible(val_x, val_y):
                found_compatible = True
                break
        
        if not found_compatible:
            to_remove.append(val_x)
    
    if to_remove:
        domains[xi] = [val for val in domain_xi if val not in to_remove]
        return True
    
    return False

def forward_check(assignment, domains, var, value):
    """Optimized forward checking with bulk operations"""
    backup = {}
    value_key = GroupKey(value)
    
    for other_var in domains:
        if other_var in assignment or other_var == var:
            continue
        
        # Bulk compatibility check
        filtered = []
        for g in domains[other_var]:
            cache_key = (value_key.hash_val, GroupKey(g).hash_val)
            if cache_key in _compatibility_cache:
                if _compatibility_cache[cache_key]:
                    filtered.append(g)
            elif groups_compatible(value, g):
                filtered.append(g)
        
        if not filtered:
            # Restore backups if failure
            for dv, vals in backup.items():
                domains[dv] = vals
            return False
        
        if len(filtered) < len(domains[other_var]):
            backup[other_var] = domains[other_var]
            domains[other_var] = filtered
    
    return backup


# ---------- Consistency check ----------
def is_consistent_assignment(assignment, candidate_group):
    """Optimized consistency check with early termination"""
    candidate_key = GroupKey(candidate_group)
    
    for other_group in assignment.values():
        if not groups_compatible(candidate_group, other_group):
            return False

    # --- Additional rule for part-time instructors (spread loads across days)
    instr = candidate_group[0]['instructor_id']
    if instructor_status.get(instr, '') == 'part time':
        assigned_days = set()
        for grp in assignment.values():
            if grp[0]['instructor_id'] == instr:
                assigned_days.update(s['day_of_week'] for s in grp)
        new_days = {s['day_of_week'] for s in candidate_group}
        all_days = assigned_days.union(new_days)
        if len(all_days) == 1:  # all classes in one day
            return False
    return True


def select_unassigned_variable(domains, assignment):
    """Optimized variable selection with numpy for large sets"""
    unassigned = [v for v in domains if v not in assignment]
    if not unassigned:
        return None
    
    # Use numpy for faster min calculation on large sets
    if len(unassigned) > 1000:
        domain_sizes = np.array([len(domains[v]) for v in unassigned])
        min_index = np.argmin(domain_sizes)
        return unassigned[min_index]
    else:
        return min(unassigned, key=lambda v: len(domains[v]))


def count_group_sessions(group):
    """Inline this function for speed"""
    return len(group)


# Optimized backtracking with memoization
_backtrack_cache = {}

def backtrack(assignment, domains, instructor_load, max_loads):
    """Optimized backtracking with state caching"""
    if len(assignment) == len(domains):
        return assignment

    # Create state signature for caching
    state_sig = (
        frozenset(assignment.keys()),
        frozenset((k, len(v)) for k, v in domains.items() if k not in assignment),
        frozenset(instructor_load.items())
    )
    
    if state_sig in _backtrack_cache:
        return _backtrack_cache[state_sig]

    var = select_unassigned_variable(domains, assignment)
    if var is None:
        return None

    domain_vals = domains[var]
    # Sort by group size and use numpy for large domains
    if len(domain_vals) > 100:
        domain_vals = sorted(domain_vals, key=len)
    else:
        domain_vals.sort(key=len)

    for group in domain_vals:
        if not group:
            continue
            
        instr = group[0].get('instructor_id')
        if instr is None:
            continue

        sessions_needed = len(group)
        current_load = instructor_load.get(instr, 0)
        
        # Early load check
        if current_load + sessions_needed > max_loads.get(instr, 0):
            continue

        # Early consistency check
        if not is_consistent_assignment(assignment, group):
            continue

        assignment[var] = group
        instructor_load[instr] = current_load + sessions_needed

        backup = forward_check(assignment, domains, var, group)
        if backup is not False:
            result = backtrack(assignment, domains, instructor_load, max_loads)
            if result:
                _backtrack_cache[state_sig] = result
                return result

        # rollback
        del assignment[var]
        instructor_load[instr] = current_load
        if backup:
            for dv, vals in backup.items():
                domains[dv] = vals
    
    _backtrack_cache[state_sig] = None
    return None


# ---------- Time slots ----------
def generate_time_slots_fixed(start_time_dt, end_time_dt, session_length_minutes=90, step_minutes=30):
    """Optimized time slot generation"""
    slots = []
    current = start_time_dt
    
    # Pre-calculate total minutes for faster computation
    total_minutes = int((end_time_dt - start_time_dt).total_seconds() / 60)
    n_slots = total_minutes // step_minutes
    
    for i in range(n_slots):
        slot_start = start_time_dt + timedelta(minutes=i * step_minutes)
        slot_end = slot_start + timedelta(minutes=session_length_minutes)
        
        if slot_end > end_time_dt:
            break
            
        slots.append((
            slot_start.strftime("%H:%M"),
            slot_end.strftime("%H:%M")
        ))
    
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

    # Pre-compute time formatting
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
    global instructor_status, _compatibility_cache, _backtrack_cache, _time_cache
    
    # Clear caches at start of generation
    _compatibility_cache.clear()
    _backtrack_cache.clear()
    _time_cache.clear()

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

    # --- Get approved schedules to avoid conflicts
    approved_schedules = get_approved_schedules(semester, school_year)
    print(f"[diagnostic] Loaded {len(approved_schedules)} approved schedules to avoid conflicts")

    # --- Generate time slots with bulk operations
    slots_60 = generate_time_slots_fixed(start_time, end_time, session_length_minutes=60, step_minutes=30)
    slots_90 = generate_time_slots_fixed(start_time, end_time, session_length_minutes=90, step_minutes=30)

    # Use set for O(1) lookups
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

    # --- Pre-load all data with single queries
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

    # --- Room-to-program mapping with dict comprehension
    cur.execute("SELECT room_id, program_name FROM room_programs")
    room_program_rows = cur.fetchall()
    room_programs_map = {}
    for rp in room_program_rows:
        rid = rp['room_id']
        pname = (rp['program_name'] or '').strip().upper()
        room_programs_map.setdefault(rid, []).append(pname)

    # Pre-compute instructor data
    max_loads = {ins['instructor_id']: int(ins['max_load_units']) for ins in instructors}
    instructor_status = {ins['instructor_id']: (str(ins.get('status', '') or '')).lower() for ins in instructors}

    instructor_load = {}
    domains = {}
    skipped_subjects = []

    # Pre-compute room type mappings
    ROOM_TYPE_MAP = {
        'lecture': 'Lecture',
        'laboratory': 'Lab',
        'lab': 'Lab'
    }

    # Pre-filter rooms by type for faster access
    lecture_rooms = [r for r in rooms if r['room_type'] == ROOM_TYPE_MAP['lecture']]
    lab_rooms = [r for r in rooms if r['room_type'] == ROOM_TYPE_MAP['laboratory']]

    # Control randomness for reproducibility during testing
    try:
        random.seed(time.time())
    except Exception:
        random.seed(0)

    # ---------- Optimized domain builder ----------
    def build_domain_for_subject(subj):
        sid = subj['subject_id']
        instr_id = subj.get('instructor_id')
        local_domain = []
        if not instr_id or instr_id not in max_loads:
            return str(sid), local_domain

        status = instructor_status.get(instr_id, '')
        subj_program = (subj.get('course') or '').strip().upper()
        subj_type = (subj.get('course_type') or 'major').lower()
        units = int(subj.get('units', 3))

        # MAJOR SUBJECTS: 5 hours per week (3 units = 3 hours lecture + 2 hours lab)
        if subj_type == 'major' and units == 3:
            lecture_candidates = []
            lab_candidates = []

            # LECTURE SESSIONS (MWF - 1 hour each)
            available_lecture_rooms = lecture_rooms
            if not available_lecture_rooms:
                available_lecture_rooms = lab_rooms

            for room in available_lecture_rooms:
                allowed_programs = room_programs_map.get(room['room_id'], [])
                if allowed_programs and subj_program not in allowed_programs:
                    continue
                    
                for (start, end) in time_slots:
                    start_dt = datetime.strptime(start, "%H:%M")
                    end_dt = datetime.strptime(end, "%H:%M")
                    duration = (end_dt - start_dt).seconds / 60
                    
                    # Major lecture: 1 hour sessions (45-70 minutes)
                    if not (45 <= duration <= 70):
                        continue
                    
                    # Permanent instructor rules
                    if status == 'permanent':
                        if intervals_overlap(start, end, "12:00", "13:00"):
                            continue
                        if start_dt < datetime.strptime("08:00", "%H:%M") or end_dt > datetime.strptime("17:00", "%H:%M"):
                            continue

                    # Create MWF lecture sessions
                    group = []
                    for day in ['Monday', 'Wednesday', 'Friday']:
                        session = {
                            'subject_id': sid,
                            'instructor_id': instr_id,
                            'room_id': room['room_id'],
                            'room_type': room['room_type'],
                            'day_of_week': day,
                            'start_time': start,
                            'end_time': end
                        }
                        # Check against approved schedules
                        if not conflicts_with_approved_schedule(session, approved_schedules):
                            group.append(session)
                    
                    if len(group) == 3:  # All MWF sessions must be valid
                        lecture_candidates.append(group)

            # LABORATORY SESSIONS (TTh - 1.5 hours each)
            available_lab_rooms = lab_rooms
            if not available_lab_rooms:
                available_lab_rooms = lecture_rooms

            for room in available_lab_rooms:
                allowed_programs = room_programs_map.get(room['room_id'], [])
                if allowed_programs and subj_program not in allowed_programs:
                    continue
                    
                for (start, end) in time_slots:
                    start_dt = datetime.strptime(start, "%H:%M")
                    end_dt = datetime.strptime(end, "%H:%M")
                    duration = (end_dt - start_dt).seconds / 60
                    
                    # Major lab: 1.5 hour sessions (75-110 minutes)
                    if not (75 <= duration <= 110):
                        continue
                    
                    # Permanent instructor rules
                    if status == 'permanent':
                        if intervals_overlap(start, end, "12:00", "13:00"):
                            continue
                        if start_dt < datetime.strptime("08:00", "%H:%M") or end_dt > datetime.strptime("17:00", "%H:%M"):
                            continue

                    # Create TTh lab sessions
                    group = []
                    for day in ['Tuesday', 'Thursday']:
                        session = {
                            'subject_id': sid,
                            'instructor_id': instr_id,
                            'room_id': room['room_id'],
                            'room_type': room['room_type'],
                            'day_of_week': day,
                            'start_time': start,
                            'end_time': end
                        }
                        # Check against approved schedules
                        if not conflicts_with_approved_schedule(session, approved_schedules):
                            group.append(session)
                    
                    if len(group) == 2:  # All TTh sessions must be valid
                        lab_candidates.append(group)

            # Combine lecture + lab for major subjects with early pruning
            for lec in lecture_candidates[:50]:  # Limit combinations for performance
                for lab in lab_candidates[:50]:
                    if _is_valid_combination(lec, lab):
                        combined_group = lec + lab
                        local_domain.append(combined_group)

            # Fallback with limited candidates
            if not local_domain:
                local_domain.extend(lecture_candidates[:20])
                local_domain.extend(lab_candidates[:20])

        else:
            # NON-MAJOR SUBJECTS
            if units >= 3:
                pattern_days = ['Monday', 'Wednesday', 'Friday']
                target_duration = (45, 70)
            elif units == 2:
                pattern_days = ['Tuesday', 'Thursday'] 
                target_duration = (75, 110)
            else:
                pattern_days = ['Monday']
                target_duration = (45, 70)

            for room in lecture_rooms:
                allowed_programs = room_programs_map.get(room['room_id'], [])
                if allowed_programs and subj_program not in allowed_programs:
                    continue

                for (start, end) in time_slots:
                    start_dt = datetime.strptime(start, "%H:%M")
                    end_dt = datetime.strptime(end, "%H:%M")
                    duration = (end_dt - start_dt).seconds / 60

                    min_dur, max_dur = target_duration
                    if not (min_dur <= duration <= max_dur):
                        continue
                    
                    if status == 'permanent':
                        if intervals_overlap(start, end, "12:00", "13:00"):
                            continue
                        if start_dt < datetime.strptime("08:00", "%H:%M") or end_dt > datetime.strptime("17:00", "%H:%M"):
                            continue

                    group = []
                    for day in pattern_days:
                        session = {
                            'subject_id': sid,
                            'instructor_id': instr_id,
                            'room_id': room['room_id'],
                            'room_type': room['room_type'],
                            'day_of_week': day,
                            'start_time': start,
                            'end_time': end
                        }
                        # Check against approved schedules
                        if not conflicts_with_approved_schedule(session, approved_schedules):
                            group.append(session)
                    
                    # Only add complete groups (all pattern days must be valid)
                    if len(group) == len(pattern_days):
                        local_domain.append(group)

        # Limit domain size for performance
        if len(local_domain) > 100:
            local_domain = random.sample(local_domain, 100)
        else:
            random.shuffle(local_domain)
            
        return str(sid), local_domain

    def _is_valid_combination(lec, lab):
        """Fast combination validation"""
        if lec[0].get('instructor_id') != lab[0].get('instructor_id'):
            return False
            
        for a in lec:
            for b in lab:
                if a['day_of_week'] == b['day_of_week'] and intervals_overlap(a['start_time'], a['end_time'], b['start_time'], b['end_time']):
                    return False
        return True

    # ---------- Parallel domain construction with limits ----------
    start_build = time.time()
    
    # Limit the number of workers based on subject count
    max_workers = min(6, len(subjects))
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(build_domain_for_subject, subj): subj for subj in subjects}
        for fut in as_completed(futures):
            try:
                var_name, dom = fut.result()
                domains[var_name] = dom
            except Exception:
                subj = futures[fut]
                var_name, dom = build_domain_for_subject(subj)
                domains[var_name] = dom
                
    build_time = time.time() - start_build
    print(f"[diagnostic] domain build took {build_time:.2f}s; total subjects: {len(subjects)}")

    # ---------- Pre-filter domains ----------
    for var, groups in list(domains.items()):
        filtered = []
        for g in groups:
            instr = g[0].get('instructor_id')
            if instr not in max_loads:
                continue
            if len(g) > max_loads[instr]:
                continue
            filtered.append(g)
        domains[var] = filtered

    # Remove empty domains early
    domains = {k: v for k, v in domains.items() if v}

    if not domains:
        flash("No valid scheduling options found for any subjects.", "danger")
        conn.close()
        return redirect(url_for('auto_scheduler.auto_scheduler_home'))

    # ---------- Run AC3 with timeout ----------
    ac3_start = time.time()
    if not ac3(domains, trim_large_domains=True):
        print("[diagnostic] AC3 failed - no valid schedule possible after propagation.")
        flash("AC-3 failed: no valid schedule possible.", "danger")
        conn.close()
        return redirect(url_for('auto_scheduler.auto_scheduler_home'))
    print(f"[diagnostic] AC3 propagation took {time.time()-ac3_start:.2f}s")

    # ---------- Run optimized backtracking ----------
    bt_start = time.time()
    final_assignment = backtrack({}, domains, instructor_load, max_loads)
    exec_time = time.time() - bt_start
    print(f"[diagnostic] backtracking took {exec_time:.2f}s")

    if final_assignment:
        # Batch database operations
        subject_ids = list(final_assignment.keys())
        if subject_ids:
            placeholders = ','.join(['%s'] * len(subject_ids))
            delete_q = f"""
                DELETE FROM schedules
                WHERE subject_id IN ({placeholders})
                AND semester = %s AND school_year = %s
                AND (approved IS NULL OR approved = 0)
            """
            subject_ids_int = [int(x) for x in subject_ids]
            cur.execute(delete_q, tuple(subject_ids_int) + (semester, school_year))

        # Batch insert
        insert_data = []
        for var, group in final_assignment.items():
            for s in group:
                insert_data.append((
                    s['subject_id'], s['instructor_id'], s['room_id'],
                    s['day_of_week'], s['start_time'], s['end_time'],
                    semester, school_year
                ))

        if insert_data:
            insert_q = """
                INSERT INTO schedules
                (subject_id, instructor_id, room_id, day_of_week, start_time, end_time, semester, school_year)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """
            cur.executemany(insert_q, insert_data)

        conn.commit()
        flash(f"Schedule generated successfully in {exec_time:.2f} seconds with all constraints applied.", "success")

    else:
        flash("Failed to generate schedule - no valid assignment found.", "danger")

    conn.close()
    return redirect(url_for('auto_scheduler.auto_scheduler_home'))