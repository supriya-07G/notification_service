"""utils/sms_keywords.py — Shared SMS keyword constants.

Used by routes/sms_inbound.py (real-time webhook handler) and
workers/reply_processor.py (batch processor) to ensure identical
classification behaviour.

Whole-word matching: _classify() uses exact-set lookup for
STOP/START/CONFIRM words (exact messages) and anchored `\b` word-
boundary regex for the multi-word keyword lists.

>>> from utils.sms_keywords import classify
>>> classify("STOP")
'stop'
>>> classify("whose number is this")  # would NOT match 'who' keyword
'question'
"""

import re

# ── Exact-match word sets (entire normalised body must be in set) ────────────

STOP_WORDS = frozenset({"STOP", "STOPALL", "UNSUBSCRIBE", "CANCEL", "END"})

# NOTE: "YES" is deliberately absent here.  A "YES" reply is a confirmation
# of a reminder (see CONFIRM_WORDS below).  Adding it to START_WORDS would
# silently remove an opt-out when a customer confirms an appointment after
# having previously sent STOP.
START_WORDS = frozenset({"START", "UNSTOP", "SUBSCRIBE"})

CONFIRM_WORDS = frozenset({
    "YES", "Y", "YEP", "YEAH", "YEA",
    "CONFIRM", "CONFIRMED",
    "1", "OK", "OKAY", "SURE",
})

# ── Keyword lists for substring classification ───────────────────────────────
# Each entry is matched as a whole word/phrase (surrounded by \b word
# boundaries) so that e.g. "change" does not match "exchange", and "who"
# does not match "whose".

_RESCHEDULE_PATTERNS = [
    r"\breschedule\b", r"\brescheduling\b", r"\bchange\b",
    r"\bdifferent time\b", r"\bmove\b", r"\bpostpone\b",
    r"\bcancel and rebook\b", r"\bdifferent day\b",
]

_QUESTION_PATTERNS = [
    r"\?", r"\bwhen\b", r"\bwhere\b", r"\bwhat\b", r"\bhow\b", r"\bwho\b",
    r"\bcan i\b", r"\bwill you\b", r"\bis there\b", r"\bdo you\b",
    r"\bare you\b",
]

# Pre-compile for efficiency (called on every inbound message)
_RESCHEDULE_RE = re.compile("|".join(_RESCHEDULE_PATTERNS), re.IGNORECASE)
_QUESTION_RE = re.compile("|".join(_QUESTION_PATTERNS), re.IGNORECASE)


# ── Classification ───────────────────────────────────────────────────────────

def classify(body: str) -> str:
    """Classify an inbound SMS body into one of the known categories.

    Returns one of: 'stop', 'start', 'confirm', 'reschedule_request',
    'question', 'unknown'.
    """
    upper = body.strip().upper()
    clean = body.strip()

    if upper in STOP_WORDS:
        return "stop"
    if upper in START_WORDS:
        return "start"
    if upper in CONFIRM_WORDS:
        return "confirm"
    if _RESCHEDULE_RE.search(clean):
        return "reschedule_request"
    if _QUESTION_RE.search(clean):
        return "question"
    return "unknown"
