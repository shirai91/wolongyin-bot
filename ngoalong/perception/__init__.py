"""Perception: canvas -> UI parts -> screen stack -> state values, without OCR (docs/08, phase R1).

* ``parts``     UI-part templates searched in their regions (``parts.yaml``)
* ``probes``    colour probes: selected tab, checkbox tick, beam, floating text, fill bars (``probes.yaml``)
* ``screens``   screen rules over parts and probes, layered base / dialogs / overlays (``screens/*.yaml``)
* ``readers``   typed state values per screen (tab, present, digits, find_all, rows, ...)
* ``snapshot``  one ``Snapshot`` per frame, the single answer to "where am I and what do I see"

Every module works on the **canvas** at 1000x600 (``Perceiver.canvas`` crops and rescales a frame).
"""

from ngoalong.perception.snapshot import Perceiver, Snapshot

__all__ = ["Perceiver", "Snapshot"]
