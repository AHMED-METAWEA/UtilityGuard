"""Static synthetic twin of a slice of the New Administrative Capital.

Layout follows the real district order (north to south): Diplomatic / Government /
Presidential, the Green River, then R2 / CBD / R5, then R7 / Medical City / R8.
Coordinates are real-world-ish around 30.02 N, 31.72 E; the network is synthetic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache

from app.schemas import Asset, Geometry, LatLng, Link, Range, Sensor, Topology, Zone

# (id, name, type, south, west, north, east)
_ZONES = [
    ("zone-dip", "Diplomatic District", "government", 30.040, 31.700, 30.052, 31.715),
    ("zone-gov", "Government District", "government", 30.040, 31.720, 30.052, 31.737),
    ("zone-pres", "Presidential District", "government", 30.040, 31.742, 30.052, 31.757),
    ("zone-res-2", "Residential R2", "residential", 30.018, 31.700, 30.030, 31.714),
    ("zone-cbd", "Central Business District", "business", 30.018, 31.719, 30.030, 31.738),
    ("zone-res-5", "Residential R5", "residential", 30.018, 31.743, 30.030, 31.757),
    ("zone-res-7", "Residential R7", "residential", 30.003, 31.700, 30.015, 31.714),
    ("zone-med", "Medical City", "services", 30.003, 31.719, 30.015, 31.738),
    ("zone-res-8", "Residential R8", "residential", 30.003, 31.743, 30.015, 31.757),
]

# Base demand per zone (water m3/h, electricity MW, cooling ton-of-refrigeration-equivalent MW)
ZONE_BASE = {
    "zone-dip": (420, 18, 6), "zone-gov": (640, 34, 14), "zone-pres": (380, 16, 6),
    "zone-res-2": (560, 22, 0), "zone-cbd": (720, 42, 16), "zone-res-5": (500, 20, 4),
    "zone-res-7": (460, 17, 0), "zone-med": (520, 26, 9), "zone-res-8": (430, 16, 0),
}

# Which substation powers each zone
ZONE_SUBSTATION = {
    "zone-dip": "elec-sub-01", "zone-res-2": "elec-sub-01", "zone-res-7": "elec-sub-01",
    "zone-gov": "elec-sub-02", "zone-cbd": "elec-sub-02",
    "zone-pres": "elec-sub-03", "zone-res-5": "elec-sub-03",
    "zone-med": "elec-sub-04", "zone-res-8": "elec-sub-04",
}
# Backup substation used when the primary trips
SUBSTATION_BACKUP = {"elec-sub-01": "elec-sub-02", "elec-sub-02": "elec-sub-03",
                     "elec-sub-03": "elec-sub-04", "elec-sub-04": "elec-sub-01"}

# District cooling plants and the zones they serve
COOLING_PLANTS = {
    "cool-plant-01": ("DCP-01 Government", "zone-gov", ["zone-dip", "zone-gov", "zone-pres"]),
    "cool-plant-02": ("DCP-02 CBD", "zone-cbd", ["zone-cbd", "zone-res-5", "zone-med"]),
}

# Water: the reservoir feeds the north trunk; the spine carries water south.
TRUNK_ORDER = ["zone-dip", "zone-gov", "zone-pres"]  # north trunk, west -> east
SPINE = {"zone-res-2": "zone-dip", "zone-res-7": "zone-res-2", "zone-cbd": "zone-gov",
         "zone-med": "zone-cbd", "zone-res-5": "zone-pres", "zone-res-8": "zone-res-5"}

UNITS = {"pressure": "bar", "flow": "m3/h", "load": "MW", "voltage": "kV", "temperature": "°C"}


@dataclass
class TwinGraph:
    topology: Topology
    zone_index: dict[str, Zone]
    asset_index: dict[str, Asset]
    sensor_index: dict[str, Sensor]
    zone_pump: dict[str, str] = field(default_factory=dict)
    zone_pipe: dict[str, str] = field(default_factory=dict)
    zone_feeder: dict[str, str] = field(default_factory=dict)
    zone_loop: dict[str, str] = field(default_factory=dict)
    upstream_zone: dict[str, str | None] = field(default_factory=dict)


def _ll(lat: float, lng: float) -> LatLng:
    return LatLng(lat=round(lat, 6), lng=round(lng, 6))


def _pt(lat: float, lng: float) -> Geometry:
    return Geometry(type="point", coordinates=[_ll(lat, lng)])


def _line(*pts: tuple[float, float]) -> Geometry:
    return Geometry(type="line", coordinates=[_ll(a, b) for a, b in pts])


@lru_cache
def build() -> TwinGraph:
    zones, assets, sensors, links = [], [], [], []
    boxes = {}
    for zid, name, ztype, s, w, n, e in _ZONES:
        boxes[zid] = (s, w, n, e)
        zones.append(Zone(id=zid, name=name, type=ztype, center=_ll((s + n) / 2, (w + e) / 2),
                          polygon=[_ll(s, w), _ll(s, e), _ll(n, e), _ll(n, w), _ll(s, w)]))

    def ctr(z):
        s, w, n, e = boxes[z]
        return (s + n) / 2, (w + e) / 2

    def add_sensor(asset_id, net, kind, lo, hi, pos, suffix=None):
        sid = f"{asset_id}-{suffix or kind}"
        sensors.append(Sensor(id=sid, asset_id=asset_id, network=net, kind=kind, unit=UNITS[kind],
                              normal_range=Range(min=lo, max=hi), position=_ll(*pos)))

    g = TwinGraph(Topology(zones=zones, assets=[], sensors=[], links=[]), {}, {}, {})

    # ---------- electricity ----------
    sub_pos = {"elec-sub-01": (30.034, 31.703), "elec-sub-02": (30.037, 31.728),
               "elec-sub-03": (30.034, 31.752), "elec-sub-04": (30.016, 31.740)}
    for i, (sid, pos) in enumerate(sub_pos.items(), 1):
        served = [z for z, s in ZONE_SUBSTATION.items() if s == sid]
        zone = served[0] if sid != "elec-sub-02" else "zone-gov"
        assets.append(Asset(id=sid, network="electricity", kind="substation", name=f"SS-0{i}",
                            zoneId=zone, geometry=_pt(*pos)))
        add_sensor(sid, "electricity", "load", 10, 110, pos)
        add_sensor(sid, "electricity", "voltage", 21.0, 23.0, pos)

    for i, (z, sub) in enumerate(ZONE_SUBSTATION.items(), 1):
        c = ctr(z)
        fid, tid = f"elec-feeder-{i:02d}", f"elec-tx-{i:02d}"
        g.zone_feeder[z] = fid
        sp = sub_pos[sub]
        tx = (c[0] - 0.003, c[1] - 0.003)
        assets.append(Asset(id=fid, network="electricity", kind="feeder", name=f"Feeder F-{i:02d}",
                            zoneId=z, geometry=_line(sp, (sp[0], tx[1]), tx)))
        assets.append(Asset(id=tid, network="electricity", kind="transformer", name=f"TX-{i:02d}",
                            zoneId=z, geometry=_pt(*tx)))
        add_sensor(fid, "electricity", "load", 2, 60, tx)
        add_sensor(tid, "electricity", "temperature", 35, 85, tx)
        links.append(Link(fromAsset=sub, toAsset=fid, relation="feeds"))
        links.append(Link(fromAsset=fid, toAsset=tid, relation="feeds"))

    # ---------- water ----------
    res = (30.046, 31.6965)
    assets.append(Asset(id="water-res-01", network="water", kind="reservoir", name="RES-01",
                        zoneId="zone-dip", geometry=_pt(*res)))
    add_sensor("water-res-01", "water", "pressure", 4.2, 5.2, res)
    add_sensor("water-res-01", "water", "flow", 2000, 6500, res)
    links.append(Link(fromAsset="elec-sub-01", toAsset="water-res-01", relation="powers"))

    pipe_n = 0
    for i, z in enumerate(ZONE_SUBSTATION, 1):
        c = ctr(z)
        pump = f"water-pump-{i:02d}"
        g.zone_pump[z] = pump
        ppos = (c[0] + 0.004, c[1] - 0.004)
        assets.append(Asset(id=pump, network="water", kind="pump", name=f"P-{i:02d}",
                            zoneId=z, geometry=_pt(*ppos)))
        add_sensor(pump, "water", "pressure", 3.2, 4.6, ppos)
        add_sensor(pump, "water", "flow", 150, 1100, ppos)
        links.append(Link(fromAsset=ZONE_SUBSTATION[z], toAsset=pump, relation="powers"))

        # feed pipe from upstream (reservoir or upstream zone) into this zone's pump
        if z in TRUNK_ORDER:
            k = TRUNK_ORDER.index(z)
            up = None if k == 0 else TRUNK_ORDER[k - 1]
        else:
            up = SPINE[z]
        g.upstream_zone[z] = up
        start = res if up is None else (ctr(up)[0] + 0.004, ctr(up)[1] - 0.004)
        pipe_n += 1
        pid = f"water-pipe-{pipe_n:02d}"
        g.zone_pipe[z] = pid
        mid = (start[0], ppos[1]) if up is None or z in TRUNK_ORDER else (ppos[0], start[1])
        assets.append(Asset(id=pid, network="water", kind="pipe", name=f"Main WP-{z.split('-', 1)[1].upper()}-{pipe_n:02d}",
                            zoneId=z, geometry=_line(start, mid, ppos)))
        add_sensor(pid, "water", "flow", 150, 4000, start, "flow-in")
        add_sensor(pid, "water", "flow", 150, 4000, ppos, "flow-out")
        add_sensor(pid, "water", "pressure", 2.5, 4.8, mid)
        links.append(Link(fromAsset=pid, toAsset=pump, relation="supplies"))
        if up:
            links.append(Link(fromAsset=g.zone_pump[up], toAsset=pid, relation="supplies"))
        else:
            links.append(Link(fromAsset="water-res-01", toAsset=pid, relation="supplies"))

    # ---------- cooling ----------
    for pid, (name, home, served) in COOLING_PLANTS.items():
        hc = ctr(home)
        pos = (hc[0] - 0.004, hc[1] + 0.005)
        assets.append(Asset(id=pid, network="cooling", kind="plant", name=name.split()[0],
                            zoneId=home, geometry=_pt(*pos)))
        add_sensor(pid, "cooling", "temperature", 5.0, 7.0, pos, "supply-temp")
        add_sensor(pid, "cooling", "flow", 800, 3200, pos)
        add_sensor(pid, "cooling", "load", 4, 45, pos)
        links.append(Link(fromAsset=ZONE_SUBSTATION[home], toAsset=pid, relation="powers"))
        for z in served:
            lid = f"cool-loop-{len(g.zone_loop) + 1:02d}"
            g.zone_loop[z] = lid
            zc = ctr(z)
            s, w, n, e = boxes[z]
            ring = [(s + 0.002, w + 0.002), (s + 0.002, e - 0.002), (n - 0.002, e - 0.002),
                    (n - 0.002, w + 0.002), (s + 0.002, w + 0.002)]
            assets.append(Asset(id=lid, network="cooling", kind="loop", name=f"Loop CL-{len(g.zone_loop):02d}",
                                zoneId=z, geometry=_line(pos, (zc[0], pos[1]), *ring)))
            add_sensor(lid, "cooling", "temperature", 11.0, 14.0, ring[2], "return-temp")
            add_sensor(lid, "cooling", "flow", 150, 1400, ring[0])
            links.append(Link(fromAsset=pid, toAsset=lid, relation="cools"))

    g.topology = Topology(zones=zones, assets=assets, sensors=sensors, links=links)
    g.zone_index = {z.id: z for z in zones}
    g.asset_index = {a.id: a for a in assets}
    g.sensor_index = {s.id: s for s in sensors}
    return g
