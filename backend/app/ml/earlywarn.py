"""Early warning: an asset whose condition signals drift away from its peers is flagged while it is still delivering.

How it works, in four steps, all in code:

1. The failure model scores each pump and transformer from age, running hours, deviation from the twin, signal variability, load and
   temperature. That score is read against the model's synthetic history; it is not the chance of failing within a stated time.
2. The same score is computed again with the two condition signals (deviation, variability) set to the median of the other assets of the
   same kind. The difference is the part of the risk that the asset's own condition explains and its age and load do not: the *excess*.
3. A warning opens when the excess, measured in log-odds so that a young pump and an old one are judged alike, stays above `EXCESS` for
   `CONSECUTIVE` samples in a row (and is at least `MIN_POINTS` of risk), while the asset is still running. It closes when the excess falls
   back below `CLEAR` for as long. A difference in probability points would never be reached by a young pump, whose risk starts near 1%.
   No warning opens for an asset whose age and load alone already give a risk of `MAX_BASE` or more (it needs its load relieved, and a small drift reads as a large relative one),
   nor for assets in a zone an open incident covers (the incident explains them).
4. The warning prices the choice with the cost model: planned maintenance now against an unplanned failure. Maintenance pays when the
   chance of failing is above `planned / unplanned`, so the warning shows that break-even next to the risk score instead of a promise.

When an asset that had a warning then stops, the warning becomes `failed` and records the lead time, the seconds between the two.
"""

from __future__ import annotations

import itertools
import json
import logging
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from functools import lru_cache
from pathlib import Path

import numpy as np

from app import ml
from app.cost import Assumptions, _summary
from app.schemas import EarlyWarning, Reason
from app.schemas.insight import RiskPoint, WarningCost, WarningEvidence, WarningSignal

log = logging.getLogger("caregrid.earlywarn")

KINDS = ("pump", "transformer")
EXCESS = 1.0  # log-odds of the risk score above the log-odds of what age and load explain (clean runs peak near 0.7, a wearing pump passes 1.0 minutes before it stops)
CLEAR = 0.5
MIN_POINTS = 0.01  # and at least this much risk in absolute terms, so a tiny difference is never called a warning
MAX_BASE = 0.6  # no new warning for an asset whose age and load alone already give this risk: it needs its load relieved, not a wear warning (a storm puts a transformer at 0.88 and any small drift reads as a large relative one)
SETTLE = 6  # samples ignored after an asset comes back (about two minutes)
CONSECUTIVE = 3  # samples in a row (about a minute at one sample every 20 s)
KEEP = 90  # samples kept per asset for the chart (about half an hour)
SIGNALS = {2: ("Deviation from the twin", "of normal range"), 3: ("Signal variability", "of normal range")}
SIGNAL_AR = {"Deviation from the twin": "الانحراف عن توقع التوأم", "Signal variability": "تذبذب الإشارة"}
EVIDENCE = Path(__file__).resolve().parents[2] / "data" / "earlywarn_evidence.json"


@lru_cache(maxsize=1)
def evidence() -> WarningEvidence | None:
    """What the test runs showed about waiting, read from the file `scripts/stress/earlywarn_matrix.py --write-evidence` writes. The sentences are written here from its figures."""
    try:
        d = json.loads(EVIDENCE.read_text())
        wear = f"{d['wear_minutes_min']:g} to {d['wear_minutes_max']:g}"
        en = (f"In {d['runs']} test runs of a pump wearing out over {wear} minutes and left alone, the warning came before the pump stopped in {d['warned']} of {d['runs']}, "
              f"a median {d['lead_median_s']:.0f} s before it (from {d['lead_min_s']:.0f} s to {d['lead_max_s']:.0f} s). At the moment of the warning the risk score ranged from "
              f"{d['risk_at_warning_min']:.0%} to {d['risk_at_warning_max']:.0%}, so the level alone does not say how close the stop is. A wear of {d['fast_minutes']:g} minutes was too fast: "
              f"warned in {d['fast_warned']} of {d['fast_runs']}. No warning was raised in {d['clean_hours']:g} simulated hours without a fault ({d['false_warnings']} false). "
              "The twin makes every wear end in a stop, so this shows that the warning comes in time, not that a real pump would fail.")
        ar = (f"في {d['runs']} تجربة لمضخة بتتآكل على مدى {d['wear_minutes_min']:g} لـ {d['wear_minutes_max']:g} دقيقة وتُركت من غير تدخل، جه الإنذار قبل توقف المضخة في {d['warned']} من {d['runs']}، "
              f"بوسيط {d['lead_median_s']:.0f} ثانية قبله (من {d['lead_min_s']:.0f} لـ {d['lead_max_s']:.0f} ثانية). وقت الإنذار كانت درجة الخطر بين "
              f"{d['risk_at_warning_min']:.0%} و{d['risk_at_warning_max']:.0%}، فالمستوى لوحده ما بيقولش التوقف قريب قد إيه. التآكل في {d['fast_minutes']:g} دقايق كان أسرع من اللازم: "
              f"اتحذّر منه في {d['fast_warned']} من {d['fast_runs']}. وما اتفتحش أي إنذار في {d['clean_hours']:g} ساعة محاكاة من غير عطل ({d['false_warnings']} كاذب). "
              "التوأم بيخلّي كل تآكل ينتهي بتوقف، فده بيثبت إن الإنذار بييجي في وقته، مش إن مضخة حقيقية هتعطل.")
        keys = WarningEvidence.model_fields
        return WarningEvidence(**{k: v for k, v in d.items() if k in keys}, text=en, text_ar=ar)
    except Exception:  # noqa: BLE001 - no measurement file, or one that does not match: the warning is shown without the evidence block
        return None


