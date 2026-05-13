"""Error classification helpers shared by every router.

Endpoint handlers wrap any HyPlan call in ``try: ... except Exception:`` and
historically rethrew everything as a 500.  In practice most of what HyPlan
raises is user-actionable (no airports in range, solar zenith unfavourable
for ``glint_arc``, degenerate polygon, etc.) — callers want a 400 with a
clear ``code`` they can react to, not an opaque 500.

Routers call :func:`raise_http(operation, exc)` instead of crafting the
HTTPException directly.  Classification + logging live here.
"""

from __future__ import annotations

import logging
import traceback

from fastapi import HTTPException

from hyplan.exceptions import HyPlanError, HyPlanValueError, HyPlanTypeError

logger = logging.getLogger("hyplan-service")


def classify(exc: Exception) -> tuple[int, str]:
    """Map a Python exception to ``(http_status, error_code)``.

    Known HyPlan exceptions become 400 with a stable ``hyplan_*`` code.
    Common Python validation errors (``ValueError``, ``KeyError``) also
    become 400.  Anything else is treated as an unexpected server fault
    and logged with full traceback in :func:`raise_http`.
    """
    if isinstance(exc, HyPlanValueError):
        return 400, "hyplan_value_error"
    if isinstance(exc, HyPlanTypeError):
        return 400, "hyplan_type_error"
    if isinstance(exc, HyPlanError):
        # Other HyPlan*Error subclasses (HyPlanRuntimeError) — the
        # planning engine has decided the request can't be fulfilled.
        # Surface as 400; the caller has more recourse than a 500
        # implies (different polygon, different aircraft, etc.).
        return 400, "hyplan_error"
    if isinstance(exc, (ValueError, KeyError)):
        return 400, "bad_input"
    return 500, "internal_error"


def raise_http(operation: str, exc: Exception) -> "HTTPException":
    """Translate ``exc`` into an :class:`HTTPException` and raise.

    The response detail is a structured dict so the frontend can show a
    clean message *and* react programmatically to the code (e.g. a
    ``hyplan_value_error`` from ``/compute-plan`` warrants different UI
    treatment than ``internal_error``).  500-class errors are logged
    with a full traceback; 400-class are logged as warnings without one
    (they're user input issues, not server bugs).
    """
    status, code = classify(exc)
    if status >= 500:
        logger.error("%s failed (%s): %s", operation, code, traceback.format_exc())
    else:
        logger.warning("%s rejected (%s): %s", operation, code, exc)
    raise HTTPException(
        status_code=status,
        detail={"message": str(exc), "code": code, "operation": operation},
    )
