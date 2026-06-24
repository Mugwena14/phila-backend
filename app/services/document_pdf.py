"""
Server-side PDF generation for documents being sent to patients.

Two paths:
  - Built-in docs (sick_letter, medical_certificate, referral_letter,
    visit_summary): rendered from JSON content via HTML template -> PDF.
  - Custom template docs (uploaded .docx with placeholder substitution):
    converted via mammoth (docx -> HTML) -> WeasyPrint (HTML -> PDF).

If WeasyPrint isn't available at runtime - either because the Python package
isn't installed (local Windows dev) or because Pango/Cairo system libs are
missing (some Linux deploys without the apt packages from nixpacks.toml) -
both paths fall back gracefully:
  - Built-in -> returns raw HTML as text/html (patient opens in browser)
  - Custom template -> returns the .docx unchanged (WhatsApp can open it)

This is the deliberate trade-off behind option (a) from the brainstorm:
free, in-process, fine for most doc types, occasional formatting loss on
complex Word layouts. Upgrade path is CloudConvert (or similar) - clean
drop-in replacement, same function signature.
"""
import logging
import os
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

PDF_MIME = "application/pdf"
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
HTML_MIME = "text/html"


def _html_to_pdf_bytes(html: str) -> Optional[bytes]:
    """Render HTML to PDF using WeasyPrint. Returns None if WeasyPrint is unavailable."""
    try:
        import weasyprint
        return weasyprint.HTML(string=html).write_pdf()
    except ImportError:
        logger.warning("weasyprint not installed - PDF rendering unavailable")
        return None
    except OSError as e:
        # Missing system libs (pango, cairo). On Windows local dev this is normal.
        logger.warning(f"weasyprint system libs missing: {e} - falling back")
        return None
    except Exception as e:
        logger.error(f"weasyprint failed unexpectedly: {e}")
        return None


def render_template_pdf(docx_path: str) -> Tuple[bytes, str, str]:
    """
    Convert a generated .docx to PDF via mammoth -> WeasyPrint.

    Returns (file_bytes, mime_type, file_extension). On any failure, falls
    back to returning the raw .docx so the doctor's send action still
    succeeds - just with a less-friendly attachment.
    """
    try:
        import mammoth
        with open(docx_path, "rb") as f:
            result = mammoth.convert_to_html(f)
        html = f"""
<!DOCTYPE html>
<html><head><meta charset="utf-8"><style>
body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; font-size: 12pt; line-height: 1.5; color: #111; padding: 40px; }}
h1, h2, h3 {{ color: #0F766E; }}
table {{ border-collapse: collapse; width: 100%; margin: 12px 0; }}
td, th {{ border: 1px solid #ccc; padding: 6px 10px; }}
</style></head><body>{result.value}</body></html>
"""
        pdf = _html_to_pdf_bytes(html)
        if pdf is not None:
            return pdf, PDF_MIME, "pdf"
    except ImportError:
        logger.warning("mammoth not installed - skipping docx->html conversion")
    except Exception as e:
        logger.error(f"docx->pdf conversion failed: {e}")

    # Fallback: send the raw .docx
    logger.info(f"Falling back to .docx attachment for {docx_path}")
    with open(docx_path, "rb") as f:
        return f.read(), DOCX_MIME, "docx"


# ── Built-in doc HTML rendering ───────────────────────────────────────────────
# Mirrors the React DocumentPreview component in DocumentsPage.tsx.
# Keep visually in sync - both render the same data structure (the JSON saved
# under PatientDocument.content for built-in doc_types).

