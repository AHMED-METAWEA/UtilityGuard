# معمارية Care Grid: الـ Architecture والـ Pipeline والـ Workflow

مكتوب بتاريخ 4 نوفمبر 2026، واتحدّث 18 نوفمبر 2026 على الكود الحالي (فرعي `dev` و`main`). كل خطوة هنا مأخوذة من الكود نفسه، وجنب كل واحدة الملف اللي بينفّذها. الرسومات بصيغة Mermaid (بتتعرض تلقائيًا على GitHub وفي VS Code بإضافة Markdown Preview Mermaid).

> **القاعدة اللي المعمارية كلها مبنية عليها:** كل رقم بيطلع من الكود (معادلة باسمها وافتراضات باسمها). الـ LLM بيشرح بس، وأي رقم في كلامه مش موجود في سجل الحسابات بيتشال قبل ما يوصل الشاشة.

---

## 1. الصورة الكبيرة

```mermaid
flowchart LR
    subgraph SIM["المحاكي (seed ثابت)"]
        ENG["Engine<br/>طلب، تدفق، ضغط، حمل، حرارة<br/>+ أحداث السيناريوهات"]
        SHADOW["Shadow Engine<br/>نفس seed بدون أحداث<br/>= القيمة المتوقعة"]
    end
    subgraph CORE["الـ Backend (FastAPI)"]
        RT["Runtime<br/>tick كل 2 ثانية"]
        ML["MLService<br/>توقع الطلب + خطر الأعطال<br/>+ الإنذار المبكر"]
        DET["Detector<br/>قواعد الكشف + تجميع الحوادث"]
        COST["Cost Engine<br/>معادلات + Monte Carlo"]
        AG["Agents (LangGraph)<br/>specialists + orchestrator"]
        BRD["Board<br/>الخطة + الاجتماع + الموافقة"]
        DSP["Dispatcher<br/>تنفيذ الطواقم"]
        AUD["Audit Log<br/>hash chain"]
        OTH["Savings · Decision Studio · Siting<br/>Lab · Why · Briefing · Telegram"]
    end
    LLM["Groq<br/>gpt-oss-120b / 20b"]
    UI["Frontend<br/>React + MapLibre"]
    HLTH["integration/health<br/>(مشروع جيهان)"]

    ENG --> RT
    SHADOW --> ML
    RT --> ML --> DET --> AG
    DET --> COST
    AG --> COST
    AG <--> LLM
    AG --> BRD --> DSP
    DSP -->|"engine.repair"| ENG
    BRD --> AUD
    DSP --> AUD
    RT -->|"WebSocket: 16 نوع رسالة"| UI
    UI -->|"REST /api/v1"| CORE
    OTH --- COST
    HLTH <-->|"CSV في out/ و data/"| CORE
    UI -.->|"تبويب Healthcare (iframe)"| STR["داشبورد جيهان<br/>Streamlit :8501"]
```

| الطبقة | المكان | بتعمل إيه |
|---|---|---|
| المحاكي | `backend/app/sim/` (`engine.py`, `topology.py`) | 9 أحياء، 48 أصل، 89 حساس، 3 شبكات. طلب بمنحنى يومي، تدفق وضغط وحمل وحرارة بعلاقات سببية. نفس الـ seed يطلّع نفس التشغيلة. |
| Runtime | `app/runtime.py` | يملك الـ Engine، يعمل tick كل 2 ثانية، يبث على WebSocket، ويستدعي الـ hooks. |
| ML | `app/ml/` | LightGBM لتوقع الطلب، موديل خطر الأعطال، الإنذار المبكر. |
| الكشف | `app/detection/` | قواعد كشف + تجميع: سبب جذري واحد = حادثة واحدة. |
| التكلفة | `app/cost/` | `IncidentFacts` ← تكلفة بالجنيه + P10/P50/P90 + سجل حساب. |
| الـ Agents | `app/agents/` | specialists (مياه/كهرباء/تبريد) + orchestrator + `checker.py` (فاحص الأرقام) + `llm.py` (غلاف Groq). |
| المجلس | `app/board/` | `plan.py` (البحث عن أرخص ترتيب)، `analysis.py` (الحسابات)، `session.py` (الاجتماع)، `dispatch.py` (الموافقة والتنفيذ)، `audit.py` (السجل). |
| باقي الأدوات | `savings.py`, `decision.py`, `siting.py`, `lab/`, `why.py`, `briefing.py`, `alerts.py`, `extras.py`, `city.py` | شرحها في القسم 6. |
| الواجهة | `frontend/src/` | React 18 + TypeScript + Vite + Zustand + MapLibre. |

