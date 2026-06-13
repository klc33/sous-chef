"""Deterministic secret + PII redaction shared by logging AND tracing.

This is the single choke point both the log pipeline (app/core/logging.py) and the trace span
processor (app/infra/tracing.py) call before anything leaves the process (FR-007). Two layers, applied
in order by `redact`:

  1. **Deterministic masking (always on).** Known secret-ish mapping keys, provider/token patterns, and
     the two highest-value free-text PII shapes — email and phone — are masked by regex. These need no
     model, so the "no secret/PII leaks" gate holds even where the NLP model is unavailable.
  2. **Presidio PII detection (best-effort).** A Presidio analyzer backed by the small spaCy model
     (`en_core_web_sm`, kept tiny to honour the lean-image rule) adds entity-level coverage for
     high-precision PII — credit-card, SSN, IBAN, IP, and email/phone again — and anonymizes each hit
     to the same mask. NER name/place entities are deliberately NOT acted on (they'd mask real recipe
     titles in the reply — see `_PII_ENTITIES`). If Presidio or its model can't load, redaction
     **degrades gracefully** to layer 1 (logged once) rather than failing a request or a log line.

`redact` / `redact_mapping` / `MASK` keep their original signatures, so the rail
(app/guardrails/output_rails.py) and the log/trace call sites are unchanged.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:  # import only for typing — Presidio is loaded lazily at first use
    from presidio_analyzer import AnalyzerEngine
    from presidio_anonymizer import AnonymizerEngine

log = structlog.get_logger()

MASK = "[REDACTED]"

# Mapping keys whose VALUES are always masked, regardless of content.
_SECRET_KEY_HINTS = (
    "token",
    "secret",
    "password",
    "passwd",
    "api_key",
    "apikey",
    "authorization",
    "auth",
    "key",
    "credential",
)

# Patterns of obvious secrets embedded in free text.
_TOKEN_PATTERNS = [
    # provider-style API keys, incl. hyphenated multi-segment forms (sk-proj-…, gsk-live-…)
    re.compile(r"\b(?:sk|pk|rk|gsk)-[A-Za-z0-9_-]{8,}\b"),
    re.compile(r"\bBearer\s+[A-Za-z0-9._\-]+", re.IGNORECASE),  # bearer tokens
    re.compile(r"\bhvs\.[A-Za-z0-9._\-]+"),  # Vault service tokens
    # key=value / key: value where the key name itself looks secret
    re.compile(
        r"\b[\w\-]*(?:secret|token|password|passwd|api[_-]?key|credential)[\w\-]*\s*[=:]\s*\S+",
        re.IGNORECASE,
    ),
]

# Deterministic PII patterns — masked WITHOUT needing the NLP model, so email/phone (the leak-gate
# bar) can never survive even when Presidio is unavailable. Presidio re-covers these plus more entities.
_PII_PATTERNS = [
    # RFC-ish email address.
    re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"),
    # Phone number (NANP-ish): optional +country, optional (area), then 3-3-4 groups with - . or space.
    re.compile(r"(?<!\w)(?:\+?\d{1,3}[-.\s]?)?(?:\(?\d{3}\)?[-.\s]?)\d{3}[-.\s]?\d{4}(?!\w)"),
    # International form: a leading + country code followed by 7+ further digits in any grouping
    # (covers "+44 20 7946 0958" and similar that the fixed-group pattern above misses).
    re.compile(r"(?<!\w)\+\d[\d\s().\-]{6,}\d(?!\w)"),
]

# Presidio entities we act on — deliberately the high-precision, pattern/checksum-based ones only.
# PERSON and LOCATION (spaCy NER) are intentionally EXCLUDED: this same `redact` runs over the
# cook-facing reply, and NER tags ordinary proper-noun-cased recipe titles as names/places ("Veg Stew",
# "Boston Cream Pie") — masking them would corrupt grounded recipe output (golden rule #2) and change
# the wall/grounding behaviour the eval gates pin (FR-020). The entities below never fire on dish names,
# so they protect genuine PII (contact + financial + network identifiers) without over-masking content.
_PII_ENTITIES = (
    "EMAIL_ADDRESS",
    "PHONE_NUMBER",
    "CREDIT_CARD",
    "US_SSN",
    "IBAN_CODE",
    "IP_ADDRESS",
)
_PII_SCORE_THRESHOLD = 0.6

# Lazily-built Presidio engines. `_pii_init_done` flips True BEFORE any logging inside the builder, so a
# build-failure log routed back through redact() can't re-enter the builder and recurse.
_pii_engines: tuple[AnalyzerEngine, AnonymizerEngine] | None = None
_pii_init_done = False


def _is_secret_key(key: str) -> bool:
    """Return True when a mapping key NAME suggests its value is a secret."""
    lowered = key.lower()
    return any(hint in lowered for hint in _SECRET_KEY_HINTS)


def _get_pii_engines() -> tuple[AnalyzerEngine, AnonymizerEngine] | None:
    """Build (once) and return the Presidio analyzer + anonymizer, or None if they can't be loaded.

    Pins the small spaCy model so the backend image stays lean. The `_pii_init_done` guard is set
    before the failure log fires, making the lazy build safe to call from inside the log pipeline (which
    itself calls redact): a re-entrant call sees the guard and returns the cached result instead of
    recursing. Any failure (missing model, import error) degrades to None — deterministic masking still
    runs — and is logged exactly once.
    """
    global _pii_engines, _pii_init_done
    if _pii_init_done:
        return _pii_engines
    _pii_init_done = True  # set first: re-entrant calls during init return the (None) result, no recursion
    try:
        from presidio_analyzer import AnalyzerEngine
        from presidio_analyzer.nlp_engine import NlpEngineProvider
        from presidio_anonymizer import AnonymizerEngine

        nlp_engine = NlpEngineProvider(
            nlp_configuration={
                "nlp_engine_name": "spacy",
                "models": [{"lang_code": "en", "model_name": "en_core_web_sm"}],
            }
        ).create_engine()
        analyzer = AnalyzerEngine(nlp_engine=nlp_engine, supported_languages=["en"])
        _pii_engines = (analyzer, AnonymizerEngine())
    except Exception as exc:  # noqa: BLE001 — any load failure must degrade, never break a request/log
        _pii_engines = None
        log.warning("redaction.presidio_unavailable", error=str(exc))
    return _pii_engines


def _presidio_redact(text: str) -> str:
    """Mask Presidio-detected PII entities in `text`, returning it unchanged if Presidio is unavailable.

    Runs the analyzer over the curated entity set with a score threshold (suppressing weak NER guesses),
    then anonymizes every hit to the shared mask. Best-effort: an analyzer/anonymizer error degrades to
    the input text — the deterministic layer in `redact` has already masked secrets, email, and phone.
    """
    engines = _get_pii_engines()
    if engines is None:
        return text
    analyzer, anonymizer = engines
    try:
        from presidio_anonymizer.entities import OperatorConfig

        results = analyzer.analyze(
            text=text,
            entities=list(_PII_ENTITIES),
            language="en",
            score_threshold=_PII_SCORE_THRESHOLD,
        )
        if not results:
            return text
        return anonymizer.anonymize(
            text=text,
            # Presidio ships two duck-compatible RecognizerResult classes (analyzer vs. anonymizer
            # package); analyze() returns the former, anonymize() is typed for the latter. They are
            # interchangeable at runtime, so the cross-package arg-type mismatch is silenced here.
            analyzer_results=results,  # type: ignore[arg-type]
            operators={"DEFAULT": OperatorConfig("replace", {"new_value": MASK})},
        ).text
    except Exception:  # noqa: BLE001 — never let PII analysis break the caller
        return text


def redact(text: str) -> str:
    """Return text with secrets, tokens, and PII replaced by the mask.

    Deterministic first: token/secret patterns then email/phone (model-free, so the leak gate holds
    regardless of Presidio). Then the Presidio layer masks high-precision entity-level PII (card, SSN,
    IBAN, IP) when its model is available. Non-str inputs are returned unchanged — callers holding
    structured data should use redact_mapping instead.
    """
    if not isinstance(text, str):
        return text
    out = text
    for pattern in (*_TOKEN_PATTERNS, *_PII_PATTERNS):
        out = pattern.sub(MASK, out)
    return _presidio_redact(out)


def redact_mapping(mapping: Mapping[str, Any]) -> dict[str, Any]:
    """Return a redacted copy of a mapping.

    A value is masked outright when its KEY looks secret; otherwise string values pass through
    redact() to catch tokens/PII embedded in free text, and nested mappings are redacted
    recursively so a secret cannot hide one level down.
    """
    redacted: dict[str, Any] = {}
    for key, value in mapping.items():
        if isinstance(value, Mapping):
            redacted[key] = redact_mapping(value)
        elif _is_secret_key(str(key)):
            redacted[key] = MASK
        elif isinstance(value, str):
            redacted[key] = redact(value)
        else:
            redacted[key] = value
    return redacted
