"""Exceptions raised by the core layer (window, capture, input, vision)."""

from __future__ import annotations


class NgoaLongError(Exception):
    """Base class for every error raised by the bot."""


class WindowNotFoundError(NgoaLongError):
    """No game window matched, or a bound window could not be found again."""


class WindowGoneError(NgoaLongError):
    """The bound window handle is no longer valid (closed, or the browser restarted)."""


class CaptureError(NgoaLongError):
    """A frame could not be captured (window minimised, PrintWindow failed, bad image file)."""


class TemplateError(NgoaLongError):
    """A template image is missing or unreadable."""


class Cancelled(NgoaLongError):
    """The run was stopped by the user while waiting."""


class TaskStopped(NgoaLongError):
    """The game asked for something the settings do not allow (e.g. spending Vàng); the run declined
    it and stops. Reported as "stopped" with this message, not as a failure."""


class WaitTimeout(NgoaLongError, TimeoutError):
    """A wait reached its time limit without the expected result."""