---

## 2. التشغيل: إيه اللي بيتبني لما السيرفر يقوم

الملف `app/main.py`، بالترتيب:

1. `runtime = Runtime()` ← بيبني الـ Engine بالـ seed (42 افتراضيًا من `SIM_SEED`).
2. `AgentService(runtime)` ← بيبني الـ `Detector` (وبيدرّب Isolation Forest داخله)، وgraph الـ specialists، وgraph الـ orchestrator، والـ `Board` (ومعاه `Dispatcher` و`AuditLog`). بيسجّل hooks على الـ runtime.
3. `MLService(runtime, detector)` ← `ml.load_or_train` بيحمّل `data/models.pkl` أو بيدرّب من الأول لو مش موجود أو قديم؛ بيقيس الكشف مرة واحدة (`ml/evaluate.py`)؛ بيبني الـ Shadow Engine؛ بيبني `EarlyWarnings`. بيدخل أول hook.
4. `TelegramAlerts`, `BriefingService`, `History` (محرك baseline بدون أحداث)، `LabCache`, `Why`, `CityData`.
5. `lifespan`: `runtime.start()` (بيشغّل الـ loop)، وتسخين الـ AI lab في thread منفصل، وحلقة الـ briefing اليومية.
6. الـ backend بيخدم الواجهة المبنية كملفات static، فبورت واحد (8000) يكفي.

**ترتيب الـ tick hooks** (ده الترتيب الفعلي اللي بيتنفذ كل tick): `MLService.tick` ← `Detector.tick` ← `_sync_llm_mode` ← `Dispatcher.tick` ← `TelegramAlerts.tick`.

---

## 3. الـ Pipeline الرئيسي: كل tick (كل 2 ثانية)

```mermaid
sequenceDiagram
    participant L as Runtime loop
    participant E as Engine
    participant UI as المتصفح (WS)
    participant M as MLService
    participant D as Detector
    participant X as Dispatcher
    participant T as Telegram
    L->>E: step(ts)
    E-->>L: قيم الحساسات (مع الأعطال: stuck/drift/dropout)
    L->>L: Reading لكل حساس + history (آخر 900)
    L->>UI: reading (دفعة 89 قراءة)
    L->>L: _update_assets (normal/warning/fault/offline + health)
    L->>UI: asset_status (عند التغيير)
    L->>M: hook 1
    M->>M: Shadow step = القيمة المتوقعة (rt.forecasts)
    M->>M: كل 10 ticks: خطر الأعطال + الإنذار المبكر
    M->>UI: early_warning
    M->>UI: كل 150 tick: forecast_update
    L->>D: hook 2
    D->>D: detect() بالقواعد → confirm 3 ticks → alert/incident
    D->>UI: alert, incident
    D-->>L: on_incident → AgentService.analyse (task في الخلفية)
    L->>X: hook 4: تقدم الطواقم، تأكيد الحل
    L->>T: hook 5: رسائل Telegram عند فتح/تصعيد/حل مشكلة
```

تفاصيل كل خطوة:

