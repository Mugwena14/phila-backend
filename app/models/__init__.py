"""
Import every model here so that Alembic autogenerate can see them in Base.metadata.

If a new model file is added under app/models/ and NOT imported here, autogenerate
will produce an empty migration (pass / pass) and silently miss the schema change.
This is exactly what happened with favorite_doctors. Do not let it happen again.
"""
from app.models.booking import Booking
from app.models.doctor import Doctor
from app.models.document_template import DocumentTemplate
from app.models.favorite_doctor import FavoriteDoctor
from app.models.intake_brief import IntakeBrief
from app.models.notification import Notification
from app.models.patient_document import PatientDocument
from app.models.patient_health_summary import PatientHealthSummary
from app.models.patient_medication import PatientMedication
from app.models.patient_profile import PatientProfile
from app.models.rating import Rating
from app.models.slot import Slot
from app.models.user import User
from app.models.waitlist import Waitlist
from app.models.working_hours import WorkingHours

__all__ = [
    "Booking",
    "Doctor",
    "DocumentTemplate",
    "FavoriteDoctor",
    "IntakeBrief",
    "Notification",
    "PatientDocument",
    "PatientHealthSummary",
    "PatientMedication",
    "PatientProfile",
    "Rating",
    "Slot",
    "User",
    "Waitlist",
    "WorkingHours",
]
