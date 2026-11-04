"""The board meeting in Arabic: the same analysis, the same figures and the same order of speakers as `session.compose`, with the sentences written in Arabic.

Each sentence is the code's own wording; a language model may re-voice it (see `session.voice`) only if every figure and every asset code survives. So the Arabic of
the meeting is held to the rule of the English one: a figure on screen is a computed figure, and `source` says which of the two wrote the words.
"""

from __future__ import annotations

from app.agents.tools import PLAYBOOK_ACTION_AR
from app.board.analysis import AGENTS, Analysis, hours, money
from app.i18n import asset_ar

NAMES_AR = {"water": "وكيل المياه", "electricity": "وكيل الكهرباء", "cooling": "وكيل التبريد", "orchestrator": "المنسّق"}
ORD_AR = ["أولًا", "ثانيًا", "ثالثًا", "رابعًا", "خامسًا", "سادسًا"]
PLURAL_AR = {"pumps": "المضخات", "cooling plants": "محطات التبريد"}


def _crew(slot) -> str:
    return chr(65 + int(slot))  # crews are named A, B, C so that no stray digits appear in the wording


def _act(t) -> str:
    return PLAYBOOK_ACTION_AR.get(t.kind, t.action)


def compose_ar(a: Analysis, fact, Draft) -> list:
    """`fact` and `Draft` are those of `session` (passed in so that this module does not import it)."""
    T, p = a.tasks, a.params
    names = {t.asset_name for t in T}
    drafts: list = []
    plan, steps = a.plan, sorted(a.plan.order, key=lambda i: (a.plan.start[i], i))
    owners = [g for g in AGENTS if a.agent_tasks(g)]
    bleed = float(p.rate.sum())

    facts = [fact("المشكلات المفتوحة", len(T), "مشكلة"), fact("الشبكات المعنية", len(owners), "شبكة"), fact("الطواقم المتاحة", p.crews, "طاقم", "assumed"),
             fact("التكلفة في الساعة والكل مفتوح", bleed, "جنيه/ساعة")]
    order = "عدد الطواقم أقل من عدد المشكلات، فالترتيب مهم." if len(T) > p.crews else "الطواقم تكفي لبدء كل شيء معًا."
    tmpl = (f"المجلس منعقد. عدد المشكلات المفتوحة: {len(T)}، موزعة على {len(owners)} من الشبكات، وعدد الطواقم المتاحة: {p.crews}. "
            f"إن تُركت مفتوحة فهي تكلّف {money(bleed)} جنيه عن كل ساعة. {order}")
    drafts.append(Draft(0, "orchestrator", "opening", facts, tmpl, "", names, True, "ar"))

    for g in owners:
        i = a.primary(g)
        t = T[i]
        mine = a.agent_tasks(g)
        own = a.own_first[g]
        facts = [fact(f"تكلفة {t.asset_name} في الساعة وهي مفتوحة", p.rate[i], "جنيه/ساعة"), fact("وقت الطاقم اللازم", p.hours[i], "ساعة", "assumed"),
                 fact("العائد لكل ساعة طاقم", p.rate[i] / p.hours[i], "جنيه/ساعة"), fact("تنتهي عند الساعة لو بدأتُ أولًا", own.end[i], "ساعة")]
        extra = ""
        if len(mine) > 1:
            facts.append(fact("مشكلاتي الأخرى المفتوحة", len(mine) - 1, "مشكلة"))
            extra = f" ولديّ مشكلات أخرى مفتوحة بعدها (العدد: {len(mine) - 1})."
        pay = a.others_pay.get(g, 0.0)
        pay_txt = ""
        if a.conflict and pay > 0.5:
            facts.append(fact("تكلفة إضافية على الآخرين لو بدأتُ أولًا", pay, "جنيه"))
            pay_txt = f" لو بدأتُ أولًا فسيدفع الآخرون مجتمعين {money(pay)} جنيه زيادة."
        tmpl = (f"{NAMES_AR[g]}. {asset_ar(t.asset_name)}: {_act(t)}. إن تُركت مفتوحة فهي تكلّف {money(p.rate[i])} جنيه عن كل ساعة وتحتاج {hours(p.hours[i])} ساعة من وقت الطاقم، "
                f"فكل ساعة طاقم تستردّ {money(p.rate[i] / p.hours[i])} جنيه في الساعة. لو بدأ عملي أولًا فسينتهي عند الساعة {hours(own.end[i])}.{extra}{pay_txt}")
        drafts.append(Draft(1, g, "position", facts, tmpl, "", names | {t.asset_name}, False, "ar"))

    trips = [t for t in T if t.kind == "substation_trip"]
    for g, label, attr in (("water", "pumps", "pumps_stopped"), ("cooling", "cooling plants", "plants_affected")):
        if g in owners:
            continue
        hit = [(t, t.stake[attr]) for t in trips if t.stake.get(attr)]
        if hit:
            t, count = max(hit, key=lambda x: x[1])
            facts = [fact(f"{PLURAL_AR[label]} التي توقفت بسبب {t.asset_name}", count, "وحدة")]
            tmpl = (f"{NAMES_AR[g]}. ليست لديّ إصلاحات خاصة، لكن {PLURAL_AR[label]} التي توقفت عندما فقدت {asset_ar(t.asset_name)} الكهرباء عددها {int(count)}. "
                    f"تكلفتها محسوبة أصلًا ضمن ضرر انقطاع الكهرباء، فلا أطلب طاقمًا. عودة {asset_ar(t.asset_name)} تعيدها.")
            drafts.append(Draft(1, g, "dependent", facts, tmpl, "", names | {t.asset_name}, False, "ar"))

    if a.conflict:
        for g in owners:
            price = a.waiting_price.get(g, 0.0)
            i = a.primary(g)
            waits = plan.start[i] - p.response > 1e-9
            if waits and price > 0.5:
                facts = [fact("تكلفة إضافية عليّ لو انتظرتُ دوري", price, "جنيه"), fact(f"يبدأ {T[i].asset_name} عند الساعة", plan.start[i], "ساعة")]
                tmpl = f"{NAMES_AR[g]}. لو انتظرتُ دوري فسيكلّف عملي {money(price)} جنيه أكثر مما لو بدأتُ أولًا. وسيبدأ عند الساعة {hours(plan.start[i])}."
                drafts.append(Draft(2, g, "challenge", facts, tmpl, "", names | {T[i].asset_name}, False, "ar"))
            elif not waits:
                facts = [fact(f"يبدأ {T[i].asset_name} عند الساعة", plan.start[i], "ساعة")]
                tmpl = f"{NAMES_AR[g]}. يمكن أن يبدأ عملي عند الساعة {hours(plan.start[i])} بطاقم خاص به، فالترتيب لا يكلّفني شيئًا."
                drafts.append(Draft(2, g, "challenge", facts, tmpl, "", names | {T[i].asset_name}, False, "ar"))

    alarm = next(x for x in a.alternatives if x["id"] == "alarm")
    order_txt = "؛ ".join(f"{ORD_AR[k]}: {asset_ar(T[i].asset_name)} على الطاقم {_crew(plan.slot[i])} من الساعة {hours(plan.start[i])} إلى الساعة {hours(plan.end[i])}"
                          for k, i in enumerate(steps))
    facts = [fact("تكلفة الترتيب المختار", plan.cost, "جنيه"), fact("الوفر مقارنةً بترتيب وصول الإنذارات", alarm["extra"], "جنيه"),
             fact("الأرخص في هذه النسبة من سيناريوهات الأسعار", a.robust.win_share * 100, "%"), fact("سيناريوهات الأسعار التي جُرّبت", a.robust.runs, "سيناريو")]
    for k, i in enumerate(steps):
        facts += [fact(f"الخطوة {k + 1}: يبدأ {T[i].asset_name}", plan.start[i], "ساعة"), fact(f"الخطوة {k + 1}: ينتهي {T[i].asset_name}", plan.end[i], "ساعة")]
    saved = f" وهذا أقل بمقدار {money(alarm['extra'])} جنيه من العمل بترتيب وصول الإنذارات." if alarm["extra"] > 0.5 else ""
    proof = (f" جرّبتُ كل الترتيبات، وهذا هو الأرخص في {round(a.robust.win_share * 100)}% من {a.robust.runs} سيناريو أسعار."
             if len(T) > 1 else " توجد مهمة واحدة، فلا ترتيب نختاره.")
    tmpl = f"القرار. {order_txt}. التكلفة المتوقعة {money(plan.cost)} جنيه.{saved}{proof}"
    drafts.append(Draft(3, "orchestrator", "decision", facts, tmpl, "", names, True, "ar"))

    for g in owners:
        mine = a.agent_tasks(g)
        i = min(mine, key=lambda x: plan.start[x])
        t = T[i]
        wait = plan.start[i] - p.response
        fl = next((f for f in a.flips if f["task"].id == t.id), None)
        if wait > 1e-9:
            facts = [fact(f"يبدأ {t.asset_name} عند الساعة", plan.start[i], "ساعة")]
            tail = ""
            if fl and fl["direction"] == "above":
                facts += [fact("تكلفتي في الساعة الآن", fl["current"], "جنيه/ساعة"), fact("التكلفة في الساعة التي أبدأ عندها أولًا", fl["threshold"], "جنيه/ساعة")]
                tail = f" ناديني إن ارتفعت تكلفتي فوق {money(fl['threshold'])} جنيه في الساعة؛ وهي الآن {money(fl['current'])}."
            tmpl = f"{NAMES_AR[g]}. موافق. يبدأ عملي عند الساعة {hours(plan.start[i])}.{tail}"
            drafts.append(Draft(4, g, "concession", facts, tmpl, "", names | {t.asset_name}, False, "ar"))
        else:
            facts = [fact(f"ينتهي {t.asset_name} عند الساعة", plan.end[i], "ساعة")]
            tmpl = f"{NAMES_AR[g]}. مفهوم. يبدأ طاقمي فورًا وأسلّمه عند الساعة {hours(plan.end[i])}."
            drafts.append(Draft(4, g, "concession", facts, tmpl, "", names | {t.asset_name}, False, "ar"))
    return drafts


