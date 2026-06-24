# Phila — Documents feature plan

Last updated: 24 June 2026

This document is the source of truth for the multi-phase rebuild of the
documents feature on phila-web, phila-backend, and phila-app. It
captures every decision made during the brainstorm, the phase
sequencing, the regulatory considerations, and the known edge cases
that have been deliberately deferred. Read this before starting any
phase. Update it as decisions change.

---

## Strategic framing

### Why we're doing this

Custom doctor-uploaded `.docx` templates already exist in the codebase,
but they're presented as a secondary path next to Phila's standard
templates. The brainstorm reframed this: **custom templates are the
right primary path, and Phila standards are the fallback for new
doctors who don't have their own yet.**

Three reasons this matters more than "documents look unique per
practice":

1. **Compliance-by-deferral.** Practices already have HPCSA-compliant
   templates their lawyers and billing people are comfortable with.
   Forcing them to adopt Phila's templates means re-validating
   compliance — a small friction that quietly creates onboarding
   resistance. Letting them upload what they already use removes that
   friction entirely. This is a real moat.

2. **Receptionist workflow.** Sick notes get printed and signed dozens
   of times a day. The receptionist already has the practice's
   letterhead in a Word doc somewhere. Letting *her* upload it once and
   never think about it again is the receptionist-day-easier thesis
   in action.

3. **Next-best-alternative math.** The doctor's current workflow is:
   open Word, paste in patient name, print. Phila's bar isn't "have
   nice templates" — it's "do that faster than Word does." Same-
   templates-but-pre-filled beats ours-but-prettier every time.

### The documents feature as a funnel, not a transaction

The patient home screen tracks water, sleep, energy, streaks, mood, and
AI features — meaning the app has a real reason for patients to return
daily even between appointments. This changes the strategic weight of
the document-delivery hook:

- The document hook gets the patient to download.
- The daily health tracking is what makes them stay.

Both halves matter. Without the hook, manually-booked patients never
enter the funnel. Without the daily-use surface, they uninstall after
one transaction. The phase plan below is designed so both halves work.

---

## Architecture decisions (locked)

These are settled. Do not relitigate without strong new evidence.

### Document sending

- **No auto-send.** After document generation, doctor sees Print / Send
  via WhatsApp / Send via Email / Download buttons. Doctor is the
  conscious actor for every patient communication.
- **Document is always saved to the patient's Phila record**, regardless
  of whether it's sent externally. This is the foundation for patient-
  app delivery.
- **WhatsApp/Email send is per-document, manual, doctor-initiated.**
  Receptionist can do this too once the doc is generated.

### Patient registration & manual-booking funnel

- **Phone-first registration with WhatsApp OTP, SMS fallback.**
  Default channel is WhatsApp ('didn't get it? Send via SMS' link as
  escape hatch). Twilio handles both off the same number.
- **Phone is the linking identifier** between manually-created bookings
  and freshly-registered patient accounts. Normalisation to
  `+27XXXXXXXXX` is mandatory and must use the same normaliser
  everywhere (already exists for WhatsApp intake — reuse it).
- **Linking happens atomically at registration.** Same DB transaction
  as user creation. If linking fails, registration rolls back.
- **"We found your stuff" reveal is a modal**, not a separate screen.
  Modal pops on first home-screen load post-registration, dismisses
  cleanly.

### Parent / child / multi-patient

- **One Phila account can have multiple patients under it.** Parent's
  account holds their own visits + their kids' visits. Implemented
  from day one (option b from the brainstorm) — not deferred, even
  though it's the harder call.
- **Manual-booking form splits "Booked by" (contact person — the
  parent) from "Patient" (the actual patient — could be same person,
  could be a child).** Phone lives on "Booked by"; patient identity is
  separate.
- **Switch-patient affordance** required in the patient app when an
  account has more than one patient. Small picker on the appointment
  detail screen + somewhere in the profile.

### Manual-booking patient comms

- **No YES/NO confirmation.** If a practice manually booked someone,
  they already know they're coming — confirmation via WhatsApp is
  asking them to re-consent to a thing they already agreed to in
  person.
- **Booking creation message is purely informational + an app-download
  hook.** "Hi Sipho, [practice] has booked you in for [date]. Download
  Phila to manage this and access documents from your visit."
