"""Top-level aliases for nested choice classes.

drf-spectacular's ``ENUM_NAME_OVERRIDES`` needs an importable dotted path, and
``import_string`` cannot reach a class nested inside a model, so the status
choice sets are re-exported here.
"""

from academics.models import Attendance, Enrollment, Student
from admissions.models import Application

STUDENT_STATUS = Student.Status
ENROLMENT_STATUS = Enrollment.Status
ATTENDANCE_STATUS = Attendance.Status
APPLICATION_STATUS = Application.Status