| الخطوة | الكود | التفصيل |
|---|---|---|
| توليد القراءات | `Engine.step` | يرجّع الحقيقة الفيزيائية والقراءة (القراءة بتختلف عن الحقيقة لو الحساس stuck/drift/dropout). |
| حالة الأصل | `Runtime._update_assets` | الانحراف = بُعد القراءة عن المدى الطبيعي ÷ عرض المدى. أكتر من 0.5 = fault، أكتر من 0.05 = warning، الأصل المفصول = offline. health = حالة أساسية − 50 × الانحراف. |
| القيمة المتوقعة | `MLService.tick` | محرك ظل بنفس الـ seed ونفس الساعة ومن غير أي حدث. الفرق بين القراءة الفعلية وقيمته هو "الانحراف عن التوأم". |
| خطر الأعطال | `MLService._failure` (كل 10 ticks) | 6 مدخلات لكل مضخة/محول/ماسورة/محطة ← درجة خطر ← `EarlyWarnings.update`. |
| الإنذار المبكر | `ml/earlywarn.py` | الدرجة تتحسب مرتين (بقيم الأصل وبقيمة وسيط نظرائه). الفرق بمقياس log-odds ≥ 1.0 لمدة 3 قراءات والأصل لسه شغّال = إنذار. بعدها: أسباب (drivers)، تسعير الصيانة، وأرقام القياس (evidence). |
| الكشف | `Detector.detect` | قواعد بعتبات (فصل محطة، كسر ماسورة، تسريب خفي، عطل مضخة، عطل تبريد، حمل زائد، حساس عاطل). |
| التأكيد والتجميع | `Detector.tick` | الحالة لازم تستمر 3 ticks. التنبيه الجديد بيتربط بحادثة موجودة لو جذره داخل نطاق تأثيرها (blast radius) أو ليه نفس السبب الخارجي (مثلا حرارة). بيتقفل بعد 6 ticks من غير الحالة. |

---

## 4. الـ Workflow: من العطل لحد الحل

```mermaid
flowchart TD
    A["سيناريو أو عطل في الـ Engine"] --> B["Detector يفتح Alert ثم Incident"]
    B --> C["AgentService.analyse<br/>ينتظر 6 ثواني تستقر التقديرات"]
    C --> D["Specialist graph:<br/>gather → costing → reason → verify → finalize"]
    D --> E["AgentFinding على الـ WS<br/>+ calculation record"]
    E --> F{"مشكلتين أو أكتر<br/>في شبكتين مختلفتين؟"}
    F -- لا --> G["الشخص يقرأ الـ finding<br/>ويقرر / Decision Studio"]
    F -- نعم --> H["Board.maybe_convene<br/>(ينتظر استقرار المجموعة)"]
    H --> I["analysis.py: أرخص ترتيب<br/>بالطواقم العامة وطواقم الشبكات<br/>+ 300 سيناريو سعر"]
    I --> J["الاجتماع: الجولات 0 لـ 4<br/>+ ملخص للمسؤول"]
    J --> K["BoardDecision: proposed"]
    K --> L{"الشخص"}
    L -- approve --> M["Dispatcher: تنفيذ"]
    L -- "تغيير الترتيب (يتسعّر الأول)" --> M
    L -- reject --> N["مرفوض + سبب في السجل"]
    M --> O["الطاقم يخلّص ← engine.repair"]
    O --> P["Detector هو اللي يؤكد إن العطل راح<br/>(مش الطاقم)"]
    P --> Q["resolved + سجل التنفيذ"]
    K -.-> R[("Audit log")]
    L -.-> R
    M -.-> R
    Q -.-> R
```

### 4.1 من العطل لـ Finding (`agents/service.py` و`agents/specialists.py`)

| العقدة | بتعمل إيه | LLM؟ |
|---|---|---|
| `gather` | تقرا الحساسات المتأثرة ونطاق التأثير (`tools.read_sensors`, `tools.impacted`) | لا |
| `costing` | `cost.compute(facts, assumptions)` ← `CalculationRecord` | لا |
| `reason` | الموديل (20b) يكتب الشرح والخطوات والأسباب المعتادة بالعربي والإنجليزي كـ JSON، والأرقام بتتكتب على شكل token (`{{act_now}}`) | نعم، نص فقط |
| `verify` | `checker.check`: أي رقم مش في سجل الحساب يتشال؛ الشاشة تعرض `numberCheck` (passed/corrected) | لا |
| `finalize` | يبني `AgentFinding`: العنوان، الأثر، الإجراء، التكلفة، "ليه" | لا |