- **Reschedule / cancel notifications still go out** if the practice
  changes a manual booking after creation.

### Per-patient clinical document storage (x-rays, scans)

- **Explicitly deferred.** Storing clinical artefacts on Phila pushes
  the platform across the EHR / SaMD line and is not a "build it and
  worry later" feature. Will reconsider only if a real pilot doctor
  requests it.

---

## Phase 0 — Deploy pipeline (prerequisite, do first)

**Goal:** Auto-run `alembic upgrade head` on every Railway deploy.
Verify the Twilio Data Processing Agreement is in place.

**Why this is Phase 0 and not optional:** Phases 2, 3, 4, 5 all involve
database migrations. We have already lost an hour debugging the
favorites table because Railway does not auto-migrate on deploy.
This will happen again, and worse, in any of the next phases.

**Scope:**

1. Configure Railway's deploy command to run `alembic upgrade head`
   before starting the FastAPI server. Exact mechanism to be confirmed
   by reading the current Railway service config — paste the Settings
   → Deploy section first, don't guess.
2. Confirm Twilio Data Processing Agreement exists. If not, get it in
   place before Phase 3 ships. Paperwork, not code, but blocking.

**Files touched:** Railway configuration (no code repo changes likely;
possibly a `railway.toml` or `nixpacks.toml` if those are how Railway
release commands are configured for this project).

**Definition of done:**

- A migration committed to main automatically applies to prod on the
  next deploy.
- A migration that fails causes the deploy to fail loudly, not
  silently launch an app that 500s on first query.
- DPA confirmed in place.

**Estimated time:** 15 minutes for the Railway change, plus DPA
verification (depends on whether one is already signed).

---

## Phase 1 — Document upload UX improvements

**Goal:** Make custom templates the obviously-primary path, and make
it impossible for a doctor to upload a broken template without realising it.

**Why first feature phase:** Pure frontend work on the doctor
dashboard. No backend changes, no patient app changes, no regulatory
considerations, no new endpoints. Zero risk of breaking production.
Ships in one session.

### In scope

1. **Reorder the sidebar in `DocumentsPage.tsx`.** "My templates"
   promoted to the top. "Standard templates" demoted to "Phila
   starters" further down, framed as fallback.

2. **Info icon next to "Upload .docx" button.** Tappable `(i)`, opens
   a modal containing:
   - Plain-language explanation (3–4 sentences)
   - Option B side-by-side illustration — template with
     `{{patient_name}}` on the left, generated output with "Sipho
     Mthembu" on the right, with a visual arrow showing the
     substitution
   - Placeholder naming tips (the auto-fill heuristic, explained
     simply)
   - Header/footer warning: "keep your logo in the header; put
     placeholders in the body"
   - Smart-quotes warning: "type `{{}}` exactly — Word sometimes
     changes curly brackets to other characters, which breaks the
     template"

3. **Post-upload feedback.** After upload, the UI shows:
   - List of placeholders found
   - Green check next to ones Phila will auto-fill, grey dot next to
     ones the doctor fills manually
   - Total count of placeholders detected

4. **Post-generation action bar.** After a document is generated and
   saved:
   - Print button (works)
   - Send via WhatsApp button (disabled with "coming in Phase 3")
   - Send via Email button (disabled with "coming in Phase 3")
   - Download button (works — same as existing flow)

5. **"Not yet sent" pill** on documents in the list, signalling docs
   generated but not yet sent. Becomes meaningful in Phase 3 but
   the UI can land now.

### Out of scope

- No automatic sending
- No backend placeholder-extraction changes
- No backend substitution changes
- No patient-app changes
- No onboarding wizard changes (deliberately — too overwhelming for
  signup)

### Files touched

- `phila-web/src/pages/doctor/DocumentsPage.tsx` — main file
- New: `phila-web/src/components/documents/TemplateInfoModal.tsx`
- New: `phila-web/src/components/documents/TemplateExampleIllustration.tsx`
  (the side-by-side SVG)

### Risks during build

- Info modal must match the existing modal styling in `DocumentsPage`
  (inline styles using `colors` from `useTheme()`, not Tailwind or
  styled-components). Style consistency is on the modal-builder.
