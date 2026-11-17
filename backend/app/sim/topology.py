"""Static synthetic twin placed on the real geography of the New Administrative Capital.

District positions follow OpenStreetMap: R2 and the CBD (Iconic Tower) in the west by the
Green River, the Arts and Culture City, the Government District (Parliament, Cabinet,
ministries) in the east, Justice City to its south-east, the Presidential District to the
north, and residential districts to the north and north-east. The utility network itself
is synthetic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache

from app.schemas import Asset, Geometry, LatLng, Link, Range, Sensor, Topology, Zone

# (id, name, type, south, west, north, east)
_ZONES = [
    ("zone-gov", "Government District", "government", 30.0070, 31.7380, 30.0210, 31.7660),
    ("zone-cult", "Arts and Culture City", "services", 30.0050, 31.7160, 30.0170, 31.7360),
    ("zone-cbd", "Central Business District", "business", 30.0050, 31.6870, 30.0175, 31.7060),
    ("zone-res-2", "Residential R2", "residential", 30.0130, 31.6580, 30.0260, 31.6800),
    ("zone-just", "Justice City", "government", 30.0000, 31.7670, 30.0130, 31.7800),
    ("zone-pres", "Presidential District", "government", 30.0245, 31.7480, 30.0360, 31.7630),
    ("zone-res-n", "Residential North", "residential", 30.0270, 31.7300, 30.0450, 31.7470),
    ("zone-res-ne", "Residential North-East", "residential", 30.0280, 31.7660, 30.0440, 31.7820),
    ("zone-res-far", "Residential Far North-East", "residential", 30.0450, 31.7720, 30.0610, 31.7880),
]

# Base demand per zone (water m3/h, electricity MW, cooling MW thermal)
ZONE_BASE = {
    "zone-gov": (640, 34, 14), "zone-cult": (420, 18, 6), "zone-cbd": (720, 42, 16),
    "zone-res-2": (560, 22, 0), "zone-just": (380, 16, 6), "zone-pres": (380, 16, 6),
    "zone-res-n": (500, 20, 0), "zone-res-ne": (460, 17, 0), "zone-res-far": (430, 16, 0),
}

# Planning service-account counts; verify against the district connection register.
ZONE_CUSTOMERS = {
    "zone-gov": 500, "zone-cult": 300, "zone-cbd": 1200, "zone-res-2": 8000,
    "zone-just": 250, "zone-pres": 200, "zone-res-n": 7000, "zone-res-ne": 6000, "zone-res-far": 5500,
}

# Which substation powers each zone
ZONE_SUBSTATION = {
    "zone-res-2": "elec-sub-01", "zone-cbd": "elec-sub-01",
    "zone-gov": "elec-sub-02", "zone-cult": "elec-sub-02",
    "zone-pres": "elec-sub-03", "zone-res-n": "elec-sub-03",
    "zone-just": "elec-sub-04", "zone-res-ne": "elec-sub-04", "zone-res-far": "elec-sub-04",
}
# Backup substation used when the primary trips
SUBSTATION_BACKUP = {"elec-sub-01": "elec-sub-02", "elec-sub-02": "elec-sub-03",
                     "elec-sub-03": "elec-sub-04", "elec-sub-04": "elec-sub-01"}
SUBSTATION_POS = {"elec-sub-01": (30.0195, 31.6840), "elec-sub-02": (30.0228, 31.7400),
                  "elec-sub-03": (30.0385, 31.7520), "elec-sub-04": (30.0270, 31.7790)}
RESERVOIR_POS = (30.0228, 31.7330)

# District cooling plants and the zones they serve
COOLING_PLANTS = {
    "cool-plant-01": ("DCP-01 Government", "zone-gov", ["zone-gov", "zone-pres", "zone-just"]),
    "cool-plant-02": ("DCP-02 CBD", "zone-cbd", ["zone-cbd", "zone-cult"]),
}

# Water: the reservoir feeds the west trunk (Government -> Culture City -> CBD -> R2);
# branches carry water north and south-east from the Government District.
TRUNK_ORDER = ["zone-gov", "zone-cult", "zone-cbd", "zone-res-2"]
SPINE = {"zone-just": "zone-gov", "zone-pres": "zone-gov", "zone-res-n": "zone-pres",
         "zone-res-ne": "zone-pres", "zone-res-far": "zone-res-ne"}

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
    calibrated: bool = False


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
    sub_pos = SUBSTATION_POS
    for i, (sid, pos) in enumerate(sub_pos.items(), 1):
        served = [z for z, s in ZONE_SUBSTATION.items() if s == sid]
        zone = min(served, key=lambda z: (ctr(z)[0] - pos[0]) ** 2 + (ctr(z)[1] - pos[1]) ** 2)
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
    res = RESERVOIR_POS
    assets.append(Asset(id="water-res-01", network="water", kind="reservoir", name="RES-01",
                        zoneId="zone-gov", geometry=_pt(*res)))
    add_sensor("water-res-01", "water", "pressure", 4.2, 5.2, res)
    add_sensor("water-res-01", "water", "flow", 2000, 6500, res)
    links.append(Link(fromAsset="elec-sub-02", toAsset="water-res-01", relation="powers"))

    pipe_n = 0
    for i, z in enumerate(TRUNK_ORDER + list(SPINE), 1):  # upstream zones first
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