لو الـ LLM وقع أو اتأخر: كاش `data/fallback_cache.json` (مسجّل من تشغيل حي)، وإلا قالب من الكود. الشريط اللي فوق بيقول `Agents: live` أو `recorded`.

**تكلفة مكتوبة بالأرقام (اتضاف 12 نوفمبر):** لو الموديل كتب تكلفة بنفسه بدل الرمز، بتتشال حتى لو صح، والإجابة بترجع لصياغة الكود (`figures.typed_money`)، و`numberCheck` بيبقى `corrected`. والفاحص بقى بيراجع كمان الأرقام الصغيرة اللي بعدها ساعات أو دقايق.

### 4.1ب نظام الـ agents كله

| الـ agent | الموديل | شغلته |
|---|---|---|
| المنسّق (orchestrator) | gpt-oss-120b | في الشات بيختار الوكلاء ويجمّع ردودهم، وفي المجلس رئيس الجلسة |
| وكيل المياه | gpt-oss-20b | المواسير والمضخات والخزانات |
| وكيل الكهرباء | gpt-oss-20b | المحطات والأحمال |
| وكيل التبريد | gpt-oss-20b | محطات التبريد والـ chillers |

```mermaid
flowchart TB
    subgraph S1["1. حادثة جديدة"]
        D1["Detector"] --> SP["وكيل الشبكة (20b)<br/>gather → costing → reason → verify → finalize"] --> F1["Finding + Telegram"]
    end
    subgraph S2["2. سؤال في الشات"]
        R2["المنسّق: router (120b)"] --> WA["مياه (20b)"] & EL["كهرباء (20b)"] & CO["تبريد (20b)"]
        WA & EL & CO --> SY["المنسّق: synthesize (120b)"] --> CK["checker"]
    end
    subgraph S3["3. اجتماع المجلس"]
        AN["analysis.py (كود)"] --> DR["جمل كتبها الكود"] --> VO["الرئيس 120b + الوكلاء 20b<br/>إعادة صياغة بس"] --> SF["نفس الأرقام؟<br/>لأ = جملة الكود"]
    end
```

الـ agents ما بيبعتوش رسايل لبعض بحرية. كل وكيل بيشتغل على بيانات الكود بيدّيهاله، والأرقام في المواقف التلاتة من الكود.

### 4.2 المجلس (`board/`)

1. **التشغيل:** تلقائي (`BOARD_AUTO=1`) لما يبقى فيه مشكلتين على الأقل في شبكتين، بعد ما المجموعة تستقر، أو يدوي `POST /board/convene`.
2. **الحسابات (`plan.py`, `analysis.py`):** كل مشكلة: تكلفة الساعة `C_i` ووقت الإصلاح `h_i`. بيجرّب **كل الترتيبات** (حتى 6 مشاكل = 720 ترتيب) ويختار الأرخص، ويقارنه بترتيب الإنذارات وبأسوأ ترتيب على 300 سيناريو سعر. مفيش LLM.
   - **الطواقم (اتضاف 12 نوفمبر):** طواقم عامة تشتغل على أي شبكة (`crews_available`، الافتراضي 2) وطواقم لكل شبكة (`crews_water` و`crews_electricity` و`crews_cooling`، الافتراضي 0). طاقم الشبكة ما بياخدش غير شغل شبكته، وأي توزيع يسيب شبكة من غير طاقم بيترفض. و"طاقم زيادة يوفّر كام" بيقول نوع الطاقم.
   - **لكل وكيل قبل الجلسة:** لو بدأ الأول الباقيين يدفعوا كام (`others_pay`)، ولو استنى دوره هو يدفع كام (`waiting_price`)، ونقطة القلب (`flips`).
