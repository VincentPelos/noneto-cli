"""Only explicit application errors may be displayed verbatim."""


class AppError(RuntimeError):
    """A fixed, application-owned message safe for display."""


def error_label(error):
    response = getattr(error, "response", None)
    if response is not None:
        return f"HTTP {response.status_code}"
    return type(error).__name__
