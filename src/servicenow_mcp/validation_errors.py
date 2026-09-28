"""Privacy-safe translation of Pydantic validation failures."""

from pydantic import ValidationError


def safe_validation_error(exc: ValidationError) -> ValueError:
    """Return a ValueError naming only the model and failure count.

    Pydantic messages embed input values and locations, so they never leave
    this module; callers log, capture and return the translated error instead.
    """
    count = exc.error_count()
    noun = "error" if count == 1 else "errors"
    return ValueError(f"Invalid {exc.title} data ({count} validation {noun}).")