3. **الاجتماع (`session.py`):** الجولات: 0 الرئيس يفتح، 1 المواقف، 2 تكلفة الانتظار، 3 الرئيس يقرر، 4 الردود. الجمل مكتوبة من الكود، والـ LLM ممكن يعيد صياغتها، ولو غيّر رقم أو أصل تترفض ويتعرض نص الكود (`source` = llm أو template).
4. **الموافقة (`dispatch.py`):** `approve` / `reject` / `preview` لأي ترتيب بديل. الأوامر البديلة بتتسعّر قبل ما تتقبل، والفرق بيتسجل (`approve_with_changes`).
5. **التنفيذ:** ساعة العمل المخططة = 20 ثانية على ساعة العرض (`DISPATCH_SECONDS_PER_HOUR`). لما الطاقم يخلص: `engine.repair`. بعدها الـ Detector هو اللي يقفل التنبيه ويتسجل `confirmed_at`.
6. **السجل (`audit.py`):** كل سطر فيه SHA-256 للسطر اللي قبله؛ أي تعديل بيكسر السلسلة و`GET /audit/verify` يقول فين. بيعيش بعد reset، ومكتوب في `data/audit.jsonl`.

### 4.2ب الاجتماع جولة جولة (`board/session.py`)

```mermaid
sequenceDiagram
    participant A as analysis.py (كود)
    participant C as الرئيس (120b)
    participant W as وكيل المياه (20b)
    participant E as وكيل الكهرباء (20b)
    participant K as وكيل التبريد (20b)
    participant P as الإنسان
    A->>A: أرخص ترتيب + 300 سيناريو + مصلحة كل وكيل بالأرقام
    Note over C,K: كل جملة الكود بيكتبها، والموديل يعيد الصياغة بس
    Note over C,K: جولة 0 - الرئيس: كام مشكلة وكام طاقم وتكلفة الساعة
    W->>C: جولة 1: مشكلتي وتكلفتها، ولو بدأت الأول الباقيين يدفعوا كام
    E->>C: جولة 1: نفس الشكل
    K->>C: جولة 1: أو "معداتي وقفت بسبب المحطة، مش بطلب طاقم"
    W->>C: جولة 2: لو استنيت دوري هدفع كام زيادة
    Note over C,K: جولة 3 - الرئيس: الترتيب والطواقم والتكلفة ونسبة الفوز في 300 سيناريو
    E->>C: جولة 4: موافق، وأبدأ الساعة كام، ورجّعوني لو تكلفتي عدّت حد معين
    C->>P: جولة 5: اعمل كذا وليه، والحالة الوحشة P90، وطاقم زيادة يوفّر كام
    Note over C,P: BoardDecision = proposed، وسطر في سجل القرارات
    P->>C: موافقة أو تغيير الترتيب (يتسعّر الأول) أو رفض بسبب
```

- **إعادة الصياغة بتتقبل بس لو:** نفس الأرقام بالظبط (مفيش زيادة ولا نقص)، وكل رقم في قايمة الأرقام، ومفيش أصل مش مسموح، والطول قريب من جملة الكود. غير كده جملة الكود هي اللي بتظهر، وكل رسالة عليها `source` = `llm` أو `template`.
- **بالعربي** الافتراضي جملة الكود، لأن الموديل غلط في النحو بصيغة المتكلم. `BOARD_AR_LLM=1` يشغّل إعادة الصياغة بنفس الفحص.
- **بعد الجلسة:** اقتراح قديم محدش قرر فيه بيبقى `superseded`، واقتراح لمشاكل اتبعتلها طواقم بالفعل بيبقى `stale`.

### 4.3 الشات (`agents/orchestrator.py`)

