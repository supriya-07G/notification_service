"""utils/log_helpers.py — Helper functions for masking PII in application logs."""

def mask_phone(p: str) -> str:
    if not p or len(p) < 4:
        return "***"
    return f"***-***-{p[-4:]}"

def mask_name(n: str) -> str:
    if not n:
        return "***"
    parts = n.split()
    if len(parts) >= 2:
        return f"{parts[0][0]}. {parts[-1][0]}."
    return parts[0][:3] + "***" if len(parts[0]) > 3 else "***"

def mask_phone_full(p: str) -> str:
    """Shows full phone number but intentionally wrapped for log context."""
    if not p:
        return "***"
    return p  # Only use in non-production, or implement full masking
