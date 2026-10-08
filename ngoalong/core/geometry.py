"""Small immutable geometry types shared by every core module.

All coordinates are integer pixels. ``Rect`` is (x, y, w, h) with an exclusive right/bottom edge.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Point:
    x: int
    y: int

    def offset(self, dx: int, dy: int) -> Point:
        return Point(self.x + dx, self.y + dy)

    def __add__(self, other: Point) -> Point:
        return Point(self.x + other.x, self.y + other.y)

    def __sub__(self, other: Point) -> Point:
        return Point(self.x - other.x, self.y - other.y)

    def as_tuple(self) -> tuple[int, int]:
        return (self.x, self.y)


@dataclass(frozen=True, slots=True)
class Rect:
    x: int
    y: int
    w: int
    h: int

    def __post_init__(self) -> None:
        if self.w < 0 or self.h < 0:
            raise ValueError(f"Rect size must be non-negative, got {self.w}x{self.h}")

    @classmethod
    def from_ltrb(cls, left: int, top: int, right: int, bottom: int) -> Rect:
        return cls(left, top, max(0, right - left), max(0, bottom - top))

    @property
    def right(self) -> int:
        return self.x + self.w

    @property
    def bottom(self) -> int:
        return self.y + self.h

    @property
    def top_left(self) -> Point:
        return Point(self.x, self.y)

    @property
    def center(self) -> Point:
        return Point(self.x + self.w // 2, self.y + self.h // 2)

    @property
    def size(self) -> tuple[int, int]:
        return (self.w, self.h)

    @property
    def area(self) -> int:
        return self.w * self.h

    def is_empty(self) -> bool:
        return self.w == 0 or self.h == 0

    def contains(self, p: Point) -> bool:
        return self.x <= p.x < self.right and self.y <= p.y < self.bottom

    def offset(self, dx: int, dy: int) -> Rect:
        return Rect(self.x + dx, self.y + dy, self.w, self.h)

    def intersect(self, other: Rect) -> Rect:
        """Overlap of two rectangles; an empty rect (w or h == 0) when they do not overlap."""
        return Rect.from_ltrb(
            max(self.x, other.x),
            max(self.y, other.y),
            min(self.right, other.right),
            min(self.bottom, other.bottom),
        )

    def scaled(self, factor: float) -> Rect:
        return Rect(
            round(self.x * factor), round(self.y * factor), round(self.w * factor), round(self.h * factor)
        )

    def as_tuple(self) -> tuple[int, int, int, int]:
        return (self.x, self.y, self.w, self.h)
