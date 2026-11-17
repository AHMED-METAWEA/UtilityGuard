"""The board's arithmetic: which open problems exist, how long each takes, and the cheapest order to send crews.

No language model is involved here. Every figure the agents later quote comes from this module.

    Hourly cost of a problem      C_i      = C_loss + C_disruption + C_secondary        (the cost model, EGP/h)
    Work time on site             h_i      = fix_hours_<kind>
    Completion hour               T_i      = start_i + h_i, crews free after response_hours, then after crew_switch_hours
    Who can take a job            a crew of the job's own network, or a general crew (the first one free; a network's own crew on a tie)
    Loss until fixed              C_i x T_i
    Repair                        R_i x m(w_i),  w_i = max(0, start_i - response_hours),  m = min(cap, 1 + (late - 1) w / delay)
    Cost of an order              sum_i  C_i x T_i + R_i x m(w_i)
    Return per crew hour          C_i / h_i      (the order a single crew should follow, by Smith's rule)

A problem that another repair makes moot (a substation overload when the tripped substation feeding it is restored first)
stops costing at the hour that repair finishes and needs no crew if it is still waiting then.
"""

from __future__ import annotations

import dataclasses
import itertools
import zlib
from dataclasses import dataclass, field

import numpy as np

from app.cost import Assumptions, IncidentFacts, _summary, customer_key, policy_kind
from app.sim.topology import SUBSTATION_BACKUP

MAX_TASKS = 6  # 6! = 720 orders, searched exhaustively
RUNS = 300  # price scenarios for the robustness check
INF = float("inf")
TEAMS = ("water", "electricity", "cooling")
GENERAL = -1  # a crew lane that takes a job on any network

ACTIONS = {
    "leak": ("Isolate the damaged segment with the nearest valves and replace the section", "pipe repair"),
    "pump_failure": ("Switch to the standby pump and repair the failed one", "mechanical"),
    "substation_trip": ("Inspect, re-close the breaker and re-energise the substation", "electrical"),
    "cooling_fault": ("Start the standby chiller and repair the faulty one", "HVAC"),
    "energy_waste": ("Shed or transfer the excess load", "operations"),
    "sensor_fault": ("Check and recalibrate or replace the sensor", "instrumentation"),
}


@dataclass
class Task:
    id: str  # the alert id
    incident_id: str
    agent: str  # water | electricity | cooling: the network the alert is on
    asset_id: str
    asset_name: str
    kind: str  # detector kind
    policy: str  # cost-model kind
    facts: IncidentFacts
    action: str
    crew: str
    rate: float
    repair: float
    hours: float
    detected_at: float  # seconds, for the order the alarms arrived in
    relieved_by: tuple[str, ...] = ()
    stake: dict = field(default_factory=dict)

    @property
    def smith(self) -> float:
        return self.rate / self.hours if self.hours else INF


def tasks_from_alerts(rt, detector, A: Assumptions) -> list[Task]:
    """One task per open alert, priced by the cost model from the facts the detector measured."""
    g = rt.engine.g
    tasks: list[Task] = []
    for o in detector.state.open.values():
        alert = rt.alerts.get(o.alert.id, o.alert)
        if alert.state == "resolved":
            continue
        policy = policy_kind(o.facts)
        n = _summary(o.facts, A)
        action, crew = ACTIONS[o.kind]
        stake = {"leak_m3h": o.facts.leak_m3h, "unserved_mw": o.facts.unserved_mw, "excess_mw": o.facts.excess_mw,
                 "cooling_deficit_tons": o.facts.cooling_deficit_tons, "pumps_stopped": o.facts.pumps_stopped,
                 "plants_affected": o.facts.plants_affected,
                 "customers": sum(A[customer_key(z)] for z in o.facts.zone_types) if o.facts.zone_types else 0.0}
        tasks.append(Task(id=alert.id, incident_id=alert.incident_id, agent=alert.network, asset_id=o.root, asset_name=g.asset_index[o.root].name,
                          kind=o.kind, policy=policy, facts=o.facts, action=action, crew=crew, rate=n["c_delay"], repair=n["repair_now"],
                          hours=A[f"fix_hours_{policy}"], detected_at=alert.detected_at.timestamp(), stake=stake))
    # an overload on a backup substation is relieved by restoring the tripped substation that feeds it
    trips = {t.asset_id: t.id for t in tasks if t.kind == "substation_trip"}
    for t in tasks:
        if t.kind == "energy_waste" and t.asset_id.startswith("elec-sub"):
            t.relieved_by = tuple(tid for sub, tid in trips.items() if SUBSTATION_BACKUP.get(sub) == t.asset_id)
    tasks.sort(key=lambda t: (t.detected_at, t.id))
    return tasks


