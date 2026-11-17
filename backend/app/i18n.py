"""Arabic wording for what the code itself writes (names, units, labels), and the shaping Arabic needs in a PDF.

The agents, the detector and the report each keep their texts in English and Arabic side by side; this module holds what they share: how a zone, an asset, a unit, a
severity or a kind of fault reads in Arabic. Digits stay Western (22,508 and not the Arabic-Indic form) in both languages, so that every number can be compared with the
calculation record, and the number checker reads both.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Literal

Lang = Literal["en", "ar"]
LANGS = ("en", "ar")
DATA = Path(__file__).resolve().parents[1] / "data"

ZONE_AR = {"zone-gov": "الحي الحكومي", "zone-cult": "مدينة الفنون والثقافة", "zone-cbd": "حي الأعمال المركزي", "zone-res-2": "الحي السكني R2",
           "zone-just": "مدينة العدالة", "zone-pres": "الحي الرئاسي", "zone-res-n": "الحي السكني الشمالي", "zone-res-ne": "الحي السكني الشمالي الشرقي",
           "zone-res-far": "الحي السكني أقصى الشمال الشرقي"}
NETWORK_AR = {"water": "المياه", "electricity": "الكهرباء", "cooling": "التبريد"}
AGENT_AR = {"water": "وكيل المياه", "electricity": "وكيل الكهرباء", "cooling": "وكيل التبريد", "orchestrator": "المنسّق"}
SEVERITY_AR = {"critical": "حرج", "warning": "تحذير", "info": "معلومة"}
STATE_AR = {"open": "مفتوح", "acknowledged": "تم الاطلاع", "resolved": "تم الحل"}
URGENCY_AR = {"immediate": "فوري", "within_hours": "خلال ساعات", "scheduled": "مجدول"}
EVIDENCE_AR = {"measured": "مقاس", "forecast": "تنبؤ", "rule": "قاعدة", "assumed": "افتراض"}
CREW_AR = {"pipe repair": "إصلاح المواسير", "mechanical": "ميكانيكا", "electrical": "كهرباء", "HVAC": "تكييف وتبريد", "operations": "تشغيل",
           "instrumentation": "أجهزة قياس"}
KIND_AR = {"leak": "تسريب أو كسر في ماسورة", "pump_failure": "عطل في طلمبة", "substation_trip": "فصل محطة كهرباء", "cooling_fault": "عطل في محطة التبريد",
           "energy_waste": "حمل زائد على محطة كهرباء", "sensor_fault": "عطل في حساس"}

UNIT_AR = {"EGP": "جنيه", "MW": "ميجاوات", "kW": "كيلوواط", "kV": "كيلوفولت", "bar": "بار", "m3/h": "م³/ساعة", "m3": "م³", "°C": "°م", "tons": "طن تبريد",
           "ton": "طن تبريد", "h": "ساعة", "kWh": "كيلوواط ساعة"}
_UNIT = re.compile(r"(?<![A-Za-z0-9])(EGP|MW|kW|kV|bar|m3/h|m3|kWh)(?![A-Za-z0-9])")


def unit_ar(text: str) -> str:
    """A figure that reads '22,508 EGP' reads '22,508 جنيه' in Arabic. Only the unit changes, never the number."""
    return _UNIT.sub(lambda m: UNIT_AR[m.group(1)], text)


_UNIT_PART = {"EGP": "جنيه", "MW": "ميجاوات", "kW": "كيلوواط", "kV": "كيلوفولت", "bar": "بار", "m3": "م³", "h": "ساعة", "kWh": "كيلوواط ساعة", "customers": "عميل",
              "customer-h": "عميل-ساعة", "ton-h": "طن تبريد-ساعة", "dispatch": "إرسال", "repair": "إصلاح", "fraction": "نسبة", "×": "×", "%": "%", "°C": "°م", "tons": "طن تبريد",
              "crews": "طاقم", "fraction/°C": "نسبة/°م", "fraction/year": "نسبة/سنة", "h/dispatch": "ساعة/إرسال", "EGP/dispatch": "جنيه/إرسال"}


def unit_text_ar(unit: str) -> str:
    """A unit on its own ('EGP/h', 'm3/h', 'EGP/customer-h') in Arabic. Each part between slashes is translated; a part with no Arabic form stays as it is."""
    if unit in _UNIT_PART:
        return _UNIT_PART[unit]
    return "/".join(_UNIT_PART.get(part, part) for part in unit.split("/"))


def num_text(v: float) -> str:
    """A figure for a table: thousands separators from a thousand up, four significant digits below, and never the 2.365e+04 form."""
    a = abs(v)
    return f"{v:,.0f}" if a >= 1000 else f"{v:.4g}"


def ar_hours(h: float) -> str:
    """'6 hours' in Arabic grammar: one hour, two hours, 3 to 10 take the plural, above that the singular after the figure."""
    n = int(h) if float(h).is_integer() else None
    if n == 1:
        return "ساعة واحدة"
    if n == 2:
        return "ساعتان"
    if n is not None and 3 <= n <= 10:
        return f"{n} ساعات"
    return f"{h:g} ساعة"


SENSOR_AR = {"load": "حمل", "voltage": "جهد", "pressure": "ضغط", "flow": "تدفق", "flow in": "التدفق الداخل إلى", "flow out": "التدفق الخارج من", "supply temp": "حرارة التغذية في",
             "return temp": "حرارة العودة في", "temperature": "حرارة"}


def sensor_ar(sensor_id: str, asset_id: str, asset_name: str) -> str:
    """A sensor as a person says it: 'water-pipe-03-pressure' is 'ضغط الخط الرئيسي WP-CBD-03'. The id stays in the data; the words are for reading."""
    suffix = sensor_id.removeprefix(asset_id + "-").replace("-", " ")
    return f"{SENSOR_AR.get(suffix, suffix)} {asset_ar(asset_name)}"


def asset_ar(name: str) -> str:
    """An asset's code with what it is, in Arabic: 'P-03' is 'المضخة P-03'. The code itself is never translated, it is what is painted on the equipment."""
    if name.startswith("SS-"):
        return f"المحطة {name}"
    if name.startswith("P-"):
        return f"المضخة {name}"
    if name.startswith("TX-"):
        return f"المحول {name}"
    if name.startswith("DCP-"):
        return f"محطة التبريد {name}"
    if name.startswith("RES-"):
        return f"الخزان {name}"
    if name.startswith("Feeder "):
        return f"المغذي {name[7:]}"
    if name.startswith("Main "):
        return f"الخط الرئيسي {name[5:]}"
    if name.startswith("Loop "):
        return f"دورة التبريد {name[5:]}"
    return name


