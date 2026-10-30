"""Early warning: an asset whose condition signals drift away from its peers is flagged while it is still delivering.

How it works, in four steps, all in code:

1. The failure model scores each pump and transformer from age, running hours, deviation from the twin, signal variability, load and
   temperature. That score is a ranking, not a calibrated probability: the model is trained on a synthetic hazard.
2. The same score is computed again with the two condition signals (deviation, variability) set to the median of the other assets of the
   same kind. The difference is the part of the risk that the asset's own condition explains and its age and load do not: the *excess*.
3. A warning opens when the excess stays above `EXCESS` for `CONSECUTIVE` samples in a row, while the asset is still running. It closes
   when the excess falls back below `CLEAR` for as long.
4. The warning prices the choice with the cost model: planned maintenance now against an unplanned failure. Maintenance pays when the
   chance of failing is above `planned / unplanned`, so the warning shows that break-even next to the risk score instead of a promise.

When an asset that had a warning then stops, the warning becomes `failed` and records the lead time, the seconds between the two.
"""

from __future__ import annotations

import itertools
import logging
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime

import numpy as np

from app import ml
from app.cost import Assumptions, _summary
from app.schemas import EarlyWarning
from app.schemas.insight import RiskPoint, WarningCost, WarningSignal

log = logging.getLogger("utilityguard.earlywarn")

KINDS = ("pump", "transformer")
EXCESS = 0.15  # risk score above what age and load explain
CLEAR = 0.07
SETTLE = 6  # samples ignored after an asset comes back (about two minutes)
CONSECUTIVE = 3  # samples in a row (about a minute at one sample every 20 s)
KEEP = 90  # samples kept per asset for the chart (about half an hour)
SIGNALS = {2: ("Deviation from the twin", "of normal range"), 3: ("Signal variability", "of normal range")}


@dataclass
class _Track:
    series: deque = field(default_factory=lambda: deque(maxlen=KEEP))  # (time, risk, peer risk)
    streak: int = 0
    calm: int = 0
    warning_id: str | None = None
    hold: int = 0  # samples to ignore after the asset restarts: a pump coming back after a power cut is noisy