@dataclass
class Params:
    """Numbers for one pricing of the same tasks: the current assumptions, or one sampled draw."""
    rate: np.ndarray
    repair: np.ndarray
    hours: np.ndarray
    late: np.ndarray
    delay: np.ndarray
    cap: float
    response: float
    switch: float
    crews: int
    relieved: np.ndarray  # relieved[i, j]: task i is relieved when task j finishes
    pool: np.ndarray | None = None  # pool[c]: the network crew c works on (index into TEAMS), or GENERAL; None means every crew is general
    team: np.ndarray | None = None  # team[i]: the network of task i (index into TEAMS)

    def team_names(self) -> list[str]:
        """The network each crew lane works on, or "any"."""
        return ["any"] * self.crews if self.pool is None else ["any" if k == GENERAL else TEAMS[k] for k in self.pool]

    def with_crew(self, team: str | None) -> "Params":
        """The same problems with one more crew: a general one (team None) or one of a network's own. Lanes stay in crew_pool's order."""
        pool = np.full(self.crews, GENERAL, dtype=np.int64) if self.pool is None else self.pool
        k = GENERAL if team is None else TEAMS.index(team)
        own = np.sort(np.append(pool[pool != GENERAL], k) if k != GENERAL else pool[pool != GENERAL])
        general = int((pool == GENERAL).sum()) + (k == GENERAL)
        team_of = self.team if self.team is not None else np.zeros(len(self.rate), dtype=np.int64)
        return dataclasses.replace(self, crews=self.crews + 1, pool=np.concatenate([own, np.full(general, GENERAL)]).astype(np.int64), team=team_of)


def crew_pool(A: Assumptions, general: int | None = None) -> np.ndarray:
    """One lane per crew: each network's own crews first (so a tie goes to them and keeps a general crew free), then the general ones."""
    own = [k for k, t in enumerate(TEAMS) for _ in range(int(round(A[f"crews_{t}"])))]
    return np.array(own + [GENERAL] * (int(round(A["crews_available"])) if general is None else general), dtype=np.int64)


def params_for(tasks: list[Task], A: Assumptions, crews: int | None = None) -> Params:
    n = len(tasks)
    pool = crew_pool(A, crews)
    team = np.array([TEAMS.index(t.agent) for t in tasks], dtype=np.int64)
    for t in tasks:
        if not ((pool == GENERAL) | (pool == TEAMS.index(t.agent))).any():
            raise ValueError(f"No crew can work on the {t.agent} network: set at least one {t.agent} crew or one general crew.")
    rel = np.zeros((n, n), dtype=bool)
    pos = {t.id: i for i, t in enumerate(tasks)}
    for i, t in enumerate(tasks):
        for r in t.relieved_by:
            if r in pos:
                rel[i, pos[r]] = True
    sums = [_summary(t.facts, A) for t in tasks]
    return Params(rate=np.array([s["c_delay"] for s in sums]), repair=np.array([s["repair_now"] for s in sums]),
                  hours=np.array([A[f"fix_hours_{t.policy}"] for t in tasks]),
                  late=np.array([A[f"late_multiplier_{t.policy}"] for t in tasks]), delay=np.array([A[f"delay_hours_{t.policy}"] for t in tasks]),
                  cap=A["late_multiplier_cap"], response=A["response_hours"], switch=A["crew_switch_hours"],
                  crews=len(pool), relieved=rel, pool=pool, team=team)


def evaluate(perms: np.ndarray, p: Params, detail: bool = False):
    """Cost of every order in `perms` (rows of task indices), all at once. With detail, also the schedule of each."""
    m, n = perms.shape
    ar = np.arange(m)
    free = np.full((m, p.crews), p.response)
    done = np.full((m, n), INF)
    loss, repair = np.zeros(m), np.zeros(m)
    if detail:
        start, end, slot, dropped, mult = (np.zeros((m, n)), np.zeros((m, n)), np.zeros((m, n), dtype=int), np.zeros((m, n), dtype=bool), np.ones((m, n)))
    for pos in range(n):
        idx = perms[:, pos]
        able = free if p.pool is None else np.where((p.pool == GENERAL) | (p.pool == p.team[idx][:, None]), free, INF)  # only crews that can do this job
        c = able.argmin(axis=1)
        s = free[ar, c]
        relief = np.where(p.relieved[idx], done, INF).min(axis=1)
        drop = relief <= s
        e = s + p.hours[idx]
        free[ar, c] = np.where(drop, s, e + p.switch)
        fin = np.where(drop, relief, e)
        done[ar, idx] = fin
        wait = np.maximum(0.0, s - p.response)
        mu = np.minimum(p.cap, 1 + (p.late[idx] - 1) * wait / p.delay[idx])
        loss += p.rate[idx] * fin
        repair += np.where(drop, 0.0, p.repair[idx] * mu)
        if detail:
            start[ar, idx], end[ar, idx], slot[ar, idx], dropped[ar, idx], mult[ar, idx] = s, fin, c, drop, np.where(drop, 1.0, mu)
    total = loss + repair
    return (total, dict(start=start, end=end, slot=slot, dropped=dropped, mult=mult)) if detail else total


