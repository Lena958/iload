from flask import Blueprint, render_template, request, redirect, url_for, flash, session, current_app
import mysql.connector
import os
from werkzeug.utils import secure_filename

# Blueprint Definition
rooms_bp = Blueprint('rooms', __name__, url_prefix='/admin/rooms')

# Database Configuration
db_config = {
    'host': 'localhost',
    'user': 'root',
    'password': '',
    'database': 'iload'
}

# Global Constants
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg'}

# -------------------- Helper Functions --------------------

def get_db_connection():
    """Establish a database connection."""
    return mysql.connector.connect(**db_config)

def is_admin():
    """Check if the current user is an admin."""
    return session.get('role') == 'admin'

def allowed_file(filename):
    """Check if the uploaded file is allowed based on extension."""
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def parse_programs(program_input):
    """Split input into multiple programs (separated by '/' or ',')."""
    if not program_input:
        return []
    # Split by '/' or ',' and strip whitespace
    return [p.strip() for p in program_input.replace(',', '/').split('/') if p.strip()]

# -------------------- Context Processor --------------------

@rooms_bp.context_processor
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

# -------------------- ROOM MANAGEMENT ROUTES --------------------

# List All Rooms
@rooms_bp.route('/')
def list_rooms():
    if not is_admin():
        return redirect(url_for('login'))

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM rooms")
    rooms = cursor.fetchall()

    # Fetch associated programs for each room
    for room in rooms:
        cursor.execute("SELECT program_name FROM room_programs WHERE room_id = %s", (room['room_id'],))
        room['programs'] = [row['program_name'] for row in cursor.fetchall()]

    conn.close()

    # Extract distinct programs for search/suggestions
    all_programs = set()
    for room in rooms:
        all_programs.update(room['programs'])
    programs = sorted(all_programs)

    return render_template("admin/rooms.html", rooms=rooms, programs=programs)

# Add Room
@rooms_bp.route('/add', methods=['GET', 'POST'])
def add_room():
    if not is_admin():
        return redirect(url_for('login'))

    # Fetch program suggestions
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT DISTINCT program_name FROM room_programs")
    programs = [row['program_name'] for row in cursor.fetchall()]
    conn.close()

    if request.method == 'POST':
        room_number = request.form['room_number']
        room_type = request.form['room_type']
        program_input = request.form.get('program')  # Single text input for multiple programs
        programs_list = parse_programs(program_input)  # Split into list

        # Handle image upload
        image_file = request.files.get('image')
        image_filename = None
        if image_file and allowed_file(image_file.filename):
            filename = secure_filename(image_file.filename)
            upload_folder = os.path.join(current_app.root_path, 'static', 'room_images')
            os.makedirs(upload_folder, exist_ok=True)
            image_file.save(os.path.join(upload_folder, filename))
            image_filename = filename

        # Insert into rooms table
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO rooms (room_number, room_type, image) VALUES (%s, %s, %s)",
            (room_number, room_type, image_filename)
        )
        room_id = cursor.lastrowid

        # Insert into room_programs table
        for program_name in programs_list:
            cursor.execute(
                "INSERT INTO room_programs (room_id, program_name) VALUES (%s, %s)",
                (room_id, program_name)
            )

        conn.commit()
        conn.close()

        flash("Room added successfully", "success")
        return redirect(url_for('rooms.list_rooms'))

    return render_template("admin/add_room.html", programs=programs)

# Edit Room
@rooms_bp.route('/edit/<int:room_id>', methods=['GET', 'POST'])
def edit_room(room_id):
    if not is_admin():
        return redirect(url_for('login'))

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    # Fetch room details
    cursor.execute("SELECT * FROM rooms WHERE room_id = %s", (room_id,))
    room = cursor.fetchone()
    if not room:
        conn.close()
        flash("Room not found", "error")
        return redirect(url_for('rooms.list_rooms'))

    # Fetch all program suggestions
    cursor.execute("SELECT DISTINCT program_name FROM room_programs")
    programs = [row['program_name'] for row in cursor.fetchall()]

    # Fetch programs assigned to this room
    cursor.execute("SELECT program_name FROM room_programs WHERE room_id = %s", (room_id,))
    room_programs = [row['program_name'] for row in cursor.fetchall()]
    current_program = '/'.join(room_programs)  # Show as slash-separated in form

    if request.method == 'POST':
        room_number = request.form['room_number']
        room_type = request.form['room_type']
        program_input = request.form.get('program')
        programs_list = parse_programs(program_input)

        # Handle image upload
        image_file = request.files.get('image')
        image_filename = room['image']
        if image_file and allowed_file(image_file.filename):
            filename = secure_filename(image_file.filename)
            upload_folder = os.path.join(current_app.root_path, 'static', 'room_images')
            os.makedirs(upload_folder, exist_ok=True)
            image_file.save(os.path.join(upload_folder, filename))
            image_filename = filename

        # Update rooms table
        cursor.execute(
            "UPDATE rooms SET room_number=%s, room_type=%s, image=%s WHERE room_id=%s",
            (room_number, room_type, image_filename, room_id)
        )

        # Update room_programs table
        cursor.execute("DELETE FROM room_programs WHERE room_id = %s", (room_id,))
        for program_name in programs_list:
            cursor.execute(
                "INSERT INTO room_programs (room_id, program_name) VALUES (%s, %s)",
                (room_id, program_name)
            )

        conn.commit()
        conn.close()

        flash("Room updated successfully", "success")
        return redirect(url_for('rooms.list_rooms'))

    conn.close()
    return render_template(
        "admin/edit_room.html",
        room=room,
        programs=programs,
        room_programs=room_programs,
        current_program=current_program
    )

# Delete Room
@rooms_bp.route('/delete/<int:room_id>', methods=['POST'])
def delete_room(room_id):
    if not is_admin():
        return redirect(url_for('login'))

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM rooms WHERE room_id = %s", (room_id,))
    # room_programs will be automatically deleted if you set ON DELETE CASCADE
    conn.commit()
    conn.close()

    flash("Room deleted successfully", "success")
    return redirect(url_for('rooms.list_rooms'))
