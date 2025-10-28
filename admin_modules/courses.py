# ==================================================
# 1. Imports
# ==================================================
from flask import Blueprint, render_template, request, redirect, url_for, flash, session
import mysql.connector

# ==================================================
# 2. Blueprint Definition
# ==================================================
courses_bp = Blueprint('courses', __name__, url_prefix='/admin/courses')

# ==================================================
# 3. Database Configuration 
# ==================================================
db_config = {
    'host': 'localhost',
    'user': 'root',
    'password': '',
    'database': 'iload'
}

# ==================================================
# 4. Helper Functions
# ==================================================
def get_db_connection():
    """Returns a new database connection."""
    return mysql.connector.connect(**db_config)

def is_admin():
    """Checks if the current session user is an admin."""
    return session.get('role') == 'admin'

# ==================================================
# 5. Context Processor
# ==================================================
@courses_bp.context_processor
def inject_instructor_name():
    """Injects instructor name and image into templates for sidebar."""
    if 'user_id' not in session:
        return dict(instructor_name=None, instructor_image=None)

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute(
        "SELECT name, image FROM instructors WHERE instructor_id = %s",
        (session['user_id'],)
    )
    instructor = cursor.fetchone()
    conn.close()

    return dict(
        instructor_name=instructor['name'] if instructor else None,
        instructor_image=instructor['image'] if instructor and instructor['image'] else None
    )

# ==================================================
# 6. Routes
# ==================================================

# ---------- List & Filter Courses ----------
@courses_bp.route('/', methods=['GET', 'POST'])
def list_courses():
    """Displays and filters the list of courses."""
    if not is_admin():
        return redirect(url_for('login'))

    # Get filter selections (from POST or preserve on GET)
    selected_program = request.form.get('program') if request.method == 'POST' else None
    selected_school_year = request.form.get('school_year') if request.method == 'POST' else None
    selected_semester = request.form.get('semester') if request.method == 'POST' else None

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    # Fetch dropdown values
    cursor.execute("SELECT DISTINCT program FROM courses ORDER BY program ASC")
    programs = [row['program'] for row in cursor.fetchall() if row['program']]

    cursor.execute("SELECT DISTINCT school_year FROM courses ORDER BY school_year DESC")
    school_years = [row['school_year'] for row in cursor.fetchall() if row['school_year']]

    cursor.execute("SELECT DISTINCT semester FROM courses ORDER BY semester ASC")
    semesters = [row['semester'] for row in cursor.fetchall() if row['semester']]

    # Base query for course listing
    query = "SELECT course_id, course_code, course_name FROM courses WHERE 1=1"
    params = []

    # Apply filters
    if selected_program:
        query += " AND program = %s"
        params.append(selected_program)

    if selected_school_year:
        query += " AND school_year = %s"
        params.append(selected_school_year)

    if selected_semester:
        query += " AND semester = %s"
        params.append(selected_semester)

    # Order by course_code for readability
    query += " ORDER BY course_code ASC"

    cursor.execute(query, tuple(params))
    courses = cursor.fetchall()

    conn.close()

    return render_template(
        'admin/courses.html',
        courses=courses,
        programs=programs,
        school_years=school_years,
        semesters=semesters,
        selected_program=selected_program,
        selected_school_year=selected_school_year,
        selected_semester=selected_semester
    )

# ---------- Add Course ----------
@courses_bp.route('/add', methods=['GET', 'POST'])
def add_course():
    """Adds a new course."""
    if not is_admin():
        return redirect(url_for('login'))

    if request.method == 'POST':
        course_code = request.form['course_code'].strip()
        course_name = request.form['course_name'].strip()
        program = request.form['program'].strip()
        school_year = request.form['school_year'].strip()
        semester = request.form['semester'].strip()

        if not all([course_code, course_name, program, school_year, semester]):
            flash("⚠️ All fields are required.", "danger")
            return redirect(url_for('courses.add_course'))

        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO courses (course_code, course_name, program, school_year, semester)
            VALUES (%s, %s, %s, %s, %s)
        """, (course_code, course_name, program, school_year, semester))
        conn.commit()
        conn.close()

        flash("✅ Course added successfully.", "success")
        return redirect(url_for('courses.list_courses'))

    return render_template('admin/add_course.html')

# ---------- Edit Course ----------
@courses_bp.route('/edit/<int:course_id>', methods=['GET', 'POST'])
def edit_course(course_id):
    """Edit an existing course and save changes to the database."""
    if not is_admin():
        return redirect(url_for('login'))

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    # Fetch the course first
    cursor.execute("SELECT * FROM courses WHERE course_id = %s", (course_id,))
    course = cursor.fetchone()

    if not course:
        conn.close()
        flash("⚠️ Course not found.", "danger")
        return redirect(url_for('courses.list_courses'))

    # If form is submitted (POST)
    if request.method == 'POST':
        course_code = request.form.get('course_code', '').strip()
        course_name = request.form.get('course_name', '').strip()
        program = request.form.get('program', '').strip()
        school_year = request.form.get('school_year', '').strip()
        semester = request.form.get('semester', '').strip()

        # Validate form data
        if not all([course_code, course_name, program, school_year, semester]):
            flash("⚠️ All fields are required.", "danger")
            conn.close()
            return redirect(url_for('courses.edit_course', course_id=course_id))

        try:
            # Update record in database
            cursor.execute("""
                UPDATE courses
                SET course_code = %s,
                    course_name = %s,
                    program = %s,
                    school_year = %s,
                    semester = %s
                WHERE course_id = %s
            """, (course_code, course_name, program, school_year, semester, course_id))
            conn.commit()

            flash("✅ Course updated successfully.", "success")
        except Exception as e:
            flash(f"❌ Error updating course: {e}", "danger")
        finally:
            conn.close()

        # Redirect back to the course list
        return redirect(url_for('courses.list_courses'))

    # GET request — display the edit form
    conn.close()
    return render_template('admin/edit_course.html', course=course)

# ---------- Delete Course ----------
@courses_bp.route('/delete/<int:course_id>', methods=['POST'])
def delete_course(course_id):
    """Deletes a course."""
    if not is_admin():
        return redirect(url_for('login'))

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM courses WHERE course_id = %s", (course_id,))
    conn.commit()
    conn.close()

    flash("🗑️ Course deleted successfully.", "success")
    return redirect(url_for('courses.list_courses'))