# ---------------------------------------------------------------- the language the agents speak
class Settings:
    """The language of the agents, kept in a file so that a restart does not change it. The report language is chosen per download and is not stored here."""

    def __init__(self, path: Path | None = None):
        self.path = path or Path(os.getenv("UG_SETTINGS", DATA / "settings.json"))
        self.agent_lang: Lang = "en"
        try:
            raw = json.loads(self.path.read_text())
            if raw.get("agentLang") in LANGS:
                self.agent_lang = raw["agentLang"]
        except Exception:  # noqa: BLE001 - a missing or damaged file means the default
            pass

    def set_agent_lang(self, lang: str) -> Lang:
        if lang not in LANGS:
            raise ValueError("lang must be en or ar")
        self.agent_lang = lang  # type: ignore[assignment]
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps({"agentLang": lang}))
        except OSError:
            pass
        return self.agent_lang


settings = Settings()


# ---------------------------------------------------------------- Arabic in a PDF
def shape(text: str) -> str:
    """ReportLab draws glyphs left to right and does not join Arabic letters. The reshaper picks the joined forms and the bidi step puts the line in reading order,
    so that Arabic runs right to left while an asset code or a number inside it stays as it is written."""
    import arabic_reshaper
    from bidi.algorithm import get_display

    return get_display(arabic_reshaper.reshape(text))