- "Will auto-fill" indicators require knowing which heuristic patterns
  match — replicated client-side from `buildTemplateValues` for now.
  Backend can own this in a future polish phase if it gets out of sync.
- SVG illustration should be real SVG, not an image — theme-aware via
  `colors.primary`, easier to maintain, no image hosting question.

### Definition of done

- Doctor opens Documents, sees "My templates" as the primary section
- Taps `(i)`, sees the visual explanation, understands the format
- Uploads a `.docx`, sees exactly which placeholders Phila detected
  and which will auto-fill
- Generates a document, sees Print/Send/Download buttons (Send
  disabled with "coming in Phase 3")
- Sees "Not yet sent" pill on the doc in the list

**Estimated time:** one session.

---

## Phase 2 — Backend robustness for custom templates

**Goal:** Make the upload and generation pipeline survive real-world
Word documents from real doctors.

**Why this position:** Phase 1 makes the upload feature prominent.
Once prominent, more doctors use it. Once more doctors use it, the
gotchas start to bite. Harden the backend before user volume grows on
this feature, not after.

**Prerequisite:** Read `app/api/routes/documents.py` and any
`python-docx` extraction/substitution code first. Do not harden blind.

### In scope

1. **Smart-quote normalisation in upload.** Before regex runs, normalise
   the document text — replace unicode `{`, `}`, `"`, `'` variants with
   ASCII equivalents. Stops Word autocorrect from silently breaking
   doctor templates. Idempotent.

2. **Run-splitting handling.** The python-docx gotcha: placeholders
   typed and then edited mid-string get split across multiple `<w:r>`
   runs. Regex on `paragraph.text` sees the placeholder; the
   substitution loop on individual runs misses it. Fix: re-join runs
   in each paragraph before substitution. Standard pattern — search
   "python-docx replace placeholder across runs."

3. **Header and footer scanning.** Extend extraction and substitution
   to `document.sections[i].header` and `.footer`. Doctors will put
   `{{practice_address}}` or `{{date_issued}}` in headers.