@dataclass
class _Track:
    series: deque = field(default_factory=lambda: deque(maxlen=KEEP))  # (time, risk, peer risk)
    streak: int = 0
    calm: int = 0
    warning_id: str | None = None
    hold: int = 0  # samples to ignore after the asset restarts: a pump coming back after a power cut is noisy


def logit(p: float) -> float:
    p = min(max(p, 1e-6), 1 - 1e-6)
    return float(np.log(p / (1 - p)))


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
            excess = logit(risk) - logit(peer)
            points = risk - peer
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
                tr.calm = tr.calm + 1 if excess < CLEAR or points < MIN_POINTS / 2 else 0
                if tr.calm >= CONSECUTIVE:
                    await self._publish(self._refresh(warning, tr, feats[n], peers[aid], now, state="cleared"))
                    tr.streak = tr.calm = 0
                    tr.warning_id = None
                else:
                    await self._publish(self._refresh(warning, tr, feats[n], peers[aid], now))
                continue
            tr.streak = tr.streak + 1 if (running and excess >= EXCESS and points >= MIN_POINTS and peer < MAX_BASE and aid not in explained) else 0
            if tr.streak >= CONSECUTIVE and self.A is not None:
                try:
                    w = self._open(aid, tr, feats[n], peers[aid], now)
                except Exception:  # noqa: BLE001 - one asset that cannot be priced must not stop the check of the others
                    log.exception("early warning on %s could not be opened", aid)
                    tr.streak = 0
                    continue
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
        zones: set[str] = set()
        for iid, radius in det.state.incident_roots.items():
            inc = self.rt.incidents.get(iid)
            if inc and inc.state != "resolved":
                reach |= radius
                zones |= set(inc.zone_ids)
        # whatever stands in a zone the incident covers is explained by it too: a transformer heating under an overloaded substation, a pump pushing more for a district in a storm
        reach |= {a.id for a in self.rt.engine.g.topology.assets if a.zone_id in zones}
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
        facts = _failure_facts(self.rt, asset, A)
        if facts is None and asset.kind == "transformer":  # a transformer failure takes its zone's feeder out: priced as that zone losing a share of its load, like the substation proxy
            from app.cost import IncidentFacts
            g = self.rt.engine.g
            facts = IncidentFacts(kind="substation_trip", zone_types={asset.zone_id: g.zone_index[asset.zone_id].type}, unserved_mw=A["maintenance_unserved_fraction"] * self.rt.engine.zone_demand[asset.zone_id][1],
                                  input_source="assumed", input_basis="Future failure planning proxy: the zone loses the registered share of its electricity demand. Verify against the asset contingency study.")
        n = _summary(facts, A)
        unplanned, planned, risk = round(float(n["cost_of_delay"]), 2), round(float(n["repair_now"] * A["planned_factor"]), 2), round(risk, 3)
        gain = risk * unplanned - planned  # from the rounded figures, so the numbers on screen reproduce it exactly
        return WarningCost(unplanned=unplanned, planned=planned, break_even=round(planned / unplanned, 4) if unplanned else 1.0,
                           risk=risk, verdict="maintain_now" if gain > 0 else "watch", expected_saving=round(gain, 2))

    def _rate(self, tr: _Track) -> float | None:
        """How fast the gap to the peers is widening, in probability points per minute over the last seven samples."""
        pts = list(tr.series)
        if len(pts) < 7:
            return None
        t0, r0, p0 = pts[-7]
        t1, r1, p1 = pts[-1]
        minutes = (t1 - t0).total_seconds() / 60
        return None if minutes <= 0 else round(((r1 - p1) - (r0 - p0)) / minutes, 4)

    def _drivers(self, aid: str, feat: np.ndarray, peer: np.ndarray, risk: float, peer_risk: float, signals: list[WarningSignal]) -> list[Reason]:
        """Why the warning is open, each part computed. The score is recomputed with each condition signal put back to the peer median; a signal's share is its average
        effect over the two orders in which they can be put back (a Shapley split), so the two shares add up to the whole gap, to the point shown."""
        def score(dev: bool, var: bool) -> float:
            x = feat.reshape(1, -1).copy()
            x[0, 2] = peer[0] if dev else x[0, 2]
            x[0, 3] = peer[1] if var else x[0, 3]
            return float(ml.failure_probability(self.mls.models, x)[0])

        full, no_dev, no_var, none = score(False, False), score(True, False), score(False, True), score(True, True)
        part = [((full - no_dev) + (no_var - none)) / 2, ((full - no_var) + (no_dev - none)) / 2]
        gap = round((round(risk, 3) - round(peer_risk, 3)) * 100, 1)
        pts = [round(part[0] * 100, 1), round(part[1] * 100, 1)]
        big = 0 if abs(pts[0]) >= abs(pts[1]) else 1
        pts[big] = round(gap - pts[1 - big], 1)  # the larger share takes the rounding, so the shares sum to the gap shown
        out = []
        for k in sorted(range(2), key=lambda i: -abs(part[i])):
            sg = signals[k]
            how = f"It explains {pts[k]:.1f} of the {gap:.1f} points" if abs(pts[k]) >= 0.05 else f"It explains less than 0.1 of the {gap:.1f} points"
            how_ar = f"بيفسّر {pts[k]:.1f} من {gap:.1f} نقطة" if abs(pts[k]) >= 0.05 else f"بيفسّر أقل من 0.1 من {gap:.1f} نقطة"
            out.append(Reason(
                text=f"{sg.label} is {sg.value:.2f} against {sg.peer_value:.2f} for its peers (as a share of the normal range). {how} by which the risk score exceeds what age and load alone give.",
                text_ar=f"{SIGNAL_AR[sg.label]} قيمته {sg.value:.2f} مقابل {sg.peer_value:.2f} للأصول المشابهة (كنسبة من المدى الطبيعي). {how_ar} هي زيادة درجة الخطر عن اللي العمر والحمل لوحدهم بيدّوه.",
                evidence_type="measured", evidence_ref=f"asset:{aid}"))
        hot = f" and {feat[5]:.1f} °C above the normal maximum" if feat[5] > 0 else ""
        hot_ar = f" و{feat[5]:.1f} °م فوق الحد الأقصى الطبيعي" if feat[5] > 0 else ""
        out.append(Reason(
            text=f"Age ({feat[0]:.1f} years), running hours ({feat[1]:.1f} thousand) and load ({feat[4]:.2f} of the normal range){hot} alone give a score of {peer_risk:.1%}. "
                 "The age and the hours are values the simulation assigns to the asset, not measurements.",
            text_ar=f"العمر ({feat[0]:.1f} سنة) وساعات التشغيل ({feat[1]:.1f} ألف ساعة) والحمل ({feat[4]:.2f} من المدى الطبيعي){hot_ar} لوحدهم بيدّوا درجة {peer_risk:.1%}. "
                    "العمر والساعات قيم بيحطها المحاكي للأصل، مش قياسات.",
            evidence_type="assumed", evidence_ref=f"asset:{aid}"))
        g = logit(risk) - logit(peer_risk)
        out.append(Reason(
            text=f"A warning opens when the gap stays at or above {EXCESS:.1f} in log-odds for {CONSECUTIVE} readings in a row while the asset is running. Now it is {g:.2f}.",
            text_ar=f"الإنذار بيتفتح لما الفرق يفضل {EXCESS:.1f} أو أكتر بمقياس log-odds لمدة {CONSECUTIVE} قراءات متتالية والأصل شغّال. دلوقتي {g:.2f}.",
            evidence_type="rule", evidence_ref="rule:early-warning"))
        return out

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
        out = w.model_copy(update=dict(
            state=state or w.state, updated_at=now, risk=round(risk, 3), peer_risk=round(pr, 3), excess=round(round(risk, 3) - round(pr, 3), 3), rise_per_minute=self._rate(tr),
            signals=signals, cost=cost, text=self._text(w.asset_name, risk, pr, cost, signals), drivers=self._drivers(w.asset_id, feat, peer, risk, pr, signals), evidence=evidence(),
            series=[RiskPoint(t=t, risk=round(r, 3), peer=round(p, 3)) for t, r, p in tr.series]))
        self.warnings[out.id] = out
        return out

    async def _publish(self, w: EarlyWarning) -> None:
        self.warnings[w.id] = w
        await self.rt.broadcast("early_warning", w)
