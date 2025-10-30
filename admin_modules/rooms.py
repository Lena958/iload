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



#  Global Constants

ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg'}



#  Helper Functions

def get_db_connection():
    """Establish a database connection."""
    return mysql.connector.connect(**db_config)


def is_admin():
    """Check if the current user is an admin."""
    return session.get('role') == 'admin'


def allowed_file(filename):
    """Check if the uploaded file is allowed based on extension."""
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS



#  Context Processor
# Injects instructor name and image globally into templates.

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



# ROOM MANAGEMENT ROUTES



#  List All Rooms
@rooms_bp.route('/')
def list_rooms():
    if not is_admin():
        return redirect(url_for('login'))

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM rooms")
    rooms = cursor.fetchall()
    conn.close()

    # Extract distinct programs for search/suggestions
    programs = sorted(set(room['program'] for room in rooms if room.get('program')))

    return render_template("admin/rooms.html", rooms=rooms, programs=programs)


#  Add Room

@rooms_bp.route('/add', methods=['GET', 'POST'])
def add_room():
    if not is_admin():
        return redirect(url_for('login'))

    # Fetch program suggestions
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT DISTINCT program FROM rooms WHERE program IS NOT NULL AND program != ''")
    programs = [row['program'] for row in cursor.fetchall()]
    conn.close()

    # Handle POST submission
    if request.method == 'POST':
        room_number = request.form['room_number']
        room_type = request.form['room_type']
        program = request.form['program'] or None

        # Handle image upload
        image_file = request.files.get('image')
        image_filename = None
        if image_file and allowed_file(image_file.filename):
            filename = secure_filename(image_file.filename)
            upload_folder = os.path.join(current_app.root_path, 'static', 'room_images')
            os.makedirs(upload_folder, exist_ok=True)
            image_file.save(os.path.join(upload_folder, filename))
            image_filename = filename

        # Insert into DB
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO rooms (room_number, room_type, program, image) VALUES (%s, %s, %s, %s)",
            (room_number, room_type, program, image_filename)
        )
        conn.commit()
        conn.close()

        flash("Room added successfully", "success")
        return redirect(url_for('rooms.list_rooms'))

    return render_template("admin/add_room.html", programs=programs)



#  Edit Room

@rooms_bp.route('/edit/<int:room_id>', methods=['GET', 'POST'])
def edit_room(room_id):
    if not is_admin():
        return redirect(url_for('login'))

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    # Fetch program suggestions
    cursor.execute("SELECT DISTINCT program FROM rooms WHERE program IS NOT NULL AND program != ''")
    programs = [row['program'] for row in cursor.fetchall()]

    # Fetch room details
    cursor.execute("SELECT * FROM rooms WHERE room_id = %s", (room_id,))
    room = cursor.fetchone()

    if not room:
        conn.close()
        flash("Room not found", "error")
        return redirect(url_for('rooms.list_rooms'))

    # Handle POST submission
    if request.method == 'POST':
        room_number = request.form['room_number']
        room_type = request.form['room_type']
        program = request.form['program'] or None

        # Handle image upload
        image_file = request.files.get('image')
        image_filename = None
        if image_file and allowed_file(image_file.filename):
            filename = secure_filename(image_file.filename)
            upload_folder = os.path.join(current_app.root_path, 'static', 'room_images')
            os.makedirs(upload_folder, exist_ok=True)
            image_file.save(os.path.join(upload_folder, filename))
            image_filename = filename

        # Update DB (with or without new image)
        if image_filename:
            cursor.execute(
                "UPDATE rooms SET room_number=%s, room_type=%s, program=%s, image=%s WHERE room_id=%s",
                (room_number, room_type, program, image_filename, room_id)
            )
        else:
            cursor.execute(
                "UPDATE rooms SET room_number=%s, room_type=%s, program=%s WHERE room_id=%s",
                (room_number, room_type, program, room_id)
            )

        conn.commit()
        conn.close()

        flash("Room updated successfully", "success")
        return redirect(url_for('rooms.list_rooms'))

    conn.close()
    return render_template("admin/edit_room.html", room=room, programs=programs)



# Delete Room

@rooms_bp.route('/delete/<int:room_id>', methods=['POST'])
def delete_room(room_id):
    if not is_admin():
        return redirect(url_for('login'))

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM rooms WHERE room_id = %s", (room_id,))
    conn.commit()
    conn.close()

    flash("Room deleted successfully", "success")
    return redirect(url_for('rooms.list_rooms'))