def _builtin_html(doc_type: str, content: dict, doctor_name: str, practice_name: str) -> str:
    """Render a built-in document as HTML, ready for WeasyPrint or browser display."""
    c = content
    title_map = {
        "sick_letter": "Sick Letter",
        "medical_certificate": "Medical Certificate",
        "referral_letter": "Referral Letter",
        "visit_summary": "Visit Summary",
    }
    title = title_map.get(doc_type, doc_type)
    practice = c.get("practice_name") or practice_name
    doctor = c.get("doctor_name") or doctor_name

    # Header block - same letterhead style across all doc types
    header = f"""
<div class="letterhead">
  <div class="practice">{practice}</div>
  <div class="doctor">{doctor}</div>
  {f'<div class="meta">{c["qualification"]}</div>' if c.get("qualification") else ""}
  {f'<div class="meta">HPCSA: {c["hpcsa_number"]}</div>' if c.get("hpcsa_number") else ""}
</div>
<div class="title">{title}</div>
<div class="date">Date: {c.get("date_issued", "")}</div>
"""

    body = ""
    if doc_type == "sick_letter":
        body = f"""
<p>To whom it may concern,</p>
<p>This is to certify that <strong>{c.get("patient_name", "")}</strong> was seen at this
practice on <strong>{c.get("date_of_visit", "")}</strong> and is unfit for work/school
for <strong>{c.get("days_off", "")}</strong> day(s){
  f', from {c["from_date"]}' + (f' to {c["to_date"]}' if c.get("to_date") else '')
  if c.get("from_date") else ''
}.</p>
{f'<p><strong>Reason:</strong> {c["diagnosis"]}</p>' if c.get("diagnosis") else ""}
{f'<p><strong>Notes:</strong> {c["notes"]}</p>' if c.get("notes") else ""}
<p>Signed,</p>
<p><strong>{doctor}</strong></p>
<p>{practice}</p>
"""
    elif doc_type == "medical_certificate":
        qual = f' ({c["qualification"]})' if c.get("qualification") else ""
        body = f"""
<p>I, <strong>{doctor}</strong>{qual}, hereby certify that I examined
<strong>{c.get("patient_name", "")}</strong> on <strong>{c.get("date_of_visit", "")}</strong>.</p>
{f'<p><strong>Diagnosis:</strong> {c["diagnosis"]}</p>' if c.get("diagnosis") else ""}
{f'<p><strong>Duration:</strong> {c["duration"]}</p>' if c.get("duration") else ""}
{f'<p><strong>Notes:</strong> {c["notes"]}</p>' if c.get("notes") else ""}
<p>Signed,</p>
<p><strong>{doctor}</strong></p>
{f'<p>HPCSA: {c["hpcsa_number"]}</p>' if c.get("hpcsa_number") else ""}
<p>{practice}</p>
"""
    elif doc_type == "referral_letter":
        addressee = (
            f'Dr {c["referred_to_doctor"]}' if c.get("referred_to_doctor")
            else f'{c.get("referred_to_specialty", "")} Specialist'
        )
        body = f"""
<p>Dear {addressee},</p>
<p>I am referring <strong>{c.get("patient_name", "")}</strong> to you for specialist assessment.</p>
{f'<p><strong>Reason for referral:</strong> {c["reason_for_referral"]}</p>' if c.get("reason_for_referral") else ""}
{f'<p><strong>Relevant history:</strong> {c["relevant_history"]}</p>' if c.get("relevant_history") else ""}
{f'<p><strong>Current medications:</strong> {c["current_medications"]}</p>' if c.get("current_medications") else ""}
{f'<p><strong>Allergies:</strong> {c["allergies"]}</p>' if c.get("allergies") else ""}
<p><strong>Urgency:</strong> {c.get("urgency", "Routine")}</p>
<p>Kind regards,</p>
<p><strong>{c.get("referring_doctor", doctor)}</strong></p>
<p>{practice}</p>
"""
    elif doc_type == "visit_summary":
        body = f"""
<p><strong>Patient:</strong> {c.get("patient_name", "")}</p>
<p><strong>Date:</strong> {c.get("date_of_visit", "")}</p>
<p><strong>Doctor:</strong> {doctor}</p>
{f'<p><strong>Chief complaint:</strong> {c["chief_complaint"]}</p>' if c.get("chief_complaint") else ""}
{f'<p><strong>Duration:</strong> {c["duration"]}</p>' if c.get("duration") else ""}
{f'<p><strong>Severity:</strong> {c["severity"]}/10</p>' if c.get("severity") else ""}
{f'<p><strong>Medications:</strong> {c["medications_prescribed"]}</p>' if c.get("medications_prescribed") else ""}
{f'<p><strong>Allergies:</strong> {c["allergies"]}</p>' if c.get("allergies") else ""}
{f'<p><strong>Recommendations:</strong> {c["recommendations"]}</p>' if c.get("recommendations") else ""}
{f'<p><strong>Follow-up:</strong> {c["follow_up"]}</p>' if c.get("follow_up") else ""}
{f'<p><strong>Notes:</strong> {c["notes"]}</p>' if c.get("notes") else ""}
<p><strong>{doctor}</strong></p>
<p>{practice}</p>
"""
    else:
        body = f"<p>Document type: {doc_type}</p>"

    return f"""
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>{title}</title>
<style>
  @page {{ size: A4; margin: 25mm; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; font-size: 11pt; line-height: 1.7; color: #111; }}
  .letterhead {{ border-bottom: 2px solid #0F766E; padding-bottom: 12px; margin-bottom: 24px; }}
  .practice {{ font-size: 18pt; font-weight: 700; color: #0F766E; }}
  .doctor {{ font-size: 11pt; color: #555; }}
  .meta {{ font-size: 10pt; color: #555; }}
  .title {{ font-size: 14pt; font-weight: 700; text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 16px; }}
  .date {{ font-size: 10pt; color: #666; margin-bottom: 20px; }}
  p {{ margin: 8px 0; }}
  strong {{ font-weight: 600; }}
</style>
</head>
<body>
{header}
{body}
</body>
</html>
"""


def render_builtin_pdf(doc_type: str, content: dict, doctor_name: str, practice_name: str) -> Tuple[bytes, str, str]:
    """
    Render a built-in doc as PDF. Falls back to HTML if WeasyPrint is unavailable.
    Returns (file_bytes, mime_type, file_extension).
    """
    html = _builtin_html(doc_type, content, doctor_name, practice_name)
    pdf = _html_to_pdf_bytes(html)
    if pdf is not None:
        return pdf, PDF_MIME, "pdf"
    logger.info(f"Falling back to HTML for built-in doc ({doc_type})")
    return html.encode("utf-8"), HTML_MIME, "html"
