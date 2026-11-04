"""The incident report in Arabic.

Same content as `extras.incident_pdf`: the alerts, each agent's finding, why the decision, the cost of acting now against waiting, the calculations, the assumptions, the
sensitivity and the agent steps. What changes is the language and the direction: the page reads right to left, tables run right to left, and Arabic letters are joined.

ReportLab draws one glyph after another from left to right and does not know Arabic, so the text is prepared here: a paragraph is first cut into lines (measured with the
joined letters, in the font that will draw them) and each line is then reshaped and put into reading order on its own. Shaping a whole paragraph and letting ReportLab cut it
afterwards would put the end of the sentence on the first line.

Figures are the calculation record's own, with Western digits. A name the cost engine only has in English (a few of its provenance sentences) stays in English and is
drawn in Latin letters; nothing is translated by a model.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import TYPE_CHECKING
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from app.i18n import AGENT_AR, CREW_AR, EVIDENCE_AR, NETWORK_AR, SEVERITY_AR, STATE_AR, URGENCY_AR, ZONE_AR, ar_hours, num_text, shape, unit_text_ar

if TYPE_CHECKING:
    from app.runtime import Runtime

FONTS = Path(__file__).resolve().parents[1] / "assets" / "fonts"
REG, BOLD = "Amiri", "Amiri-Bold"
_registered = False

CALC_AR = {
    "Water loss": "فقد المياه", "Energy waste": "إهدار الطاقة", "Cooling shortfall": "عجز التبريد", "Knock-on overdraw in another network": "سحب زائد متتابع في شبكة أخرى",
    "False dispatches": "إرسال طواقم لإنذارات كاذبة", "Repair now": "الإصلاح الآن", "Cost of delay per hour": "تكلفة التأخير في الساعة",
    "Losses until the crew arrives": "الخسائر حتى وصول الطاقم", "Cost of acting now": "تكلفة التصرف الآن", "Repair later": "الإصلاح لاحقًا", "Cost of delay": "تكلفة التأخير",
    "Saving from acting now": "الوفر من التصرف الآن",
}
KIND_NAME_AR = {"pipe_burst": "كسر ماسورة", "pump_failure": "عطل مضخة", "substation_trip": "فصل محطة", "cooling_fault": "عطل تبريد", "stuck_sensor": "حساس عالق", "energy_waste": "إهدار طاقة",
                "leak": "تسريب", "sensor_fault": "عطل حساس"}
TYPE_AR = {"government": "حكومي", "business": "أعمال", "residential": "سكني", "services": "خدمات"}
ASSUMPTION_AR = {
    "water_price": "تكلفة إنتاج المياه", "elec_tariff": "تعريفة الكهرباء", "cooling_price": "تكلفة طاقة التبريد", "crew_callout": "تكلفة استدعاء الطاقم",
    "parts_pipe": "قطع إصلاح الماسورة", "parts_pump": "قطع إصلاح المضخة", "parts_substation": "إصلاح المحطة وفحصها", "parts_chiller": "قطع إصلاح المبرّد",
    "parts_sensor": "معايرة الحساس أو استبداله", "response_hours": "زمن استجابة الطاقم عند الإرسال الآن", "isolate_loss_reduction": "خفض الفقد بعد عزل الماسورة",
    "late_multiplier_cap": "الحد الأقصى لمضاعِف الإصلاح المتأخر", "false_dispatch": "تكلفة طاقم أُرسل لإنذار كاذب", "false_dispatch_interval": "الفاصل بين الإرسالات الكاذبة",
    "planned_factor": "تكلفة الإصلاح المخطط نسبةً للطارئ", "background_loss_ratio": "التسرب الأساسي نسبةً للطلب المفوتر", "crews_available": "الطواقم المتاحة في آن واحد",
    "crew_switch_hours": "تنقّل الطاقم بين مهمتين",
}
SOURCE_AR = {"default assumption": "افتراض افتراضي"}


def formula_ar(formula: str) -> str:
    """The formulas are symbols and stay as they are; the one letter the Arabic font has no glyph for is spelled."""
    return formula.replace("Δ", "delta_").replace("−", "-")


def calc_name_ar(name: str) -> str:
    if name.startswith("Saving range ("):
        runs = "".join(ch for ch in name if ch.isdigit())
        return f"نطاق الوفر ({runs} تشغيل محاكى)"
    if name.startswith("Delayed cost: "):
        return "تكلفة التأخير: " + calc_name_ar(name[len("Delayed cost: "):])
    for prefix, ar in (("Energy not served", "طاقة غير مخدومة"), ("Service disruption", "تعطّل الخدمة")):
        if name.startswith(prefix):
            zone = name[len(prefix):].lstrip(", ")
            return f"{ar}، {ZONE_AR.get(zone, zone)}" if zone else ar
    return CALC_AR.get(name, name)


def assumption_name_ar(key: str, name: str) -> str:
    if key in ASSUMPTION_AR:
        return ASSUMPTION_AR[key]
    for prefix, ar in (("voll_", "قيمة الحمل المفقود"), ("customer_hour_", "إدارة الانقطاع (ساعة لكل عميل)")):
        if key.startswith(prefix) and key[len(prefix):] in TYPE_AR:
            return f"{ar}، {TYPE_AR[key[len(prefix):]]}"
    if key.startswith("customers_zone_"):
        zone = "zone-" + key[len("customers_zone_"):].replace("_", "-")
        return f"حسابات الخدمة، {ZONE_AR.get(zone, zone)}"
    for prefix, ar in (("delay_hours_", "أفق الاستجابة المؤجلة"), ("late_multiplier_", "مضاعِف الإصلاح المتأخر"), ("fix_hours_", "زمن العمل في الموقع"),
                       ("saving_min_", "أدنى وفر معقول"), ("saving_max_", "أقصى وفر معقول")):
        if key.startswith(prefix):
            kind = key[len(prefix):]
            return f"{ar}، {KIND_NAME_AR.get(kind, kind)}"
    return name


def _fonts() -> None:
    global _registered
    if _registered:
        return
    pdfmetrics.registerFont(TTFont(REG, str(FONTS / "Amiri-Regular.ttf")))
    pdfmetrics.registerFont(TTFont(BOLD, str(FONTS / "Amiri-Bold.ttf")))
    pdfmetrics.registerFontFamily(REG, normal=REG, bold=BOLD, italic=REG, boldItalic=BOLD)
    _registered = True


def wrap(text: str, font: str, size: float, width: float) -> list[str]:
    """Cut a logical-order line of text into lines that fit `width`, measured as they will be drawn (joined letters), then shape each line on its own."""
    lines: list[str] = []
    cur = ""
    for word in text.split(" "):
        cand = f"{cur} {word}".strip()
        if cur and stringWidth(shape(cand), font, size) > width:
            lines.append(cur)
            cur = word
        else:
            cur = cand
    lines.append(cur)
    return [shape(x) for x in lines]


def incident_pdf_ar(rt: "Runtime", incident_id: str) -> bytes:
    _fonts()
    inc = rt.incidents[incident_id]
    buf = io.BytesIO()
    left = right = 16 * mm
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=left, rightMargin=right, topMargin=14 * mm, bottomMargin=14 * mm, title=f"UtilityGuard incident {inc.id}")
    full = A4[0] - left - right
    navy, line = colors.HexColor("#0B1630"), colors.HexColor("#C9D2E3")
    st_body = ParagraphStyle("b", fontName=REG, fontSize=10.5, leading=17, alignment=TA_RIGHT)
    st_cell = ParagraphStyle("c", fontName=REG, fontSize=9.5, leading=14, alignment=TA_RIGHT)
    st_head = ParagraphStyle("h", fontName=BOLD, fontSize=9.5, leading=14, alignment=TA_RIGHT, textColor=colors.white)
    st_h1 = ParagraphStyle("h1", fontName=BOLD, fontSize=19, leading=28, alignment=TA_RIGHT)
    st_h2 = ParagraphStyle("h2", fontName=BOLD, fontSize=14, leading=22, alignment=TA_RIGHT, spaceBefore=8)
    st_h3 = ParagraphStyle("h3", fontName=BOLD, fontSize=11.5, leading=19, alignment=TA_RIGHT, spaceBefore=4)
    st_note = ParagraphStyle("n", fontName=REG, fontSize=9.5, leading=15, alignment=TA_RIGHT, textColor=colors.HexColor("#556"))

    def para(text: str, style: ParagraphStyle, width: float = full) -> Paragraph:
        # a frame and a table cell each keep 6 points of padding on both sides; a line cut wider than what is left would be cut again by ReportLab
        font, size = style.fontName, style.fontSize
        return Paragraph("<br/>".join(escape(x) for x in wrap(str(text), font, size, width - 16)), style)

    def table(rows: list[list[str]], widths_mm: list[float]) -> Table:
        """Columns run right to left: the first column of the logical row is the right-most on the page."""
        widths = [w * mm for w in widths_mm]
        cells = []
        for r, row in enumerate(rows):
            cells.append([para(c, st_head if r == 0 else st_cell, w) for c, w in zip(row, widths)][::-1])
        t = Table(cells, colWidths=widths[::-1], repeatRows=1)
        t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.4, line), ("VALIGN", (0, 0), (-1, -1), "TOP"), ("BACKGROUND", (0, 0), (-1, 0), navy)]))
        return t

    g = rt.engine.g
    st = [para("تقرير الحادثة، UtilityGuard Twin", st_h1),
          para(f"{inc.title_ar or inc.title} ({inc.id}). الخطورة: {SEVERITY_AR.get(inc.severity, inc.severity)}، الحالة: {STATE_AR.get(inc.state, inc.state)}. "
               f"فُتحت في {inc.opened_at:%Y-%m-%d %H:%M} بتوقيت UTC. الشبكات: {'، '.join(NETWORK_AR.get(n, n) for n in inc.networks)}. "
               f"المناطق: {'، '.join(ZONE_AR.get(z, g.zone_index[z].name) for z in inc.zone_ids)}.", st_body),
          Spacer(1, 6), para("التنبيهات", st_h2),
          table([["التنبيه", "الخطورة", "العنوان", "وقت الاكتشاف", "تقدير القاعدة"]] +
                [[a.id, SEVERITY_AR.get(a.severity, a.severity), a.title_ar or a.title, f"{a.detected_at:%H:%M:%S}", f"{a.confidence:.0%}"]
                 for a in (rt.alerts[x] for x in inc.alert_ids)], [24, 22, 78, 28, 26])]
    for fid in inc.finding_ids:
        f = rt.findings[fid]
        t = f.ar
        c, e = f.cost, f.explain
        headline = t.headline if t else f.headline
        action = t.action if t else f.recommended_action.text
        st += [para(f"{AGENT_AR.get(f.agent, f.agent)}: {headline}", st_h2), para(t.explanation if t else f.explanation, st_body), Spacer(1, 4),
               para(f"الإجراء الموصى به: {action} (الفريق: {CREW_AR.get(f.recommended_action.crew_type, f.recommended_action.crew_type)}، "
                    f"الإلحاح: {URGENCY_AR.get(f.recommended_action.urgency, f.recommended_action.urgency)})", st_body)]
        if t:
            st += [para("الأسباب المحتملة (فرضيات يؤكدها الفريق في الموقع)", st_h3)] + [para(f"{i}. {x}", st_body) for i, x in enumerate(t.probable_causes, 1)]
            st += [para(f"الأثر المتوقع: {t.impact}", st_body)]
        st += [para("لماذا هذا القرار", st_h3)]
        st += [para(f"{i}. {r.text_ar or r.text} ({EVIDENCE_AR.get(r.evidence_type, r.evidence_type)})", st_body) for i, r in enumerate(e.reasons, 1)]
        st += [para("التكلفة: التصرف الآن مقابل التأخير", st_h3),
               table([["التصرف الآن", f"التأخير {ar_hours(c.delay_hours or 0)}", "الوفر", "تقدير القاعدة"],
                      [f"{c.act_now:,.0f} جنيه", f"{c.cost_of_delay:,.0f} جنيه", f"{c.expected_saving:,.0f} جنيه", f"{e.confidence:.0%}"]], [44, 44, 44, 44]),
               para("الحسابات", st_h3),
               table([["الاسم", "المعادلة", "المدخلات", "النتيجة"]] +
                     [[calc_name_ar(k.name), formula_ar(k.formula),
                       "؛ ".join(f"{assumption_name_ar(i.key or '', i.name)} = {num_text(i.value)} {unit_text_ar(i.unit)} ({EVIDENCE_AR.get(i.source, i.source)})" for i in k.inputs),
                       f"{k.result:,.0f} {unit_text_ar(k.unit)}"] for k in e.calculations], [34, 42, 70, 28]),
               para("الافتراضات", st_h3),
               table([["الافتراض", "القيمة", "المصدر"]] + [[assumption_name_ar(a.key, a.name), f"{num_text(a.value)} {unit_text_ar(a.unit)}",
                                                           SOURCE_AR.get(a.source, a.source) + (" (عُدّل)" if a.user_changed else "")] for a in e.assumptions], [84, 44, 46]),
               para("الحساسية", st_h3),
               para("؛ ".join(f"{assumption_name_ar(s.input, s.input)} {s.change_percent:+.0f}% يعطي وفرًا قدره {s.new_saving:,.0f} جنيه" for s in e.sensitivity) or "—", st_body),
               para("خطوات الوكيل", st_h3)]
        st += [para(f"{i}. {x}", st_body) for i, x in enumerate((t.reasoning_steps if t else f.reasoning_steps), 1)]
    st += [Spacer(1, 8), para("كل الأرقام محسوبة بنموذج التكلفة في UtilityGuard من المعادلات والافتراضات المذكورة. الشبكة محاكاة والقيم للتوضيح، وأسعارها افتراضات تخطيط وليست تعريفة رسمية.", st_note)]
    doc.build(st)
    return buf.getvalue()