def summary_draft_ar(a: Analysis, fact, Draft):
    T, p, plan = a.tasks, a.params, a.plan
    steps = sorted(plan.order, key=lambda i: (plan.start[i], i))
    alarm = next(x for x in a.alternatives if x["id"] == "alarm")
    names = {t.asset_name for t in T}
    lines = [f"{ORD_AR[k]}: {asset_ar(T[i].asset_name)}، {_act(T[i])}، الطاقم {_crew(plan.slot[i])}، من الساعة {hours(plan.start[i])} إلى الساعة {hours(plan.end[i])}" for k, i in enumerate(steps)]
    facts = [fact("تكلفة الخطة", plan.cost, "جنيه"), fact("الوفر مقارنةً بترتيب الإنذارات", alarm["extra"], "جنيه"), fact("تكلفة الساعة لكل المشكلات المفتوحة", float(p.rate.sum()), "جنيه/ساعة"),
             fact("التكلفة في حالة سيئة (واحدة من كل عشر)", a.robust.p90, "جنيه")]
    for k, i in enumerate(steps):
        facts += [fact(f"الخطوة {ORD_AR[k]}: يبدأ {T[i].asset_name}", plan.start[i], "ساعة"), fact(f"الخطوة {ORD_AR[k]}: ينتهي {T[i].asset_name}", plan.end[i], "ساعة")]
    first = T[steps[0]]
    why = f"{asset_ar(first.asset_name)} تكلّف {money(first.rate)} جنيه عن كل ساعة تبقى مفتوحة."
    facts.append(fact(f"تكلفة {first.asset_name} في الساعة", first.rate, "جنيه/ساعة"))
    risk = ""
    if a.flips:
        f = a.flips[0]
        risk = f" سيتغير الترتيب لو كانت تكلفة {asset_ar(f['task'].asset_name)} في الساعة {'أقل من' if f['direction'] == 'below' else 'أعلى من'} {money(f['threshold'])} جنيه."
        facts += [fact(f"مستوى تغيّر الترتيب لـ{f['task'].asset_name}", f["threshold"], "جنيه/ساعة"), fact(f"تكلفة {f['task'].asset_name} في الساعة الآن", f["current"], "جنيه/ساعة")]
    crew = ""
    if a.extra_crew_saving:
        crew = f" طاقم إضافي واحد سيوفّر {money(a.extra_crew_saving)} جنيه."
        facts.append(fact("الوفر من طاقم إضافي", a.extra_crew_saving, "جنيه"))
    saved = f" يوفّر {money(alarm['extra'])} جنيه مقارنةً بالعمل بترتيب وصول الإنذارات." if alarm["extra"] > 0.5 else ""
    tmpl = ("افعل التالي. " + "؛ ".join(lines) + f". السبب: {why} التكلفة المتوقعة للخطة {money(plan.cost)} جنيه.{saved} "
            f"في الحالة السيئة تكلّف {money(a.robust.p90)} جنيه.{risk}{crew}")
    return Draft(5, "orchestrator", "decision", facts, tmpl, "", names, True, "ar")
