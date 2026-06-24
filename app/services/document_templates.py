"""
Custom .docx template handling - placeholder extraction, validation, and
filling. All the python-docx edge cases live here so the routes stay simple.

Hardening covered:
  - Smart-quote normalisation (Word autocorrect breaks `{{` and `}}` silently)
  - Run-joining before regex/replace (placeholders edited mid-string in Word
    get split across multiple <w:r> XML runs - extraction sees the placeholder
    on para.text but per-run substitution misses it)
  - Header and footer scanning (doctors put dates/addresses there)
  - Validation - corrupt files, missing placeholders, etc surface as useful
    errors instead of 500s

If you change the placeholder regex here, also update placeholderWillAutofill
in phila-web's DocumentsPage.tsx so the UI indicators stay in sync.
Phase 4-ish task: collapse both into a single server-owned heuristic.
"""
import re
import logging
from typing import Iterable
from docx import Document as DocxDocument
from docx.text.paragraph import Paragraph

logger = logging.getLogger(__name__)


# Curly variants Word autocorrect produces, mapped to ASCII straight quotes.
# We only normalise within text we're about to scan for placeholders - the rest
# of the document is left alone so legitimate smart quotes in prose survive.
SMART_QUOTE_MAP = {
    "\u2018": "'",   # left single quote
    "\u2019": "'",   # right single quote
    "\u201C": '"',   # left double quote
    "\u201D": '"',   # right double quote
    "\u201A": "'",   # single low-9 quote
    "\u201E": '"',   # double low-9 quote
    # Curly braces - the actual blockers for placeholders
    "\uFF5B": "{",   # fullwidth left brace
    "\uFF5D": "}",   # fullwidth right brace
}

PLACEHOLDER_RE = re.compile(r"\{\{(\w+)\}\}")


class TemplateError(Exception):
    """Raised when a .docx can't be processed - corrupt file, unreadable, etc."""
    pass


def _normalise(text: str) -> str:
    """Replace curly Unicode variants with ASCII so placeholder regex matches."""
    if not text:
        return text
    for bad, good in SMART_QUOTE_MAP.items():
        text = text.replace(bad, good)
    return text


def _iter_paragraphs(doc) -> Iterable[Paragraph]:
    """
    Yield every paragraph in the document - body, tables, headers, footers.
    Doctors put practice address and date in headers; placeholders there need
    to be both detected and substituted.
    """
    # Body
    for p in doc.paragraphs:
        yield p

    # Tables in body
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                for p in cell.paragraphs:
                    yield p

    # Sections - headers and footers
    for section in doc.sections:
        for container in (section.header, section.footer,
                          section.first_page_header, section.first_page_footer,
                          section.even_page_header, section.even_page_footer):
            if container is None:
                continue
            for p in container.paragraphs:
                yield p
            for table in container.tables:
                for row in table.rows:
                    for cell in row.cells:
                        for p in cell.paragraphs:
                            yield p


def extract_placeholders(file_path: str) -> list[str]:
    """
    Scan a .docx and return every unique {{placeholder}} key it contains.

    Handles smart quotes, scans body + tables + headers + footers. Preserves
    insertion order so the UI form matches the document order.

    Raises TemplateError if the file can't be opened.
    """
    try:
        doc = DocxDocument(file_path)
    except Exception as e:
        raise TemplateError(f"Could not open .docx file (is it valid?): {e}") from e

    seen: dict[str, None] = {}
    for p in _iter_paragraphs(doc):
        text = _normalise(p.text)
        for match in PLACEHOLDER_RE.findall(text):
            seen.setdefault(match, None)

    return list(seen.keys())


def _replace_in_paragraph(para: Paragraph, values: dict) -> None:
    """
    Replace {{placeholders}} in a paragraph WITHOUT losing formatting.

    The python-docx run-splitting problem: Word stores text as one or more
    <w:r> runs per paragraph. A placeholder typed and later edited often gets
    split across runs. Per-run replacement misses these because each run holds
    only a fragment.

    The fix: rebuild the paragraph by joining all run text, doing the replace
    on the joined string, then writing the result back to the first run and
    emptying the rest. This loses the formatting variation between sub-runs
    (e.g. if `{{patient_name}}` had one half bold and one half italic, the
    output uses the first run's formatting throughout). That trade-off is
    fine - mixed formatting inside a single placeholder is rare and looks
    wrong anyway.
    """
    if not para.runs:
        return

    full_text = "".join(run.text for run in para.runs)
    full_text = _normalise(full_text)

    if "{{" not in full_text:
        return  # nothing to do

    for key, val in values.items():
        full_text = full_text.replace(f"{{{{{key}}}}}", str(val) if val is not None else "")

    # Write the result back into the first run; blank out the rest.
    para.runs[0].text = full_text
    for run in para.runs[1:]:
        run.text = ""


def fill_placeholders(template_path: str, values: dict, output_path: str) -> None:
    """
    Open template_path, substitute {{placeholders}} with values, save to
    output_path. Handles run-splitting, headers, footers, tables.

    Raises TemplateError on failure.
    """
    try:
        doc = DocxDocument(template_path)
    except Exception as e:
        raise TemplateError(f"Could not open template: {e}") from e

    try:
        for p in _iter_paragraphs(doc):
            _replace_in_paragraph(p, values)
        doc.save(output_path)
    except Exception as e:
        raise TemplateError(f"Could not fill template: {e}") from e


# ── Sample data for the preview endpoint ──────────────────────────────────────

# Phase 1's placeholderWillAutofill heuristic, mirrored. Used for both the
# preview endpoint (so the doctor sees realistic-looking sample data in their
# template before using it on a real patient) and Phase 4's future single
# source of truth.
def sample_value_for(key: str) -> str:
    """Realistic-looking sample value to use when previewing a template."""
    k = key.lower()
    if "patient" in k and "name" in k:    return "Sipho Mthembu"
    if "doctor" in k:                      return "Dr Jane Mthembu"
    if "practice" in k:                    return "Mthembu Family Practice"
    if "date" in k and "visit" in k:       return "14 March 2026"
    if "date" in k:                        return "15 March 2026"
    if "diagnosis" in k or "concern" in k: return "Upper respiratory tract infection"
    if "medication" in k:                  return "Paracetamol 500mg, Amoxicillin 500mg"
    if "allerg" in k:                      return "Penicillin"
    if "note" in k or "additional" in k:   return "Patient to rest and increase fluid intake."
    if "duration" in k:                    return "3 days"
    if "severity" in k:                    return "5/10"
    if "days_off" in k or "days off" in k: return "3"
    if "hpcsa" in k:                       return "MP 123456"
    if "qualification" in k:               return "MBChB (UCT)"
    if "urgency" in k:                     return "Routine"
    # Manual-entry placeholders - keep the placeholder text so the doctor can
    # see at a glance which fields they'd need to fill in for real.
    return f"[{key}]"


def build_sample_values(placeholders: list[str]) -> dict:
    """Return a dict mapping every placeholder to a realistic sample value."""
    return {p: sample_value_for(p) for p in placeholders}