```mermaid
flowchart LR
    Q["سؤال المستخدم + اللغة"] --> R["router (120b)<br/>الـ prompt فيه ربط المحطات بالمضخات والتبريد"]
    R -->|"رد صالح"| FL["أرضية الشبكة<br/>أي وكيل الأصل بيأثر عليه يتضاف"]
    R -->|"مفيش رد"| RR["router بالقواعد<br/>كلمات عربي وإنجليزي"]
    FL --> S["simulate<br/>what_if على نسخة sandbox من التوأم"]
    RR --> S
    S --> C["consult (20b)<br/>رأي كل وكيل"]
    C --> Y["synthesize (120b)<br/>الإجابة النهائية"]
    Y --> V["checker: الأرقام والأصول<br/>+ حذف التكاليف المكتوبة بالأرقام"]
    V --> W["chat_route · chat_chunk · chat_done على الـ WS"]
```

السؤال زي "لو استنينا 12 ساعة؟" بيتحسب في **نسخة sandbox** (`Runtime.clone()`) بنفس الـ detector ونفس محرك التكلفة. الشات ما بيغيّرش التوأم الحي.

**الـ routing (اتحسّن 12 نوفمبر):** الموديل ممكن يزوّد وكيل، بس عمره ما ينقص وكيل الشبكة بتقول إنه متأثر (`topology_route` + `with_floor`). ولو الموديل ما ردش، `rule_route` بالكلمات المفتاحية. القياس على 54 سؤال (`docs/measurements/routing.md`): الموديل قبل 35 من 54، واللي شغال دلوقتي 47 من 54، صفر نسيان و7 زيادة.

---

## 5. التكلفة والافتراضات (`app/cost/`)

```
C_delay     = C_loss + C_disruption + C_secondary             (جنيه/ساعة)
ActNow      = C_repair_now + C_delay × t_response
CostOfDelay = C_delay × t_delay + C_repair_now × late_multiplier
Saving      = CostOfDelay − ActNow
```

- المدخلات: `IncidentFacts` (النوع، الأحياء، الميجاوات غير المخدومة، المضخات الواقفة…) من الـ Detector.
- الافتراضات: `data/assumptions.json`، قابلة للتعديل من الشاشة (`PUT /assumptions`)، لكل واحدة أساس ومدى ومرجع. التعديل بيعمل `recompute` للحوادث المفتوحة ويبعت الـ findings تاني، وباقي الأرقام (التوفير، الإنذار المبكر، المجلس) بتتحسب من الافتراضات الجديدة في أول مرة تتعرض بعدها.
- المخرجات: `CalculationRecord` (معادلة + مدخلات + مصدر كل مدخل)، ونطاق Monte Carlo (800 محاولة)، ورسم tornado. `GET /calculations/{id}` يرجّع السجل.

---

## 6. باقي الأجزاء وأين تتصل

| الجزء | الملف | بيتصل بإيه | بيقدم إيه |
|---|---|---|---|
| Decision Studio | `decision.py` | الـ finding + التكلفة | "نصلّح دلوقتي ولا نستنى 6/24/72 ساعة" مع الندم ونقطة التعادل |
| Savings | `savings.py` + `dashboard.History` | محرك baseline 30 يوم + التكلفة + خطر الأعطال | فرص توفير شهرية بنطاق P10/P50/P90 |
| Forecast | `ml/` + `main.py /forecast*` | موديلات LightGBM | توقع 24–72 ساعة + نطاق + أدلة الدقة + حكم "reliable/indicative/unreliable" |
| AI lab | `lab/` | محرك حقيقي بأعطال محقونة | مقارنة 4 كواشف، تحديد الماسورة، جودة الحساسات، "جرّب تسريب بنفسك" |
| Siting ("ابني هنا") | `siting.py` | نسخة من Engine بحمل إضافي | احتمال المشاكل وتكلفتها لو اتبنى مستشفى/عيادة في حي |
| Why | `why.py` | كل المصادر | "ليه الرقم ده؟": المعادلة والمدخلات ومصدر كل مدخل |
| Guardrail | `agents/guardrail.py` | LLM + الـ checker | "جرّب تخدع الـ AI": كل رقم مبالغ فيه بيتشال |
| Briefing | `briefing.py` | KPIs + المجلس + الإنذارات + التوقع | ملخص الصبح بالعربي أو الإنجليزي، 100% من الكود، يتبعت 07:00 |
| Telegram | `alerts.py` | `tick` hook | رسالة بكل التفاصيل عند فتح/تصعيد/حل مشكلة، وعند الإنذار المبكر |
| تقرير الحادثة | `extras.py` + `report_ar.py` | الـ finding | PDF بالإنجليزي أو العربي (RTL، خط Amiri) |
| مقارنة بتدخل/بدون | `extras.compare` | sandbox | الحادثة بتتحل فعلًا مقابل ما تتساب |
| لغة الـ agents | `i18n.py` + `GET/PUT /settings` | الـ specialists والشات والمجلس والتقارير | EN أو عربي، محفوظة في `data/settings.json` |

