"""Reusable, local company identity for internal document drafts.

Formatting checks do not verify a business against public registers. Documents
use the current saved profile; this is not a historical fiscal identity snapshot.
"""
import re
import unicodedata


CORE_COMPANY_FIELDS = ("name", "sector", "description", "signature")
OPTIONAL_COMPANY_FIELDS = (
    "legal_name", "vat_number", "tax_code", "address", "postal_code", "city",
    "province", "email", "phone", "pec", "sdi_code",
)
COMPANY_FIELDS = (*CORE_COMPANY_FIELDS, *OPTIONAL_COMPANY_FIELDS)
FIELD_LIMITS = {
    "name": 120, "sector": 160, "description": 2000, "signature": 600,
    "legal_name": 160, "vat_number": 13, "tax_code": 16, "address": 250,
    "postal_code": 5, "city": 120, "province": 2, "email": 254,
    "phone": 40, "pec": 254, "sdi_code": 7,
}
EMAIL_PATTERN = re.compile(
    r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@"
    r"[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?\.[A-Za-z]{2,63}"
)


def normalize_company_field(field, value):
    """Normalize text and validate only the entered field's basic structure."""
    if not isinstance(value, str):
        raise ValueError("Indica un testo per questo campo.")
    value = value.strip()
    allowed = "\n\t" if field in ("description", "signature") else ""
    if any(unicodedata.category(char) in ("Cc", "Cf") and char not in allowed for char in value):
        raise ValueError("Il testo contiene caratteri di controllo non consentiti.")
    if len(value) > FIELD_LIMITS[field]:
        raise ValueError("Il testo è troppo lungo.")
    if not value:
        return ""
    if field == "vat_number":
        value = value.upper()
        if value.startswith("IT"):
            value = value[2:]
        if not re.fullmatch(r"[0-9]{11}", value):
            raise ValueError("Indica una partita IVA di 11 cifre, con prefisso IT facoltativo.")
    elif field == "tax_code":
        value = value.upper()
        if not re.fullmatch(r"(?:[0-9]{11}|[A-Z0-9]{16})", value):
            raise ValueError("Indica un codice fiscale di 11 cifre o 16 caratteri alfanumerici.")
    elif field == "postal_code":
        if not re.fullmatch(r"[0-9]{5}", value):
            raise ValueError("Indica un CAP di 5 cifre.")
    elif field == "province":
        value = value.upper()
        if not re.fullmatch(r"[A-Z]{2}", value):
            raise ValueError("Indica la sigla della provincia di 2 lettere.")
    elif field == "sdi_code":
        value = value.upper()
        if not re.fullmatch(r"[A-Z0-9]{7}", value):
            raise ValueError("Indica un codice destinatario di 7 caratteri alfanumerici.")
    elif field in ("email", "pec"):
        if not EMAIL_PATTERN.fullmatch(value) or ".." in value:
            raise ValueError("Indica un indirizzo email valido.")
        value = value.lower()
    return value


def get_document_company(db):
    """Return the current private profile, excluding fictitious demo identity.

    Legacy profiles may omit the newly introduced fields. Invalid legacy values
    are omitted rather than inserted into a document or treated as verified data.
    """
    company = db.get_setting("company", {})
    result = dict.fromkeys(COMPANY_FIELDS, "")
    if not isinstance(company, dict) or company.get("demo"):
        return result
    for field in COMPANY_FIELDS:
        try:
            result[field] = normalize_company_field(field, company.get(field, ""))
        except ValueError:
            # Stored records from an older version should not break an export.
            pass
    return result