class EarlyWarnings:
    def __init__(self, rt, mls):
        self.rt, self.mls = rt, mls
        self.A: Assumptions | None = None  # set by the app so costs use the same assumptions as everything else
        self.warnings: dict[str, EarlyWarning] = {}
        self._track: dict[str, _Track] = {}
        self._ids = itertools.count(1)
        rt.reset_hooks.append(self.reset)

    def reset(self) -> None:
        self.warnings.clear()
        self._track.clear()

    def listing(self) -> list[EarlyWarning]:
        return sorted(self.warnings.values(), key=lambda w: w.created_at, reverse=True)

    # ---------- the check, once per risk update ----------
    async def update(self, ids: list[str], feats: np.ndarray, probs: np.ndarray) -> None:
        g = self.rt.engine.g
        kinds = {i: g.asset_index[i].kind for i in ids}
        peer_risk: dict[str, float] = {}
        peers: dict[str, np.ndarray] = {}
        for kind in KINDS:
            rows = [n for n, i in enumerate(ids) if kinds[i] == kind]
            if len(rows) < 3:
                continue
            block = feats[rows]
            counter = block.copy()
            for n, r in enumerate(rows):
                others = np.delete(block, n, axis=0)
                med = np.median(others[:, 2:4], axis=0)
                counter[n, 2:4] = med
                peers[ids[r]] = med
            for r, p in zip(rows, ml.failure_probability(self.mls.models, counter)):
                peer_risk[ids[r]] = float(p)
        now = self.rt.now
        explained = self._explained()
        for n, aid in enumerate(ids):
            if aid not in peer_risk:
                continue
            risk, peer = float(probs[n]), peer_risk[aid]
            tr = self._track.setdefault(aid, _Track())
            tr.series.append((now, risk, peer))
            status = self.rt.asset_status.get(aid)
            running = bool(status) and status.status in ("normal", "warning")
            excess = risk - peer
            if not running:
                tr.hold = SETTLE
            elif tr.hold > 0:
                tr.hold -= 1
                tr.streak = 0
                if not (tr.warning_id and self.warnings.get(tr.warning_id) and self.warnings[tr.warning_id].state == "active"):
                    continue
            warning = self.warnings.get(tr.warning_id) if tr.warning_id else None
            if warning and warning.state == "active":
                if not running:
                    await self._failed(aid, warning, now)
                    tr.streak = tr.calm = 0
                    continue
                tr.calm = tr.calm + 1 if excess < CLEAR else 0
                if tr.calm >= CONSECUTIVE:
                    await self._publish(self._refresh(warning, tr, feats[n], peers[aid], now, state="cleared"))
                    tr.streak = tr.calm = 0
                    tr.warning_id = None
                else:
                    await self._publish(self._refresh(warning, tr, feats[n], peers[aid], now))
                continue
            tr.streak = tr.streak + 1 if (running and excess >= EXCESS and aid not in explained) else 0
            if tr.streak >= CONSECUTIVE and self.A is not None:
                w = self._open(aid, tr, feats[n], peers[aid], now)
                self.warnings[w.id] = w
                tr.warning_id = w.id
                log.info("early warning %s on %s: risk %.2f, peers %.2f", w.id, aid, risk, peer)
                await self._publish(w)

    def _explained(self) -> set[str]:
        """Assets inside the reach of an open incident. A pump moving more water because a main has burst is not wearing out."""
        det = getattr(self.rt, "detector", None)
        if det is None:
            return set()
        reach: set[str] = set()
        for iid, radius in det.state.incident_roots.items():
            inc = self.rt.incidents.get(iid)
            if inc and inc.state != "resolved":
                reach |= radius
        return reach

    async def _failed(self, aid: str, w: EarlyWarning, now: datetime) -> None:
        """The asset stopped: the lead time is measured against the failure alarm when there is one, otherwise against now."""
        alarm = next((a.detected_at for a in self.rt.alerts.values() if aid in a.asset_ids and a.detected_at >= w.created_at), now)
        lead = max(0.0, (alarm - w.created_at).total_seconds())
        done = w.model_copy(update={"state": "failed", "updated_at": now, "lead_time_seconds": round(lead, 1),
                                    "text": w.text + f" It then failed {lead / 60:.1f} minutes after this warning."})
        self.warnings[w.id] = done
        await self._publish(done)

    # ---------- building the warning ----------
    def _signals(self, feat: np.ndarray, peer: np.ndarray) -> list[WarningSignal]:
        return [WarningSignal(label=SIGNALS[c][0], value=round(float(feat[c]), 3), peer_value=round(float(peer[k]), 3), unit=SIGNALS[c][1]) for k, c in enumerate((2, 3))]

    def _cost(self, aid: str, risk: float) -> WarningCost:
        from app.savings import _failure_facts  # imported late: savings imports the runtime
        asset = self.rt.engine.g.asset_index[aid]
        A = self.A
        n = _summary(_failure_facts(self.rt, asset, A), A)
        unplanned, planned, risk = round(float(n["cost_of_delay"]), 2), round(float(n["repair_now"] * A["planned_factor"]), 2), round(risk, 3)
        gain = risk * unplanned - planned  # from the rounded figures, so the numbers on screen reproduce it exactly
        return WarningCost(unplanned=unplanned, planned=planned, break_even=round(planned / unplanned, 4) if unplanned else 1.0,
                           risk=risk, verdict="maintain_now" if gain > 0 else "watch", expected_saving=round(gain, 2))

    def _rates(self, tr: _Track) -> tuple[float | None, float | None]:
        pts = list(tr.series)
        if len(pts) < 7:
            return None, None
        t0, r0, p0 = pts[-7]
        t1, r1, p1 = pts[-1]
        minutes = (t1 - t0).total_seconds() / 60
        if minutes <= 0:
            return None, None
        rate = ((r1 - p1) - (r0 - p0)) / minutes
        eta = (0.9 - r1) / (rate) if rate > 0.005 and r1 < 0.9 else (0.0 if r1 >= 0.9 else None)
        return round(rate, 4), None if eta is None else round(float(eta), 1)

    def _text(self, name: str, w_risk: float, peer: float, cost: WarningCost, signals: list[WarningSignal]) -> str:
        sig = signals[1]
        verdict = ("At this risk score maintaining now is cheaper than waiting." if cost.verdict == "maintain_now"
                   else "At this risk score waiting is still cheaper, but it is worth watching.")
        return (f"{name} is still running, but its {sig.label.lower()} is {sig.value:.2f} against {sig.peer_value:.2f} for its peers, and its risk score is "
                f"{w_risk:.0%} where age and load alone would give {peer:.0%}. Maintenance pays when the chance of failing is above {cost.break_even:.1%}: "
                f"{cost.planned:,.0f} EGP planned against {cost.unplanned:,.0f} EGP if it fails. {verdict}")

    def _open(self, aid: str, tr: _Track, feat: np.ndarray, peer: np.ndarray, now: datetime) -> EarlyWarning:
        a = self.rt.engine.g.asset_index[aid]
        wid = f"warn-{next(self._ids):04d}"
        return self._refresh(EarlyWarning(
            id=wid, asset_id=aid, asset_name=a.name, network=a.network, zone_id=a.zone_id, state="active", created_at=now, updated_at=now,
            risk=0, peer_risk=0, excess=0, signals=[], series=[], cost=self._cost(aid, 0.0), text="", why_ref=f"warning:{wid}"), tr, feat, peer, now)

    def _refresh(self, w: EarlyWarning, tr: _Track, feat: np.ndarray, peer: np.ndarray, now: datetime, state: str | None = None) -> EarlyWarning:
        _, risk, pr = tr.series[-1]
        signals = self._signals(feat, peer)
        cost = self._cost(w.asset_id, risk)
        rate, eta = self._rates(tr)
        out = w.model_copy(update=dict(
            state=state or w.state, updated_at=now, risk=round(risk, 3), peer_risk=round(pr, 3), excess=round(risk - pr, 3), rise_per_minute=rate, minutes_to_90=eta,
            signals=signals, cost=cost, text=self._text(w.asset_name, risk, pr, cost, signals),
            series=[RiskPoint(t=t, risk=round(r, 3), peer=round(p, 3)) for t, r, p in tr.series]))
        self.warnings[out.id] = out
        return out

    async def _publish(self, w: EarlyWarning) -> None:
        self.warnings[w.id] = w
        await self.rt.broadcast("early_warning", w)
