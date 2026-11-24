# HOVITA — سیستم شخصیت‌های هوشمند مستقل (صفر تا صد)

**HOVITA** یک سامانه تجاری، ماژولار و آماده استقرار برای ساخت، تثبیت هویت (چهره و صدا)، اتصال رسمی شبکه‌های اجتماعی، تولید ویدیو با کنترل کیفیت چندمرحله‌ای، زمان‌بندی، انتشار رسمی، یادگیری هفتگی و مکالمه زنده با شخصیت هوشمند است.

---

## اصول معماری و تضمین‌های بنیادین

- **نصب تک‌شخصیتی (`Single-Persona per Install`)**: هر سرور/کانتینر/پایگاه‌داده منحصراً مالک یک شخصیت (`character_id`) است و قفل پایگاه‌داده (`singleton_lock = 1`) مانع اختلاط چند شخصیت در یک نصب می‌شود. برای شخصیت جدید از `make clone-install` استفاده کنید.
- **۱۲ ماژول قابل تعویض (`12 Swappable Modules`)**: هسته کسب‌وکار بدون وابستگی مستقیم به SDK ارائه‌دهندگان بیرونی، از طریق قراردادهای JSON صریح (`ProviderRegistry`) کار می‌کند (`llm`, `memory`, `research`, `image_gen`, `video_gen`, `motion_transfer`, `tts`, `stt`, `video_edit`, `social_analytics`, `publisher`, `workflow_engine`).
- **رایگان‌پیش‌فرض و اقتصاد هوشمند (`Free-First & S0→S4`)**: اولویت اجرای محلی/رایگان (`S0`), چرخش خودکار کلیدهای رایگان در خطای `429/5xx` (`S1`), و ارتقا به API پولی (`S2/S3`) صرفاً در صورت بهبود کیفیت **حداقل ۲۵٪** یا شکست گیت کیفیت. ثبت دقیق هزینه (`tin`, `tout`, `sec_video`, `images`, `min_call`, `gpu_min`, `energy_kwh`) با هشدار **۸۰٪ بودجه روزانه** و سقف **۲ بازتولید** به ازای هر محتوا.
- **پایپلاین ۱۱ مرحله‌ای با Checkpoint + ۷ گیت کنترل کیفیت**:
  - `hook_3s >= 6.0` (قاضی مستقل) · `ArcFace >= 0.72` · همگام‌سازی صدا/تصویر `>= 95%` · تطابق لحن/خط قرمز · ریتم ریلز `15–45s` با حداقل ۱ برش در ۵ ثانیه · گیت ایمنی و انطباق قیمت/ویژگی با **جدول حقیقت محصول (`product`)** · درج اجباری **برچسب هوش مصنوعی و متادیتای `C2PA`**.
- **انتشار صرفاً رسمی (`Official APIs Only`)**: اتصال و انتشار منحصراً از طریق API رسمی پلتفرم‌ها (Instagram Graph API / TikTok Content Posting API / YouTube Data API) با کلید یکتاسازی (`sha256(file_hash + platform + date)`) برای جلوگیری از انتشار تکراری و رعایت سقف روزانه. لایه مرورگر (`Playwright`) فقط خواندنی و برای بازبینی بصری/اکشن دستی کاربر است.
- **مدیریت منبع `CPU Always-On / GPU On-Demand`**: بخش CPU همیشه‌روشن، اما GPU فقط هنگام نیاز بیدار می‌شود، کارهای صف اولویت‌بندی‌شده را دسته‌ای اجرا می‌کند و پس از **۱۰ دقیقه بی‌کاری** به‌طور خودکار می‌خوابد.
- **امنیت و توقف اضطراری (`Kill Switch`)**: رمزنگاری کلیدها با `Fernet` (هرگز در کد/لاگ/دیتابیس ذخیره نمی‌شوند)، فیلتر ضدتزریق پرامپت (`Prompt Injection Gate`)، لاگ ممیزی زنجیره‌ای تغییرناپذیر (`SHA-256 hash chain`) و دکمه توقف اضطراری.

---

## شروع سریع (نصب تمیز در کمتر از ۳۰ دقیقه)

