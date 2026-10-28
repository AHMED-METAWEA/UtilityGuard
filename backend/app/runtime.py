"""Live twin runtime: owns the engine, ticks every 2 s, broadcasts over WebSocket.

Other layers (detection, agents) plug in through `tick_hooks`, each called with the
runtime after every tick and free to `await rt.broadcast(...)`.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Awaitable, Callable

import numpy as np
from fastapi import WebSocket

from app.schemas import (
    AgentFinding, Alert, AssetStatus, Incident, Model, Reading, ReadingBatch, ScenarioState, Snapshot, envelope,
)
from app.sim.engine import TICK_SECONDS, Engine, Event

log = logging.getLogger("utilityguard.runtime")

TickHook = Callable[["Runtime"], Awaitable[None]]


class Runtime:
    def __init__(self, seed: int | None = None):
        self.seed = int(seed if seed is not None else os.getenv("SIM_SEED", "42"))
        self.engine = Engine(self.seed)
        self.clients: set[WebSocket] = set()
        self.tick_hooks: list[TickHook] = []
        self.reset_hooks: list[Callable[[], None]] = []
        self.llm_mode: str = "live"
        self._task: asyncio.Task | None = None
        self._lock = asyncio.Lock()
        self._base_health = {a.id: float(h) for a, h in zip(
            self.engine.g.topology.assets,
            np.random.default_rng(self.seed + 7).uniform(84, 98, len(self.engine.g.topology.assets)))}
        self.reset_state()

    def reset_state(self) -> None:
        self.engine.reset()
        self.latest: dict[str, Reading] = {}
        self.history: dict[str, list[float]] = {}
        self.asset_status: dict[str, AssetStatus] = {}
        self.alerts: dict[str, Alert] = {}
        self.incidents: dict[str, Incident] = {}
        self.findings: dict[str, AgentFinding] = {}
        self.runs: dict[str, ScenarioState] = {}
        self.forecasts: dict[str, float] = {}
        self.now = datetime.now(timezone.utc)
        for hook in self.reset_hooks:
            hook()

    # ---------- websocket ----------
    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self.clients.add(ws)
        await ws.send_json(envelope("snapshot", self.snapshot()))

    def disconnect(self, ws: WebSocket) -> None:
        self.clients.discard(ws)

    async def broadcast(self, type_: str, payload: Model) -> None:
        msg = envelope(type_, payload)
        dead = []
        for ws in list(self.clients):
            try:
                await ws.send_json(msg)
            except Exception:  # noqa: BLE001 - a dropped client must not stop the loop
                dead.append(ws)
        for ws in dead:
            self.clients.discard(ws)

    def snapshot(self) -> Snapshot:
        return Snapshot(
            tick=self.engine.tick, timestamp=self.now, topology=self.engine.g.topology,
            latest=list(self.latest.values()), assets=list(self.asset_status.values()),
            alerts=list(self.alerts.values()), incidents=list(self.incidents.values()),
            findings=list(self.findings.values()), scenarios=list(self.runs.values()), llm=self.llm_mode)

    # ---------- loop ----------
    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            self._task = None

    async def _loop(self) -> None:
        while True:
            started = asyncio.get_running_loop().time()
            try:
                await self.tick_once()
            except Exception:  # noqa: BLE001
                log.exception("tick failed")
            elapsed = asyncio.get_running_loop().time() - started
            await asyncio.sleep(max(0.05, TICK_SECONDS - elapsed))

    async def tick_once(self, ts: datetime | None = None) -> None:
        async with self._lock:
            ts, truth, values = self.engine.step(ts)
            self.now = ts
            readings = []
            for s in self.engine.g.topology.sensors:
                v = values[s.id]
                r = Reading(sensor_id=s.id, timestamp=ts, value=None if v is None else round(float(v), 3),
                            quality="ok" if v is not None else "missing", forecast=self.forecasts.get(s.id))
                readings.append(r)
                self.latest[s.id] = r
                h = self.history.setdefault(s.id, [])
                h.append(float(v) if v is not None else float("nan"))
                if len(h) > 900:
                    del h[:-900]
            await self.broadcast("reading", ReadingBatch(tick=self.engine.tick, timestamp=ts, readings=readings))
            await self._update_assets()
            for hook in self.tick_hooks:
                try:
                    await hook(self)
                except Exception:  # noqa: BLE001
                    log.exception("tick hook %s failed", getattr(hook, "__name__", hook))

    async def _update_assets(self) -> None:
        g = self.engine.g
        by_asset: dict[str, list] = {}
        for s in g.topology.sensors:
            by_asset.setdefault(s.asset_id, []).append(s)
        for a in g.topology.assets:
            worst = 0.0
            for s in by_asset.get(a.id, []):
                r = self.latest[s.id]
                if r.value is None:
                    continue
                lo, hi = s.normal_range.min, s.normal_range.max
                span = max(hi - lo, 1e-6)
                dev = max(lo - r.value, r.value - hi, 0) / span
                worst = max(worst, dev)
            offline = (a.kind == "substation" and not self.engine.sub_on.get(a.id, True)) or \
                      (a.kind == "pump" and not self.engine.pump_on.get(a.id, True))
            if offline:
                status = "offline"
            elif worst > 0.5:
                status = "fault"
            elif worst > 0.05:
                status = "warning"
            else:
                status = "normal"
            health = max(0.0, self._base_health[a.id] - min(worst, 1.5) * 50 - (40 if offline else 0))
            prev = self.asset_status.get(a.id)
            fp = prev.failure_probability if prev else None
            new = AssetStatus(asset_id=a.id, status=status, health_score=round(health, 1), failure_probability=fp)
            if prev is None or prev.status != new.status or abs(prev.health_score - new.health_score) >= 2:
                self.asset_status[a.id] = new
                if prev is not None:
                    await self.broadcast("asset_status", new)

    # ---------- scenarios ----------
    def add_event(self, ev: Event) -> None:
        self.engine.add_event(ev)


runtime = Runtime()
