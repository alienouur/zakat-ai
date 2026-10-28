# مساعد صندوق الزكاة (شات بوت اللائحة)

شات بوت ويب يجيب المواطنين عن أسئلة الزكاة وإجراءات صندوق الزكاة اعتمادًا على «لائحة صندوق الزكاة الليبي رقم (1) لسنة 2026م» (مجموعة `libya_zakat_fund` في المجلد `لائحة_الزكاة/`)، ويحوّل الشكاوى والاستفسارات الخاصة إلى بريد الصندوق عبر أداة `send_escalation_email`.

## التشغيل

```bash
pip install -r requirements.txt
cp .env.example .env            # واملأ GEMINI_API_KEY وإعدادات SMTP
mkdir -p config                 # ضع فيه system_prompt.md و escalation_tool.json (لا يُرفعان إلى GitHub)
uvicorn chatbot.server:app --host 0.0.0.0 --port 8090
```

ثم افتح `http://localhost:8090`.

إن لم يجد الخادم ملفَي `config/system_prompt.md` و`config/escalation_tool.json` فلن يعمل، ويطبع رسالة تقول أين يجب وضعهما.

## المتغيرات (ملف `.env`)

| المتغير | المعنى |
|---|---|
| `GEMINI_API_KEY` | مفتاح Gemini (في الخادم فقط، لا يصل إلى المتصفح) |
| `GEMINI_MODEL` | اسم النموذج (افتراضيًا `gemini-3.6-flash`) |
| `COMPLAINTS_EMAIL` | البريد المستلم لكل الطلبات (الشكاوى وطلبات الفتوى الموجّهة إلى مكتب المستشارين الشرعيين)؛ ثابت ولا يأخذه النموذج من المستخدم |
| `SMTP_HOST/PORT/USER/PASSWORD/FROM/USE_SSL` | إعدادات الإرسال (587 مع STARTTLS أو 465 مع SSL) |
| `EMAIL_DRY_RUN` | `true` = تُكتب الرسائل في `data/outbox/*.eml` بدل إرسالها |
| `ALLOWED_ORIGINS` | النطاقات المسموح لها باستدعاء الـAPI (CORS) |
| `CHAT_RATE_LIMIT_PER_MINUTE` | حد الطلبات لكل IP (افتراضيًا 30) |
| `CHATBOT_CONFIG_DIR` / `CHATBOT_DATA_DIR` | مجلد الإعدادات السرية ومجلد البيانات |

## نقاط الـAPI

- `POST /api/chat` — `{"session_id": اختياري, "message": "..."}` ← `{"session_id", "reply", "citations": [{"article", "page"}], "escalation": {"reference"} | null, "limit_reached"}`
- `POST /api/consult` — `{"session_id", "name", "contact_phone", "contact_email", "city_or_office", "question"}` ← `{"reference"}`: يرسل طلب فتوى بسؤال المستخدم إلى مكتب المستشارين الشرعيين عبر بريد الشكاوى (بلا نموذج) عندما لا يجد المساعد الجواب؛ تظهر الاستمارة في الواجهة تلقائيًا مع رد «لا تتناول اللائحة…» أو من رابط «لم تجد الجواب؟»
- `POST /api/session/new` — جلسة جديدة
- `GET /api/health` — حالة الخادم

`POST /api/chat` يعيد أيضًا `no_answer: true` عندما تكون كتلة المعرفة فارغة أو يصرّح الرد بأن اللائحة لا تتناول المسألة، فتعرض الواجهة زر الاستشارة.
- `GET /` — الواجهة

## كيف يعمل

1. `knowledge.py` يكتشف الموضوع (`detect_topics`) ويسترجع أفضل 5 سجلات من اللائحة فقط (BM25) ويبنيها في كتلة `<knowledge>`؛ الرسائل القصيرة أو التكميلية تُبحث مع السؤال السابق.
2. `llm.py` يرسل تعليمات النظام + المحادثة كاملة + الكتلة إلى Gemini مع تعريف الأداة. إذا طلب النموذج الأداة يُنفَّذ الطلب (حتى 3 دورات) وتُعاد كل النتائج في رسالة واحدة. المحادثة تُبنى بالإضافة فقط.
3. `escalation.py` يتحقق من المدخلات (pydantic)، ويطبّق الحدود (3 لكل جلسة، 20 في الساعة لكل IP)، ويُنشئ رقمًا مرجعيًا `ZKT-YYYYMMDD-XXXX`، ويرسل البريد أو يكتبه في `data/outbox/`، ويسجّل الطلب في `data/escalations.sqlite` دون الهاتف أو البريد.
4. `sessions.py` يحفظ المحادثات في `data/sessions.sqlite` ويحذف الأقدم من 30 يومًا عند التشغيل، وحد الجلسة 40 رسالة.

## الاختبارات والتقييم

```bash
pytest chatbot/tests -q                      # بلا اتصال حقيقي بالـAPI أو البريد
python -m chatbot.eval_questions --yes       # 15 سؤالًا واقعيًا بالمفتاح الحقيقي (يستهلك رصيدًا)
```
