class VncError(Exception):
    """Protocol, authentication, connection or lease failure."""


class VncGarbledGreeting(VncError):
    """The server answered without a valid RFB greeting: transient on the desktop side, worth one
    retry (unlike a refused password)."""
