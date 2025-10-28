import os
from flask import Blueprint, render_template, redirect, url_for, session, request, flash, current_app
import mysql.connector
from werkzeug.utils import secure_filename
import re
from werkzeug.security import generate_password_hash, check_password_hash

# Database config
db_config = {
    'host': 'localhost',
    'user': 'root',
    'password': '',
    'database': 'iload'
}

admin_bp = Blueprint('admin', __name__, url_prefix='/admin')

ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}

def is_admin():
    return session.get('role') == 'admin'

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

@admin_bp.route('/profile', methods=['GET', 'POST'])
def profile():
    if not is_admin():
        return redirect(url_for('login'))

    username = session.get('username')

    try:
        connection = mysql.connector.connect(**db_config)
        cursor = connection.cursor(dictionary=True)

        if request.method == 'POST':
            # Get form data
            new_name = request.form.get('name')
            new_department = request.form.get('department')
            new_max_load = request.form.get('max_load_units')

            current_password = request.form.get('current_password')
            new_password = request.form.get('new_password')
            confirm_password = request.form.get('confirm_password')

            image_file = request.files.get('image')
            image_filename = None

            # Handle image upload
            if image_file and image_file.filename != '':
                if allowed_file(image_file.filename):
                    filename = secure_filename(image_file.filename)
                    upload_folder = os.path.join(current_app.root_path, 'static/uploads')
                    os.makedirs(upload_folder, exist_ok=True)

                    # Optionally, generate unique filename to avoid overwriting
                    # import uuid
                    # filename = f"{uuid.uuid4().hex}_{filename}"

                    image_path = os.path.join(upload_folder, filename)
                    image_file.save(image_path)
                    image_filename = filename
                else:
                    flash('Invalid image format! Allowed types: png, jpg, jpeg, gif', 'danger')
                    return redirect(url_for('admin.profile'))

            # Password update logic
            hashed_password = None
            if current_password or new_password or confirm_password:
                # Fetch current password hash from DB
                cursor.execute("SELECT password FROM instructors WHERE username = %s", (username,))
                user = cursor.fetchone()

                if not user:
                    flash('User not found.', 'danger')
                    return redirect(url_for('admin.profile'))

                # Verify current password
                if not current_password or not check_password_hash(user['password'], current_password):
                    flash('Current password is incorrect.', 'danger')
                    return redirect(url_for('admin.profile'))

                # Check if new password and confirmation match
                if new_password != confirm_password:
                    flash('New password and confirmation do not match.', 'danger')
                    return redirect(url_for('admin.profile'))

                # Password complexity validation function
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
                    return redirect(url_for('admin.profile'))

                # Hash the new password
                hashed_password = generate_password_hash(new_password)

            # Build SQL update query dynamically
            update_fields = ['name = %s', 'department = %s', 'max_load_units = %s']
            update_values = [new_name, new_department, new_max_load]

            if image_filename:
                update_fields.append('image = %s')
                update_values.append(image_filename)

            if hashed_password:
                update_fields.append('password = %s')
                update_values.append(hashed_password)

            update_values.append(username)  # For WHERE clause

            update_query = f"UPDATE instructors SET {', '.join(update_fields)} WHERE username = %s"
            cursor.execute(update_query, tuple(update_values))

            connection.commit()
            flash('Profile updated successfully!', 'success')
            return redirect(url_for('admin.profile'))

        else:  # GET method - fetch profile data
            select_query = "SELECT name, department, max_load_units, username, image FROM instructors WHERE username = %s"
            cursor.execute(select_query, (username,))
            instructor = cursor.fetchone()

            if not instructor:
                flash('Instructor profile not found.', 'danger')
                return redirect(url_for('login'))

            return render_template(
                'admin/profile.html',
                instructor_name=instructor['name'],
                instructor_username=instructor['username'],
                instructor_department=instructor['department'],
                instructor_max_load=instructor['max_load_units'],
                instructor_image=instructor['image']
            )

    except mysql.connector.Error as err:
        print(f"Database error: {err}")
        flash('An error occurred while processing your request.', 'danger')
        return redirect(url_for('admin.profile'))

    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close() 