---

## 7. الواجهة (`frontend/src/`)

```mermaid
flowchart LR
    WS["WebSocket /ws/stream"] --> P["api/ws.ts<br/>parseEnvelope: يتحقق من شكل كل رسالة"]
    P --> ST["store (Zustand)<br/>readings, assets, alerts, incidents,<br/>findings, warnings, board, chat"]
    REST["REST /api/v1<br/>api/client.ts"] --> PG
    ST --> PG["الصفحات (hash routing)"]
    PG --> MAP["map/ MapLibre"]
    PG --> CH["charts/ SVG"]
    PG --> PN["panels/: ChatDock · ScenarioLab · WhyDrawer · Palette"]
```

- **9 صفحات** (`lib/route.ts`): `live`, `incidents`, `savings`, `forecast`, `lab`, `executive`, `siting`, `healthcare`, `audit`.
- **16 نوع رسالة WebSocket**: `snapshot, reading, asset_status, alert, incident, agent_finding, scenario_state, forecast_update, chat_route, chat_chunk, chat_done, board_state, board_message, board_decision, audit_entry, early_warning`. أول ما الاتصال يفتح، السيرفر يبعت `snapshot` كامل ثم التحديثات.
- أي رسالة شكلها غلط بتتهمل (مش بتكسر الشاشة). الحقول الجديدة المضافة (additive) ما بتبوّظش الرسائل القديمة.
- **لغتين منفصلتين:** لغة الواجهة نفسها (زرار الكرة الأرضية)، ولغة الـ agents (زرار EN | عربي، محفوظة في السيرفر).

---

## 8. الربط مع مشروع جيهان (`integration/health/`)

```mermaid
flowchart LR
    G["their-app<br/>(مشروع جيهان، git-ignored، قراءة فقط)"] -->|"extract_environment.py<br/>extract_packages.py"| DATA["integration/health/data/<br/>بيئة شهرية، أحياء، منشآت، باقاتها"]
    DATA --> CITY["app/city.py<br/>(بيقرا ويربط بـ district_key)"]
    CITY --> API["/city/districts · /city/decision · /city/facilities"]
    API --> UI2["صفحة Healthcare + طبقات الخريطة + الجدول المشترك في Executive"]
    TW["التوأم: Engine + Detector + Cost"] -->|"export_for_health.py<br/>(sandbox لكل أصل)"| OUT["integration/health/out/*.csv"]
    OUT --> CITY
    OUT --> G
```

- **اتجاهين من غير تعديل المشروعين:** بياناتها بتتقرأ read-only، ونتايج التوأم بتتكتب CSV في `out/` (ومعاها `MANIFEST.json` بـ SHA-256 لكل ملف).
- `export_for_health.py` بيشغّل نفس "لو الأصل ده وقع" اللي بيستخدمه الـ agents: sandbox + الـ detector الحقيقي + محرك التكلفة بالافتراضات الافتراضية. مفيش موديل جديد. المخرجات: `scenarios.csv`, `district_risk.csv`, `hospital_exposure.csv`, `joint_decision.csv`, `reliability_packages.csv`...
- مطابقة الأحياء: 4 من 15 فقط نفس الحي (`direct`)، والباقي بالنوع أو بدون مقابل (`zone_mapping.csv`).
- ما بنشغّلش موديلات التنبؤ بتاعتها (Ridge/LightGBM) جوه نظامنا؛ بنستخدم بياناتها ومخرجات محرك السيناريوهات بتاعها.
- `run_all.sh` بيعمل clone لمشروعها في `integration/health/their-app` (git-ignored) ببيئة لوحده، ويشغّله على 8501، وداشبوردها بيظهر جوه تبويب Healthcare (iframe).

