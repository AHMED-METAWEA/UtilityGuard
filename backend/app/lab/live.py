"""Localization of a live incident from the running twin's readings and its expectation."""

from __future__ import annotations

import numpy as np

from app.lab.localize import Localizer
from app.schemas.lab import Localization

LATEST = 5  # ticks averaged


def live_localization(rt, loc: Localizer, incident_id: str) -> Localization:
    z = np.full(len(loc.bench.sensors), np.nan)
    for i, sid in enumerate(loc.bench.sensors):
        h, e = rt.history.get(sid, []), rt.expected_history.get(sid, [])
        pairs = [(a, b) for a, b in zip(h[-LATEST:], e[-LATEST:]) if a == a and b is not None]
        if pairs:
            z[i] = np.mean([a - b for a, b in pairs]) / loc.scale.sigma[i]
    out = loc.localize(z)
    out.incident_id = incident_id
    return out