### ۱. اجرا با Docker Compose (محیط کامل تولید: PostgreSQL + pgvector, Redis, MinIO, FastAPI, Next.js)

```bash
cp .env.example .env
# مقداردهی SECRET_MASTER_KEY و توکن‌های API در .env
make up
make health
```

دسترسی‌ها:
- پنل مدیریت وب (Next.js): `http://localhost:3000`
- رابط API و مستندات OpenAPI: `http://localhost:8000/docs`
- وضعیت سلامت: `http://localhost:8000/healthz`

برای فعال‌سازی پروفایل محلی Ollama یا n8n (در صورت داشتن مجوز تجاری یا میزبانی شخصی):
```bash
docker compose --profile local-ai up -d
```

### ۲. راه‌اندازی مستقیم لوکال / توسعه (با پشتیبانی SQLite در نبود Postgres)

```bash
bash setup.sh
```

اجرای سرور توسعه:
```bash
# ترمینال ۱ — Backend API
make dev

# ترمینال ۲ — Frontend Next.js
cd web && npm run dev
```

---

## دستورات کلیدی (`Makefile`)

| دستور | شرح |
|---|---|
| `make install` | نصب وابستگی‌ها و محیط بدون اجرای اسموک |
| `make migrate` | اجرای مایگریشن‌های Alembic |
| `make seed` | مقداردهی اولیه شخصیت و ۸ گام راه‌اندازی (Idempotent) |
| `make health` | بررسی سلامت دیتابیس، ذخیره‌سازی و FFmpeg |
| `make lint` | بررسی استاندارد کد با `ruff` |
| `make typecheck` | بررسی تایپ ایستا با `mypy` |
| `make test` | اجرای کامل تست‌های `unit`, `contract`, `integration`, `resilience`, `security` |
| `make smoke` | اجرای تست دود End-to-End بکند |
| `make up` / `make down` | بالا آوردن / خاموش کردن استک Docker Compose |
| `make clone-install TARGET=../persona-2 CHAR_ID=persona-002 CHAR_NAME="سارا"` | کلون ایزوله برای نصب شخصیت جدید |

---

## ساختار مخزن

```text
├── alembic/                    # مایگریشن‌های دیتابیس (PostgreSQL pgvector و SQLite)
├── backend/src/hovita/
│   ├── config/                 # تنظیمات Pydantic v2 و SecretStore رمزنگاری‌شده
│   ├── contracts/              # قراردادهای JSON و Registry برای ۱۲ ماژول + Brief
│   ├── db/                     # مدل‌های SQLAlchemy 2.0 (۲۰ جدول) و بذر تک‌شخصیتی
│   ├── security/               # زنجیره ممیزی Hash، ضدتزریق پرامپت و Kill Switch
│   ├── economy/                # فرمول هزینه، بودجه روزانه، هشدار ۸۰٪ و چرخش کلید 429
│   ├── resources/              # ObjectStorage (MinIO/Local)، مدیر GPU و مرورگر خواندنی
│   ├── modules/                # پیاده‌سازی ۱۲ ماژول قابل تعویض (llm..workflow_engine)
│   ├── agents/                 # سازنده F01، استودیو هویت، Planner، Producer ۱۱ مرحله‌ای، QA، یادگیری، تماس زنده
│   ├── scheduler/              # زمان‌بند داخلی (پاک‌سازی انقضا، خواب GPU، چرخه خودمختار)
│   └── api/                    # سرویس FastAPI و WebSocket مکالمه زنده
├── web/                        # رابط کاربری Next.js 15 (RTL) و تست‌های Playwright
├── notebooks/                  # رانر سبک نوت‌بوک برای Colab / Kaggle / Lightning AI
├── scripts/                    # healthcheck، smoke_test و clone_install
├── docs/                       # مستندات تصمیم‌های معماری و استقرار
└── tests/                      # unit, contract, integration, smoke, resilience, security
```

---

## اجرای تست‌ها

```bash
# تست‌های کامل بک‌اند
make lint
make typecheck
make test
make smoke

# تست بیلد و E2E رابط کاربری
cd web && npm run lint && npm run build && npm run test:e2e
```

## مجوز (License)

تحت مجوز **MIT** منتشر شده است (`LICENSE` و `THIRD_PARTY_LICENSES.md`).