---

## 9. أين تتخزن البيانات

| الملف (`backend/data/`) | فيه إيه | بيتغير امتى |
|---|---|---|
| `models.pkl` | موديلات LightGBM وخطر الأعطال وأدلة الدقة | عند أول تشغيل، ويتجدد يوميًا لو قديم |
| `assumptions.json` | افتراضات التكلفة المسجلة | عند تعديلها من الشاشة |
| `fallback_cache.json` | إجابات LLM مسجلة تُعاد وقت الـ offline | عند كل إجابة حية ناجحة |
| `lab_benchmark.json` | نتائج مقارنة الكواشف | عند أول تسخين للـ lab |
| `audit.jsonl` | سجل القرارات (hash chain) | سطر لكل حدث، ولا يُمسح بالـ reset |
| `settings.json` | لغة الـ agents | عند تغييرها (git-ignored) |
| `briefing_state.json` | آخر ملخصات اتبعتت | عند كل briefing |
| `earlywarn_evidence.json` | أرقام قياس الإنذار المبكر (اختياري) | عند `earlywarn_matrix.py --write-evidence` |

الذاكرة الحية (تتصفّر مع `POST /scenarios/reset`): readings وhistory وalerts وincidents وfindings وwarnings وجلسات المجلس.

---

## 10. الاختبار والقياس

| ماذا | أين |
|---|---|
| اختبارات الـ backend (414) | `backend/tests/` (`PYTHONPATH=. .venv/bin/python -m pytest -q`، حوالي 9 دقايق) |
| اختبارات الـ frontend (84) + typecheck + build | `frontend/` |
| الـ fixtures المشتركة (يتحقق منها الـ backend ويقراها الـ frontend) | `fixtures/` (`scripts/make_fixtures.py`) |
| فحص العرض end-to-end | `backend/scripts/demo_check.py` |
| مقاييس الكشف والتوقع والإنذار المبكر | `backend/scripts/stress/` ونتايجها في `docs/measurements/` |
| قياس الـ routing | `app/agents/routing_eval.py --live` على `tests/data/routing_eval.json` (54 سؤال)، والنتيجة في `docs/measurements/routing.md` |

---

## 11. الحدود (قولها بنفسك قبل ما تتسأل)

- كل البيانات من المحاكي، والأسعار افتراضات تخطيط بأساس ومدى. مفيش بيانات ACUD حقيقية.
- موديل خطر الأعطال متدرّب على معادلة خطر مكتوبة بالكود، فهو ترتيب مش احتمال عطل في وقت محدد.
- الكشف قواعد مش تعلّم آلة؛ أضعف نقطة فيه التسريب الخفي الأقل من حوالي 24 م³/س.
- الـ Isolation Forest داخل الـ Detector بيتحسب كل tick ومفيش حاجة بتقراه؛ هو شغّال فعلًا في الـ AI lab (D3) فقط.
- المستشفى في "ابني هنا" بيتعامل كحمل ينفصل وقت فصل المحطة، واستهلاكه للكهرباء من مراجع غير مصرية.
- الأرقام الخاصة بالمجلس بالعربي مكتوبة من قوالب الكود (`BOARD_AR_LLM=1` يشغّل إعادة الصياغة بالـ LLM).
- وقت التنقل بين الأعطال في المجلس ثابت (نص ساعة)، مش محسوب من الخريطة.
- قياس الـ routing على 54 سؤال كتبهم شخص واحد.