4. **Validation on upload.** Return errors and warnings, not just
   success:
   - Zero placeholders → warning ("Phila will save this but won't be
     able to pre-fill any fields")
   - Corrupted/password-protected file → useful error, not 500

5. **Preview-with-sample-data endpoint.**
   `POST /documents/templates/{id}/preview` — fills template with
   dummy data, returns the rendered `.docx`. Doctor can verify their
   template works before using it on a real patient.

6. **`app/models/__init__.py` audit.** Every model file under
   `app/models/` must have a corresponding import in `__init__.py`.
   This is the root cause of the favorites empty-migration bug — fix
   it once, prevent the entire class of bug from happening on every
   future model.

### Out of scope

- No WhatsApp send logic (Phase 3)
- No patient-facing endpoints (Phase 4)
- No frontend changes

### Files touched (confirm by reading first)

- `phila-backend/app/api/routes/documents.py`
- `phila-backend/app/services/` — wherever python-docx
  substitution lives
- `phila-backend/app/models/__init__.py`

### Migration risk

Probably none — this is logic changes, not schema. Confirm by reading.

### Risks during build

- Run-splitting fix is the most likely regression source. Existing
  substitution code might already partially handle it, or handle body
  paragraphs but not table cells. Read carefully.
- Preview endpoint needs dummy data — reuse `buildTemplateValues`
  heuristic with placeholder names as values where appropriate.
- Smart-quote normalisation must be idempotent (re-running it on
  already-normalised input produces the same output).

### Definition of done

- Template with smart quotes from Word autocorrect → works
- Template where doctor edited mid-placeholder → works
- Placeholder in Word Header → detected and substituted
- Empty document upload → useful error, not 500
- Doctor can preview a template against dummy data
- `app/models/__init__.py` imports every model file

**Estimated time:** one session, longer than Phase 1.

---

## Phase 3 — WhatsApp / Email send infrastructure

**Goal:** Wire the Send buttons from Phase 1 into actual delivery.

**Why this position:** Templates are robust (Phase 2) and the UX
directs doctors toward generating (Phase 1). Building send infra for
templates that don't reliably work is bad sequencing.

### In scope

1. **`POST /documents/{id}/send` endpoint.** Takes doc ID + channel
   (`whatsapp` or `email`), looks up patient contact info from the
   booking, sends. Returns success/failure with reason.

2. **Twilio WhatsApp document send.** Send the generated PDF as a
   WhatsApp media attachment with accompanying message. Reuses
   existing Twilio integration. Phone normalised to `+27XXXXXXXXX`
   before sending.

3. **`.docx` → `.pdf` conversion before WhatsApp send.** Patients
   mostly can't open `.docx` on a phone. **Decision deferred to Phase 3
   start.** Research Railway-compatible options at that point:
   - `docx2pdf` (requires LibreOffice/Word — won't work on Railway)
   - `pypandoc` (dependency-heavy)
   - CloudConvert API (paid, bulletproof)
   - render docx→HTML→PDF via weasyprint (lighter, might be enough)

4. **Email send.** SendGrid or similar. Confirm whether email infra
   already exists in the project; if not, this is a real new
   integration and might warrant its own mini-phase.

5. **State tracking.** New columns on `documents` table:
   `sent_via_whatsapp_at`, `sent_via_email_at`. The "Not yet sent" pill
   from Phase 1 reads these.

6. **Recall/resend button.** Doctor can mark a sent doc as recalled
   (sends a follow-up WhatsApp: "Please disregard the previous
   document") and re-send a corrected version. This is the unfuck
   path for doctor errors.

7. **Audit log.** Every send action: `document_send_log` table with
   doc id, channel, recipient, timestamp, success/failure. Matters
   for POPIA — if a patient disputes receipt, we need a record.

### Out of scope

- Patient-app display (Phase 4)
- Manual-booking comms (Phase 5)

### Files touched

- `phila-backend/app/api/routes/documents.py` — new send endpoint
- `phila-backend/app/services/whatsapp.py` — document-send method
- New: `phila-backend/app/services/pdf_converter.py`
- `phila-backend/app/models/document.py` — sent timestamps
- New migration: sent columns + audit log table
- `phila-web/src/pages/doctor/DocumentsPage.tsx` — wire Send buttons
- New: `phila-web/src/components/documents/SendConfirmation.tsx`

### Migration risk

Real this time. Two schema additions, both must reach prod. Phase 0
must be in place before this ships.

### Risks during build

- `.docx → .pdf` decision is the biggest technical question. Don't
  build the whole flow then discover the conversion doesn't work on
  Railway. Decide first.
- WhatsApp media messages have a 16MB limit. Clinical docs are tiny
  but handle the edge.
- **Twilio template approval.** Sending a freeform media message
  outside a 24h conversation window requires a Twilio-approved
  template. Set this up in advance — 24–48h lead time.
- **POPIA pre-check.** Sending clinical docs via WhatsApp is a notable
  processing event. DPA must be in place (Phase 0 dependency).
- Patient consent: the patient phone number was given to the practice
  for healthcare communications — probably sufficient consent under
  POPIA's healthcare exemption, but worth verifying with an attorney.
  Not blocking, but real.

### Definition of done

- Doctor generates a sick note → clicks Send via WhatsApp → patient
  gets WhatsApp with PDF attached
- Doctor sends wrong doc → clicks Recall → patient gets follow-up
- Document list shows "Sent via WhatsApp 14:32" instead of "Not yet
  sent"
- Audit log records every send

**Estimated time:** two sessions probably. PDF conversion alone could
take a session.

---

## Phase 4 — Patient app document inbox + phone-based linking

**Goal:** Patients see their documents in the app. Patients who didn't
have the app at the time of their visit can download and instantly see
their history. Multi-patient accounts (parent+kids) supported from day
one.

**Why this position:** Docs are reliable (Phase 2), doctor can send
them (Phase 3), now build the app-side delivery and the registration
funnel.

### In scope

1. **Phone-first registration with WhatsApp OTP + SMS fallback.**
   - Phone-entry screen
   - Default channel: WhatsApp OTP
   - "Didn't get the WhatsApp? Send via SMS instead" link (Twilio
     SMS, ~R0.30/OTP)
   - Set password + full name + optional email after OTP verified
   - All in one short flow

2. **Atomic linking on registration.** On successful registration:
   - Normalise phone to `+27XXXXXXXXX`
   - Check if existing user has this phone → reject (account exists)
   - Create user row
   - `UPDATE bookings SET patient_account_id = :new_user_id WHERE booked_by_phone = :phone AND patient_account_id IS NULL`
   - Same for documents (or implicit via booking join)
   - Commit transaction
   - If linker fails, registration rolls back

3. **"We found your stuff" modal.** Pops on first home-screen load
   after registration:
   - "Welcome, Sipho. We found 1 upcoming appointment and 2 past
     visits from [practice]."
   - Lists them as tappable links
   - Dismisses cleanly
   - If they have zero history (downloaded without ever being manually
     booked), skip the modal — standard empty state

4. **`GET /me/appointments/{id}` endpoint** (or extend existing
   booking detail endpoint to include documents). Returns booking +
   docs + intake brief + practice info + ratings prompt + reschedule/
   cancel actions if upcoming.

5. **`AppointmentDetailScreen` in phila-app.** New screen, reached by
   tapping an appointment in `AppointmentsScreen`. Shows everything
   for that visit in one place: doctor info, practice info, intake
   brief, documents (tappable to view PDFs), ratings.

6. **Document viewer in the app.** Tap a doc → opens PDF viewer.
   Options: `expo-print`, `expo-document-picker`, `react-native-pdf`.
   Decide at Phase 4 start. Download option to save locally.

7. **Multi-patient support — schema and switch-patient UI.**
   - Bookings table gets `booked_by_phone` (the contact) and
     `patient_name` / `patient_dob` (the actual patient — possibly
     different from booker)
   - Patient is a flexible concept: sometimes a user account,
     sometimes just a name attached to a contact
   - Switch-patient picker on `AppointmentDetailScreen` and in
     profile when account has >1 patient

8. **Push notification when a new doc is added.** Patient gets a
   notification, opens Phila, sees the sick note waiting.

### Out of scope

- Auto-booking-creation messages (Phase 5)

### Files touched

- `phila-backend/app/api/routes/auth.py` — phone-first registration,
  OTP flow, linking transaction
- `phila-backend/app/api/routes/documents.py` — `/me` scoped endpoint
- `phila-backend/app/api/routes/bookings.py` — extend booking detail
- `phila-backend/app/services/whatsapp.py` — OTP send
- `phila-backend/app/services/sms.py` (likely new) — SMS OTP fallback
- New migrations: nullable `patient_account_id` on bookings, add
  `booked_by_phone`, `patient_name`, `patient_dob`
- `phila-app/src/screens/patient/AppointmentsScreen.tsx` — make items
  tappable
- New: `phila-app/src/screens/patient/AppointmentDetailScreen.tsx`
- New: `phila-app/src/screens/patient/DocumentViewerScreen.tsx`
- New: `phila-app/src/components/auth/PhoneEntryScreen.tsx`
- New: `phila-app/src/components/auth/OTPScreen.tsx`
- New: `phila-app/src/components/home/WelcomeBackModal.tsx` (the
  "we found your stuff" modal)
- `phila-app/src/api/documents.ts` (new file)

### Schema preconditions to confirm before Phase 4

1. Is `bookings.patient_id` currently NOT NULL? If so, small migration
   to make it nullable (or restructure to `patient_account_id` nullable
   + `patient_name` always required).
2. Does the bookings table currently have any phone field for
   manually-created bookings? If not, that's the
   `booked_by_phone` migration.

### Risks during build

- Phone normalisation must be ironclad. Inconsistent normalisation
  between booking creation and patient registration silently breaks
  the entire feature. Reuse the existing normaliser from WhatsApp
  intake — same exact function, not a reimplementation.
- PDF viewing on React Native is harder than it should be. Plan for
  half a session of just getting the viewer working cleanly across
  iOS and Android.
- WhatsApp OTP requires a Twilio-approved template for the first-
  contact message. 24–48h lead time.
- SMS OTP needs Twilio SMS configured (not just WhatsApp). Confirm
  it's set up on the account; if not, that's account-level setup
  work in addition to the code.

### Known edge cases (acknowledged, not solved here)

These are real and will eventually hit production. They are
deliberately deferred — none block Phase 4 — but document them so
they surface during pilot use.

1. **Phone number recycling.** SA prepaid SIMs get recycled. Patient
   A's old number may become Patient B's new number. Patient B gets
   blocked from registering on their own phone. **Mitigation when it
   appears:** add an account-recovery path that proves new identity
   via SMS OTP + secondary signal, then transfers the number.

2. **Typo'd phone at registration.** Patient types a digit wrong,
   creates an account, sees an empty app. **Mitigation:** the "we
   found X bookings" modal counts — even saying "0 bookings found"
   gives the patient signal something's off if they were expecting
   their history.

3. **Account-already-exists at manual-booking creation.** Practice
   manually books an existing Phila user. **Mitigation:** dashboard
   should check for existing accounts at booking creation time and
   link immediately if one exists. Belongs in Phase 5 but flagged here.

### Definition of done

- Practice manually creates a booking for Sipho → Sipho's visit
  happens → doctor generates a sick note → it's saved to Sipho's
  record
- Sipho downloads Phila, enters his phone, completes WhatsApp OTP,
  sets password + name
- "We found 1 upcoming appointment and 2 past visits" modal shows
- Sipho taps a past visit → sees the full appointment detail screen
  → taps his sick note → views the PDF → downloads it
- New doc generated later → Sipho gets a push notification
- Parent registers with their phone — sees their visits AND their
  child's visits (multi-patient picker shown if >1 patient)

**Estimated time:** two sessions. Phone-linking + OTP is one session;
appointment-detail screen + PDF viewer is one session.

---

## Phase 5 — Booking-creation comms for manual bookings

**Goal:** Manually-booked patients get a WhatsApp message at booking
creation, introducing Phila and prompting app download.

**Why this position:** Until Phase 4 is shipped, sending "download
Phila to manage your booking" leads to a broken experience — the
patient downloads and sees nothing because their booking can't be
displayed. Phase 4 is the prerequisite that makes Phase 5 deliver value.

### In scope

1. **Booking creation hook.** When practice creates a booking via
   dashboard, if booking has a `booked_by_phone` but no associated
   user account, fire WhatsApp message.

2. **The message.** "Hi [first_name], [practice_name] has booked you
   in for [date] at [time]. Download the Phila app to manage your
   bookings and access any documents from your visit: [app link]"

   No YES/NO confirmation. Booking is confirmed at creation.

3. **24h reminder.** Already exists for app-confirmed bookings;
   confirm it also fires for manually-created ones. If not, extend.

4. **Reschedule / cancel notifications.** If practice changes a manual
   booking, patient gets notified. (Patient can't reply to confirm or
   reject — they're not in the comms loop, just informed.)

5. **Existing-account detection at booking creation.** Dashboard form
   checks for an existing Phila account with matching phone. If found:
   - Link booking directly to that account (`patient_account_id` set)
   - Send a different message: "[practice] has booked you for [date]
     — open Phila to see details"
   - No app-download CTA, since they already have it

6. **Dashboard form changes:** add "Booked by" / "Patient" split.
   "Booked by" gets the phone number; "Patient" gets name and DOB.
   They can be the same person (default) or different.

7. **Twilio template approval.** Submit the booking-confirmation
   template early — 24–48h lead time.

### Out of scope

- No reply handling for the manual-booking message (intentional)
- No new document features

### Files touched

- `phila-backend/app/api/routes/bookings.py` — booking creation hook
- `phila-backend/app/services/whatsapp.py` — booking-confirmation
  send method
- `phila-backend/app/services/conversation_state.py` — confirm
  existing logic handles or ignores any incoming replies to this
  template
- Celery task config (if 24h reminder isn't already there for manual
  bookings)
- `phila-web/src/pages/doctor/` — manual-booking form: add
  "Booked by" / "Patient" split
- Possibly new migration if a `manual_booking_confirmations` table
  is needed for tracking, though minimal

### Risks during build

- **Twilio template approval lead time is calendar time, not work
  time.** Submit before starting code work.
- Phone normalisation (again). Receptionist enters "082 555 1234";
  Phila must normalise to `+27825551234` consistently with the patient
  app's registration flow.
- Wrong-phone case: practice enters wrong number, patient gets a
  WhatsApp about an appointment they didn't make. They reply confused.
  Conversation state should route this to a fallback handler that
  flags the practice ("recipient says this number is wrong, please
  verify").
- Existing-account check needs phone normalisation on both sides
  (input normalised before lookup; stored phone already normalised).

### Definition of done

- Practice creates a manual booking for Sipho (no existing account)
  → within 30 seconds, Sipho gets a WhatsApp from Phila
- Sipho downloads Phila weeks later → sees his booking history
  (Phase 4 dependency)
- Practice creates a manual booking for an existing Phila user → no
  app-download CTA, link is direct
- Practice reschedules a manual booking → Sipho gets notified

**Estimated time:** one session, plus Twilio template approval lead time.

---

## Sequencing summary

```
Phase 0  →  Phase 1  →  Phase 2  →  Phase 3  →  Phase 4  →  Phase 5
deploy      doc UX      backend     send         patient     booking
pipeline    + info      hardening   infra        app +       creation
            modal                                linking     comms
```

**Re-evaluation gate after Phase 1.** Stop. Look at the pilot doctor's
usage. Decide whether Phases 2–5 ship in order, get re-ordered based
on what the pilot reveals, or get partially deferred. The plan is the
default; reality wins if it diverges.

---

## Out-of-scope items (named, deferred)

These came up during brainstorm and were explicitly excluded. Worth
knowing why they're not here so they don't get re-litigated by accident:

- **Per-patient clinical document storage (x-rays, scans).** Pushes
  Phila across the EHR/SaMD line. Defer until a pilot doctor
  specifically asks.
- **AI-generated full document body from intake brief.** Current
  field-level pre-fill is the right amount of AI. Generating clinical
  language puts Phila across the SAHPRA line.
- **Doctor-to-doctor referral marketplace inside Phila.** Regulatory
  and consent nightmare. 12-month idea, not now.
- **Federated learning on clinical documents across practices.** Hard
  no — POPIA and trust nightmare.
- **Cross-practice patient identity.** A patient using two different
  practices both on Phila — currently each practice creates their own
  manual booking under their own phone record. Multi-practice unified
  identity is a real future feature but not in this plan.

---

## Open questions to revisit

1. **`.docx → .pdf` conversion mechanism** — decide at Phase 3 start.
2. **Email infrastructure** — confirm at Phase 3 start whether
   SendGrid or equivalent already exists; if not, scope a mini-phase.
3. **PDF viewing library on React Native** — decide at Phase 4 start.
4. **Existing 24h reminder behaviour for manual bookings** — confirm
   at Phase 5 start whether the Celery task already fires for these
   or needs to be extended.

---

## Working conventions for this work

- All file writes via PowerShell `Set-Content` heredoc scripts, ASCII-
  only inside `Write-Host` lines.
- Full-file rewrites over regex/`-replace` patches on files touched
  across multiple phases.
- Never blindly rewrite files whose current full content hasn't been
  seen in the active session. Paste the file, then the rewrite.
- AI-parsing/generation logic lives in the FastAPI backend, not the
  app. App captures input and displays/applies the result.
- Human review step before any AI output is applied to real data —
  never auto-apply.
- Phone normalisation: `+27XXXXXXXXX` everywhere. One normaliser,
  shared.

---

## Memory aids — things this conversation has already revealed

- Empty migrations are silent if the model isn't imported into
  `app/models/__init__.py`. This is the root cause of the favorites
  bug. Phase 2 fixes it once.
- Railway does not auto-run `alembic upgrade head` on deploy. Phase 0
  fixes it.
- The patient home screen has water/sleep/energy/streaks/mood/AI as
  daily-use surface — meaning Phila has a real reason to retain
  patients beyond a single document delivery. This changes the
  funnel math.
- Today, the only WhatsApp touchpoint is the AI intake assistant after
  a patient confirms a booking *in the app*. Manually-booked patients
  currently get zero Phila messages. Phase 5 closes that gap.
- The receptionist is the daily operator on the doctor side, not the
  doctor. Every feature in this plan should be evaluated against:
  does this make the receptionist's day easier?
