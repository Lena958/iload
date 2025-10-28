from .admin_routes import admin_bp
from .instructors import instructors_bp
from .subjects import subjects_bp
from .rooms import rooms_bp
from .schedules import schedules_bp
from .auto_scheduler import auto_scheduler_bp
from .conflicts import conflicts_bp
from .feedback import feedback_bp
from .load import load_bp
from .dashboard import dashboard_bp
from .courses import courses_bp

__all__ = [
    'admin_bp', 'instructors_bp', 'subjects_bp',
    'rooms_bp', 'schedules_bp', 'auto_scheduler_bp',
    'conflicts_bp', 'feedback_bp', 'load_bp', 'dashboard_bp',
    'courses_bp',
]
