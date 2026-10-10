"""``InputDriver`` interface (docs/06 Core interfaces).

Points are CLIENT coordinates of the target window (the Coowon top-level window in the MVP);
canvas points (1000x600) are converted by the ``Actor`` with the canvas fix before they reach a driver.
"""

from __future__ import annotations

from typing import Literal, Protocol, runtime_checkable

from ngoalong.core.geometry import Point

MouseButton = Literal["left", "right", "middle"]

# Common virtual-key codes used by features.
VK_BACK = 0x08
VK_TAB = 0x09
VK_RETURN = 0x0D
VK_ESCAPE = 0x1B
VK_SPACE = 0x20
VK_END = 0x23
VK_HOME = 0x24
VK_DELETE = 0x2E


@runtime_checkable
class InputDriver(Protocol):
    def click(self, p: Point, button: MouseButton = "left", double: bool = False) -> None:
        """Mouse click at ``p`` (client coordinates). ``double`` sends a real double click."""
        ...

    def key(self, vk: int) -> None:
        """Press and release one virtual key (key down + key up)."""
        ...

    def text(self, s: str) -> None:
        """Type ``s`` as characters (WM_CHAR per character on Win32)."""
        ...
