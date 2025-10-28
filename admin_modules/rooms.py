from flask import Blueprint, render_template, request, redirect, url_for, flash, session, current_app
import mysql.connector
import os
from werkzeug.utils import secure_filename

rooms_bp = Blueprint('rooms', __name__, url_prefix='/admin/rooms')

db_config = {
    'host': 'localhost',
    'user': 'root',
    'password': '',
    'database': 'iload'
}

ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg'}

def get_db_connection():
    return mysql.connector.connect(**db_config)

def is_admin():
    return session.get('role') == 'admin'

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# Inject instructor's name for sidebar
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


# List all rooms
@rooms_bp.route('/')
def list_rooms():
    if not is_admin():
        return redirect(url_for('login'))
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM rooms")
    rooms = cursor.fetchall()
    conn.close()
    return render_template("admin/rooms.html", rooms=rooms)

# Add a room
@rooms_bp.route('/add', methods=['GET', 'POST'])
def add_room():
    if not is_admin():
        return redirect(url_for('login'))

    if request.method == 'POST':
        room_number = request.form['room_number']
        room_type = request.form['room_type']

        # Handle image upload
        image_file = request.files.get('image')
        image_filename = None
        if image_file and allowed_file(image_file.filename):
            filename = secure_filename(image_file.filename)
            upload_folder = os.path.join(current_app.root_path, 'static', 'room_images')
            os.makedirs(upload_folder, exist_ok=True)
            image_file.save(os.path.join(upload_folder, filename))
            image_filename = filename

        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO rooms (room_number, room_type, image) VALUES (%s, %s, %s)",
            (room_number, room_type, image_filename)
        )
        conn.commit()
        conn.close()
        flash("Room added successfully")
        return redirect(url_for('rooms.list_rooms'))

    return render_template("admin/add_room.html")

# Edit a room
@rooms_bp.route('/edit/<int:room_id>', methods=['GET', 'POST'])
def edit_room(room_id):
    if not is_admin():
        return redirect(url_for('login'))

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    if request.method == 'POST':
        room_number = request.form['room_number']
        room_type = request.form['room_type']

        # Handle image upload
        image_file = request.files.get('image')
        image_filename = None
        if image_file and allowed_file(image_file.filename):
            filename = secure_filename(image_file.filename)
            upload_folder = os.path.join(current_app.root_path, 'static', 'room_images')
            os.makedirs(upload_folder, exist_ok=True)
            image_file.save(os.path.join(upload_folder, filename))
            image_filename = filename

        if image_filename:
            cursor.execute(
                "UPDATE rooms SET room_number=%s, room_type=%s, image=%s WHERE room_id=%s",
                (room_number, room_type, image_filename, room_id)
            )
        else:
            cursor.execute(
                "UPDATE rooms SET room_number=%s, room_type=%s WHERE room_id=%s",
                (room_number, room_type, room_id)
            )

        conn.commit()
        conn.close()
        flash("Room updated successfully")
        return redirect(url_for('rooms.list_rooms'))

    cursor.execute("SELECT * FROM rooms WHERE room_id = %s", (room_id,))
    room = cursor.fetchone()
    conn.close()
    return render_template("admin/edit_room.html", room=room)

# Delete a room
@rooms_bp.route('/delete/<int:room_id>', methods=['POST'])
def delete_room(room_id):
    if not is_admin():
        return redirect(url_for('login'))

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM rooms WHERE room_id = %s", (room_id,))
    conn.commit()
    conn.close()
    flash("Room deleted successfully")
    return redirect(url_for('rooms.list_rooms'))