def all_orders(n: int) -> np.ndarray:
    return np.array(list(itertools.permutations(range(n))), dtype=np.int64).reshape(-1, n)


def smith_order(tasks: list[Task], idx: list[int]) -> list[int]:
    return sorted(idx, key=lambda i: (-tasks[i].smith, tasks[i].detected_at))


def search_space(tasks: list[Task]) -> tuple[list[Task], list[Task]]:
    """The tasks searched exhaustively and any beyond MAX_TASKS, which go last in Smith order."""
    if len(tasks) <= MAX_TASKS:
        return tasks, []
    order = smith_order(tasks, list(range(len(tasks))))
    return [tasks[i] for i in order[:MAX_TASKS]], [tasks[i] for i in order[MAX_TASKS:]]


@dataclass
class Plan:
    order: list[int]  # indices into tasks
    cost: float
    start: np.ndarray
    end: np.ndarray
    slot: np.ndarray
    dropped: np.ndarray
    mult: np.ndarray


def plan_for(order: list[int], p: Params) -> Plan:
    perms = np.array([order], dtype=np.int64)
    total, d = evaluate(perms, p, detail=True)
    return Plan(order=list(order), cost=float(total[0]), start=d["start"][0], end=d["end"][0], slot=d["slot"][0], dropped=d["dropped"][0], mult=d["mult"][0])


def best_order(p: Params, n: int, perms: np.ndarray | None = None) -> tuple[list[int], float]:
    perms = all_orders(n) if perms is None else perms
    costs = evaluate(perms, p)
    i = int(np.argmin(costs))  # ties keep the first, which is the order the alarms arrived in
    return [int(x) for x in perms[i]], float(costs[i])


def signature(plan: Plan) -> tuple:
    """Who starts when. Two orders with the same schedule are the same plan."""
    return tuple(zip((round(float(x), 6) for x in plan.start), (bool(x) for x in plan.dropped)))


@dataclass
class Robustness:
    p10: float
    p50: float
    p90: float
    win_share: float
    regret_mean: float
    regret_p90: float
    runs: int


def robustness(tasks: list[Task], A: Assumptions, chosen: list[int], seed_key: str, runs: int = RUNS) -> Robustness:
    """The chosen order against the best order in each of `runs` price scenarios drawn from the registered ranges."""
    rng = np.random.default_rng(zlib.crc32(seed_key.encode()))
    perms = all_orders(len(tasks))
    row = int(np.where((perms == np.array(chosen)).all(axis=1))[0][0])
    costs, regrets, wins = [], [], []
    for _ in range(runs):
        s = A.sampled(rng)
        p = params_for(tasks, s)
        c = evaluate(perms, p)
        best = c.min()
        costs.append(c[row])
        regrets.append(c[row] - best)
        wins.append(c[row] <= best + 1e-6)
    pct = np.percentile(costs, [10, 50, 90])
    return Robustness(p10=float(pct[0]), p50=float(pct[1]), p90=float(pct[2]), win_share=float(np.mean(wins)),
                      regret_mean=float(np.mean(regrets)), regret_p90=float(np.percentile(regrets, 90)), runs=runs)


def flips(tasks: list[Task], p: Params, chosen: Plan, perms: np.ndarray) -> list[dict]:
    """For each task, the hourly cost at which the best schedule would change: what would change the board's mind."""
    out = []
    base = signature(chosen)
    for i, t in enumerate(tasks):
        best = None

        def changed(scale: float) -> bool:
            rate = p.rate.copy()
            rate[i] *= scale
            q = dataclasses.replace(p, rate=rate)
            order = [int(x) for x in perms[int(np.argmin(evaluate(perms, q)))]]
            return signature(plan_for(order, q)) != base

        for s in sorted({round(x, 3) for x in np.concatenate([np.linspace(0.0, 1.0, 41), np.linspace(1.0, 6.0, 51)])}, key=lambda x: abs(x - 1.0)):
            if s == 1.0:
                continue
            rate = p.rate.copy()
            rate[i] *= s
            q = dataclasses.replace(p, rate=rate)
            costs = evaluate(perms, q)
            order = [int(x) for x in perms[int(np.argmin(costs))]]
            if signature(plan_for(order, q)) != base:
                best = s
                break
        if best is not None:  # the grid says the plan changes somewhere between 1.0 and `best`: bisect to the point where it does, so the figure quoted is the boundary and not the next grid step
            lo, hi = 1.0, best
            for _ in range(24):
                mid = (lo + hi) / 2
                lo, hi = (lo, mid) if changed(mid) else (mid, hi)
            best = hi
        if best is not None and p.rate[i] * best > 0.5:  # a threshold at zero only says the task keeps its place however cheap it gets
            out.append(dict(task=t, current=float(p.rate[i]), threshold=float(p.rate[i] * best), direction="below" if best < 1 else "above", scale=best))
    out.sort(key=lambda f: abs(f["scale"] - 1.0))
    return out
