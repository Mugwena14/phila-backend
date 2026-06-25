"""
Every SQLAlchemy model must be imported here. See env.py for the reasoning.
"""
from app.models.user import User
from app.models.doctor import Doctor
from app.models.booking import Booking
from app.models.slot import Slot
from app.models.working_hours import WorkingHours
from app.models.rating import Rating
from app.models.waitlist import Waitlist
from app.models.notification import Notification
from app.models.intake_brief import IntakeBrief
from app.models.patient_document import PatientDocument
from app.models.patient_health_summary import PatientHealthSummary
from app.models.patient_medication import PatientMedication
from app.models.patient_profile import PatientProfile
from app.models.document_template import DocumentTemplate
from app.models.favorite_doctor import FavoriteDoctor
from app.models.document_send_log import DocumentSendLog
from app.models.booking_comms_log import BookingCommsLog