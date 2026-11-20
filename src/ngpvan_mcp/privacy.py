"""Keeping personal data away from the model.

Two layers, both in code, neither relying on the model's good behaviour:

1. Tools build their output from an allowlist of fields. A person's phone
   numbers, emails and street addresses are read only to answer yes/no
   questions ("is there a cell on file?"), never copied out.
2. `scrub()` runs over every tool result before it leaves the server. It
   drops any key that names personal data wherever it appears, and masks
   anything in free text that looks like an email, a phone number, a street
   address or a date of birth. It is the safety net for layer 1 and for
   free-text fields such as event descriptions and VAN error messages.
"""

from __future__ import annotations

import re
from typing import Any, Literal

NamePolicy = Literal["full", "initial", "none"]

# Keys that never leave the server, matched case-insensitively anywhere in
# a result. Covers the VAN person model and the API key profile.
PII_KEYS = frozenset(
    k.lower()
    for k in (
        "phones",
        "phone",
        "phoneNumber",
        "phoneId",
        "ext",
        "emails",
        "email",
        "addresses",
        "address",
        "recordedAddresses",
        "addressLine1",
        "addressLine2",
        "addressLine3",
        "streetAddress",
        "unitNo",
        "zipOrPostalCode",
        "geoLocation",
        "preview",
        "dateOfBirth",
        "dob",
        "birthDate",
        "identifiers",
        "externalIds",
        "disclosureFieldValues",
        "reportedDemographics",
        "biography",
        "suppressions",
        "envelopeName",
        "formalEnvelopeName",
        "salutation",
        "formalSalutation",
        "username",
        "userFirstName",
        "userLastName",
        "userId",
        "keyReference",
        "apiKey",
        "password",
        "authorization",
    )
)

_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
# US-style numbers: 10 digits with optional separators and a leading 1.
_PHONE = re.compile(r"(?<!\d)(?:\+?1[\s.\-]?)?\(?\d{3}\)?[\s.\-]?\d{3}[\s.\-]?\d{4}(?!\d)")
_STREET = re.compile(
    r"\b\d{1,6}\s+(?:[NSEW]\.?\s+)?(?:[A-Z][a-z]+\s+){1,3}"
    r"(?:St|Street|Ave|Avenue|Rd|Road|Blvd|Boulevard|Dr|Drive|Ln|Lane|Ct|Court|Way|Pl|Place|Ter|Terrace|Pkwy|Parkway)\b\.?"
)
_DOB = re.compile(r"\b(?:DOB|born|birth(?:day|date)?)\W{0,3}\s*\d{1,4}[/\-]\d{1,2}[/\-]\d{1,4}\b", re.IGNORECASE)


def mask_text(text: str) -> str:
    """Mask emails, phone numbers, street addresses and birth dates in free text."""
    text = _EMAIL.sub("[email removed]", text)
    text = _DOB.sub("[birth date removed]", text)
    text = _PHONE.sub("[phone removed]", text)
    text = _STREET.sub("[address removed]", text)
    return text


def scrub(value: Any) -> Any:
    """Return a copy of `value` with personal-data keys dropped and text masked."""
    if isinstance(value, dict):
        return {k: scrub(v) for k, v in value.items() if str(k).lower() not in PII_KEYS}
    if isinstance(value, (list, tuple)):
        return [scrub(v) for v in value]
    if isinstance(value, str):
        return mask_text(value)
    return value


def display_name(first: str | None, last: str | None, policy: NamePolicy) -> str | None:
    """A person's name as the configured policy allows.

    `initial` (the default) gives "Maria G.", enough for an organizer to
    recognise a volunteer on a roster without handing the model a full name.
    """
    first = (first or "").strip()
    last = (last or "").strip()
    if policy == "none":
        return None
    if policy == "full":
        return " ".join(p for p in (first, last) if p) or None
    if last:
        return f"{first} {last[0]}." if first else f"{last[0]}."
    return first or None


# Argument names whose values are personal and get redacted in the audit log.
SENSITIVE_ARGS = frozenset({"first_name", "last_name", "name", "city", "zip_code", "notes"})


def mask_args(args: dict[str, Any]) -> dict[str, Any]:
    """Tool arguments as they may be written to the audit log."""
    out: dict[str, Any] = {}
    for key, value in args.items():
        if key.lower() in PII_KEYS or key.lower() in SENSITIVE_ARGS:
            out[key] = _redacted(value)
        else:
            out[key] = scrub(value)
    return out


def _redacted(value: Any) -> str:
    if value is None:
        return "[none]"
    text = str(value)
    return f"[redacted, {len(text)} chars]"
