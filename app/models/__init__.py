"""
Every SQLAlchemy model must be imported here.

Alembic's autogenerate compares Base.metadata against the database. Models that
aren't imported into the metadata at generation time are invisible - autogenerate
produces an empty upgrade() with `pass`, the migration "applies" silently, and
the table that should have been created simply never exists in prod.

This is what caused the favorites table 500s on 23 June 2026. The lesson:
every new model file in this directory needs a line here. If you add a new
model and forget, the next autogenerate is silent garbage.
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
