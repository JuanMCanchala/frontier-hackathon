"""Revocable permission to send input to the desktop."""

from aprendiz.vnc.errors import VncError


class Lease:
    """Created per task by the supervisor. Once revoked, no input event may be sent,
    even if the runner code keeps executing after cancellation."""

    def __init__(self) -> None:
        self._revoked = False

    @property
    def revoked(self) -> bool:
        return self._revoked

    def revoke(self) -> None:
        self._revoked = True

    def ensure_active(self) -> None:
        if self._revoked:
            raise VncError("lease revoked: the task no longer owns the desktop")
