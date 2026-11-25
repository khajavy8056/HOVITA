"use client";

import React, { useCallback, useEffect, useRef, useState } from "react";

type TabKey =
  | "onboarding"
  | "identity"
  | "social"
  | "production"
  | "live"
  | "economy";

interface SystemStatus {
  character: {
    id: string;
    name: string;
    job: string;
    goal: string;
    status: string;
    autonomy_level: "L0" | "L1" | "L2" | "L3";
    publish_mode: string;
    emergency_stop: boolean;
    version: number;
  };
  budget: {
    daily_spend_usd: number;
    daily_budget_usd: number;
    monthly_spend_usd: number;
    utilization_pct: number;
    warning_80pct: boolean;
    budget_exhausted: boolean;
  };
  gpu: {
    mode: "on_demand" | "always_on" | "hourly_rental";
    state: string;
    gpu_available: boolean;
    idle_sleep_minutes: number;
    total_energy_kwh: number;
  };
  pending_approvals: number;
  audit_chain_valid: boolean;
  free_first_policy: string;
}

interface OnboardingStep {
  step_index: number;
  step_key: string;
  title: string;
  status: string;
  pct: number;
}

interface ContentRecord {
  id: string;
  title: string;
  mode: string;
  status: string;
  platform: string;
  caption: string;
  assets: Record<string, string>;
  published_id: string | null;
  c2pa_verified: boolean;
  ai_disclosure_applied: boolean;
  qa_gates: Array<{
    gate: string;
    score: number;
    threshold: number;
    verdict: string;
    note: string;
  }>;
}

interface PendingApproval {
  approval_id: string;
  content_id: string;
  title: string;
  caption: string;
  platform: string;
  assets: Record<string, string>;
}

interface ModuleProviderMeta {
  provider_id: string;
  display_name: string;
  tier: string;
  degradation_level: string;
  active_override?: boolean;
}

function mediaUrl(filePath?: string | null): string {
  if (!filePath) return "";
  return `/api/v1/media?path=${encodeURIComponent(filePath)}`;
}

export default function HovitaDashboard() {
  const [tab, setTab] = useState<TabKey>("onboarding");
  const [status, setStatus] = useState<SystemStatus | null>(null);
  const [personaData, setPersonaData] = useState<Record<string, unknown> | null>(null);
  const [behaviorSamples, setBehaviorSamples] = useState<Array<Record<string, unknown>>>([]);
  const [steps, setSteps] = useState<OnboardingStep[]>([]);
  const [products, setProducts] = useState<Array<Record<string, unknown>>>([]);
  const [accounts, setAccounts] = useState<Array<Record<string, unknown>>>([]);
  const [contents, setContents] = useState<ContentRecord[]>([]);
  const [pending, setPending] = useState<PendingApproval[]>([]);
  const [captionEdits, setCaptionEdits] = useState<Record<string, string>>({});
  const [costs, setCosts] = useState<Record<string, unknown> | null>(null);
  const [decisions, setDecisions] = useState<Array<Record<string, unknown>>>([]);
  const [logs, setLogs] = useState<Array<Record<string, unknown>>>([]);
  const [modules, setModules] = useState<Record<string, ModuleProviderMeta[]>>({});
  const [activeOverrides, setActiveOverrides] = useState<Record<string, string>>({});

  const [busy, setBusy] = useState(false);
  const [banner, setBanner] = useState<string>("");
  const [errorMsg, setErrorMsg] = useState<string>("");

  // Form states
  const [builderMsg, setBuilderMsg] = useState(
    "یک شخصیت فروشنده عطر حرفه‌ای برای اینستاگرام بساز، اسمش آوا باشه، لحنش صمیمی و شفاف، بودجه ۶۰ دلار"
  );
  const [builderResp, setBuilderResp] = useState<Record<string, unknown> | null>(null);
  const [refinementNote, setRefinementNote] = useState("");
  const [voiceLang, setVoiceLang] = useState<"fa" | "en">("fa");
  const [voiceResult, setVoiceResult] = useState<Record<string, unknown> | null>(null);
  const [introVideo, setIntroVideo] = useState<Record<string, unknown> | null>(null);

  const [prodSku, setProdSku] = useState("PRF-001");
  const [prodName, setProdName] = useState("عطر رز هویتا ۱۰۰ میل");
  const [prodPrice, setProdPrice] = useState("65");
  const [prodStock, setProdStock] = useState("25");
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  const [socialPlatform, setSocialPlatform] = useState<"instagram" | "tiktok" | "youtube">("instagram");
  const [socialHandle, setSocialHandle] = useState("@hovita_persona");
  const [auditReport, setAuditReport] = useState<Record<string, unknown> | null>(null);

  const [produceTopic, setProduceTopic] = useState("معرفی تخصصی ماندگاری عطر تابستانی");
  const [produceMode, setProduceMode] = useState<"new_idea" | "trend_motion" | "hybrid">("new_idea");
  const [planOutput, setPlanOutput] = useState<Record<string, unknown> | null>(null);
  const [learningReport, setLearningReport] = useState<Record<string, unknown> | null>(null);

  const [chatInput, setChatInput] = useState("این ترند جدید عطر را بررسی کن");
  const [chatAttachUrl, setChatAttachUrl] = useState("");
  const [chatHistory, setChatHistory] = useState<
    Array<{ role: string; text: string; audioPath?: string | null }>
  >([]);

  const [callInput, setCallInput] = useState("برنامه هفته آینده روی فروش عطر تمرکز کنه");
  const [callResult, setCallResult] = useState<Record<string, unknown> | null>(null);
  const [livekitInfo, setLivekitInfo] = useState<Record<string, unknown> | null>(null);
  const [wsConnected, setWsConnected] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);

  const apiCall = useCallback(async (url: string, init?: RequestInit) => {
    const res = await fetch(url, {
      ...init,
      headers: {
        ...(init?.body instanceof FormData ? {} : { "Content-Type": "application/json" }),
        ...(init?.headers || {}),
      },
    });
    const data = await res.json();
    if (!res.ok) {
      throw new Error(data.detail || `HTTP ${res.status}`);
    }
    return data;
  }, []);

  const refreshAll = useCallback(async () => {
    try {
      const [st, per, stp, prd, acc, cnt, app, cst, dec, lg, mod] = await Promise.all([
        apiCall("/api/v1/system/status"),
        apiCall("/api/v1/persona"),
        apiCall("/api/v1/onboarding/steps"),
        apiCall("/api/v1/products"),
        apiCall("/api/v1/social/accounts"),
        apiCall("/api/v1/content"),
        apiCall("/api/v1/approvals/pending"),
        apiCall("/api/v1/costs/summary"),
        apiCall("/api/v1/decisions"),
        apiCall("/api/v1/logs"),
        apiCall("/api/v1/modules"),
      ]);
      setStatus(st);
      setPersonaData(per);
      setSteps(stp.steps || []);
      setProducts(prd.products || []);
      setAccounts(acc.accounts || []);
      setContents(cnt.items || []);
      setPending(app.pending || []);
      setCosts(cst);
      setDecisions(dec.decisions || []);
      setLogs(lg.logs || []);
      setModules(mod.modules || {});
      setActiveOverrides(mod.active_overrides || {});
    } catch (err) {
      setErrorMsg(err instanceof Error ? err.message : "خطا در دریافت وضعیت سامانه");
    }
  }, [apiCall]);

  useEffect(() => {
    void refreshAll();
    return () => {
      wsRef.current?.close();
    };
  }, [refreshAll]);

  async function runAction(label: string, fn: () => Promise<void>) {
    setBusy(true);
    setErrorMsg("");
    setBanner("");
    try {
      await fn();
      setBanner(label);
      await refreshAll();
    } catch (err) {
      setErrorMsg(err instanceof Error ? err.message : "عملیات ناموفق بود");
    } finally {
      setBusy(false);
    }
  }

  function connectLiveWebSocket() {
    if (typeof window === "undefined") return;
    if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) return;
    const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
    // Route directly to API host/port if on 3000, or relative host
    const apiHost = window.location.port === "3000" ? `${window.location.hostname}:8000` : window.location.host;
    const ws = new WebSocket(`${proto}//${apiHost}/ws/live-call/web-ws-call`);
    ws.onopen = () => setWsConnected(true);
    ws.onclose = () => setWsConnected(false);
    ws.onmessage = (evt) => {
      try {
        const parsed = JSON.parse(evt.data);
        setCallResult(parsed);
      } catch {
        // ignore malformed frame
      }
    };
    wsRef.current = ws;
  }

  const refImages = (
    ((personaData?.identity_assets as Array<Record<string, unknown>>) || []).filter(
      (a) => a.kind === "ref_image"
    )
  );
  const frontPortraitPath = refImages[0]?.path ? String(refImages[0].path) : "";

  return (
    <div className="container">
      {/* Top Control & Emergency Kill Switch Bar */}
      <header className="topbar" data-testid="hovita-topbar">
        <div className="brand">
          <h1>
            HOVITA · {status?.character.name || "شخصیت هوشمند مستقل"}
          </h1>
          <div style={{ fontSize: "0.84rem", color: "var(--muted)" }}>
            شناسه نصب تک‌شخصیتی:{" "}
            <span className="mono">{status?.character.id || "..."}</span> | وضعیت:{" "}
            <strong>{status?.character.status || "..."}</strong> | شغل:{" "}
            <strong>{status?.character.job || "..."}</strong>
          </div>
        </div>

        <div className="status-pills">
          <span
            className={`pill ${status?.budget.warning_80pct ? "amber" : "green"}`}
            data-testid="budget-pill"
          >
            بودجه روزانه: ${status?.budget.daily_spend_usd.toFixed(3) || "0.000"} / $
            {status?.budget.daily_budget_usd.toFixed(2) || "3.00"} (
            {status?.budget.utilization_pct.toFixed(0) || 0}%)
          </span>
          <span className="pill">
            GPU: {status?.gpu.state || "sleeping"} ({status?.gpu.mode || "on_demand"})
          </span>
          <span className={`pill ${status?.audit_chain_valid ? "green" : "red"}`}>
            ممیزی امنیتی: {status?.audit_chain_valid ? "سالم (Hash Valid)" : "خطا"}
          </span>

          <select
            className="select"
            style={{ width: "auto", marginBottom: 0 }}
            data-testid="autonomy-select"
            value={status?.character.autonomy_level || "L1"}
            onChange={(e) =>
              void runAction(`سطح خودمختاری به ${e.target.value} تغییر یافت`, async () => {
                await apiCall("/api/v1/system/autonomy", {
                  method: "POST",
                  body: JSON.stringify({ autonomy_level: e.target.value }),
                });
              })
            }
          >
            <option value="L0">L0 · فقط تولید در کتابخانه</option>
            <option value="L1">L1 · نیازمند تایید انسانی</option>
            <option value="L2">L2 · زمان‌بندی خودکار</option>
            <option value="L3">L3 · خودمختار کامل</option>
          </select>

          {status?.character.status === "paused" ? (
            <button
              className="btn btn-secondary"
              data-testid="resume-persona-btn"
              disabled={busy}
              onClick={() =>
                void runAction("فعالیت شخصیت از سر گرفته شد", async () => {
                  await apiCall("/api/v1/system/resume", { method: "POST" });
                })
              }
            >
              ادامه فعالیت
            </button>
          ) : (
            <button
              className="btn btn-secondary"
              data-testid="pause-persona-btn"
              disabled={busy}
              onClick={() =>
                void runAction("فعالیت شخصیت موقتاً مکث شد", async () => {
                  await apiCall("/api/v1/system/pause", { method: "POST" });
                })
              }
            >
              مکث شخصیت
            </button>
          )}

          {status?.character.emergency_stop ? (
            <button
              className="btn btn-primary"
              data-testid="emergency-resume-btn"
              disabled={busy}
              onClick={() =>
                void runAction("حالت عادی از سر گرفته شد", async () => {
                  await apiCall("/api/v1/system/emergency-resume", { method: "POST" });
                })
              }
            >
              رفع توقف اضطراری
            </button>
          ) : (
            <button
              className="btn btn-danger"
              data-testid="emergency-stop-btn"
              disabled={busy}
              onClick={() =>
                void runAction("توقف اضطراری فعال و صف‌ها متوقف شدند", async () => {
                  await apiCall("/api/v1/system/emergency-stop", {
                    method: "POST",
                    body: JSON.stringify({ reason: "توقف دستی از داشبورد مدیر" }),
                  });
                })
              }
            >
              توقف اضطراری (Kill Switch)
            </button>
          )}
        </div>
      </header>

      {banner && <div className="alert" data-testid="status-banner">{banner}</div>}
      {errorMsg && <div className="alert error" data-testid="error-banner">{errorMsg}</div>}

      {/* Navigation Tabs */}
      <nav className="nav-tabs">
        {[
          { key: "onboarding", label: "۱. سازنده و راه‌اندازی ۸ مرحله‌ای" },
          { key: "identity", label: "۲. استودیو هویت، صدا و محصولات" },
          { key: "social", label: "۳. اتصال شبکه‌ها و ممیزی پیج" },
          {
            key: "production",
            label: `۴. خط تولید، QA و صف تایید (${pending.length})`,
          },
          { key: "live", label: "۵. چت مستقیم و تماس زنده" },
          { key: "economy", label: "۶. هزینه‌ها، ماژول‌ها و امنیت" },
        ].map((item) => (
          <button
            key={item.key}
            data-testid={`tab-${item.key}`}
            className={`tab-btn ${tab === item.key ? "active" : ""}`}
            onClick={() => setTab(item.key as TabKey)}
          >
            {item.label}
          </button>
        ))}
      </nav>

      {/* TAB 1: Conversational Builder + 8-Step Onboarding + F04 Samples */}
      {tab === "onboarding" && (
        <div className="grid-2">
          <section className="card">
            <h2>سازنده گفتگومحور شخصیت و بریف (F01 · زیر ۱۵ سوال)</h2>
            <textarea
              className="textarea"
              rows={3}
              data-testid="builder-input"
              value={builderMsg}
              onChange={(e) => setBuilderMsg(e.target.value)}
            />
            <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
              <button
                className="btn btn-primary"
                data-testid="builder-submit-btn"
                disabled={busy}
                onClick={() =>
                  void runAction("پاسخ سازنده شخصیت پردازش شد", async () => {
                    const res = await apiCall("/api/v1/persona/builder", {
                      method: "POST",
                      body: JSON.stringify({
                        thread_id: "ui-onboarding",
                        message: builderMsg,
                      }),
                    });
                    setBuilderResp(res);
                  })
                }
              >
                ارسال به سازنده شخصیت
              </button>
              <button
                className="btn btn-secondary"
                disabled={busy}
                onClick={() =>
                  void runAction("تکمیل سریع پروفایل و بریف JSON", async () => {
                    const res = await apiCall("/api/v1/persona/builder", {
                      method: "POST",
                      body: JSON.stringify({
                        thread_id: "ui-onboarding",
                        message: builderMsg,
                        explicit_fields: {
                          name: "آوا راد",
                          appearance_desc: "۲۵ ساله، موهای موج‌دار تیره، استایل مدرن و طبیعی",
                          tone_desc: "صمیمی، شفاف، حرفه‌ای و دقیق در معرفی محصول",
                          platforms: ["instagram_reels", "tiktok"],
                        },
                      }),
                    });
                    setBuilderResp(res);
                  })
                }
              >
                نهایی‌سازی ساختار JSON
              </button>
              <button
                className="btn btn-secondary"
                disabled={busy}
                onClick={() =>
                  void runAction("۵ نمونه رفتاری و خط قرمز بررسی شدند", async () => {
                    const res = await apiCall("/api/v1/persona/behavior-samples");
                    setBehaviorSamples(res.samples || []);
                  })
                }
              >
                آزمون ۵ نمونه رفتاری (F04)
              </button>
            </div>
            {builderResp && (
              <pre
                className="mono"
                style={{
                  marginTop: 12,
                  padding: 12,
                  background: "var(--bg)",
                  borderRadius: 10,
                  overflowX: "auto",
                  fontSize: "0.78rem",
                }}
              >
                {JSON.stringify(builderResp, null, 2)}
              </pre>
            )}
            {behaviorSamples.length > 0 && (
              <div style={{ marginTop: 12 }}>
                <h3>نمونه‌های آزمون سبک و خطوط قرمز (F04)</h3>
                {behaviorSamples.map((s, idx) => (
                  <div key={idx} className="alert" style={{ marginBottom: 6 }}>
                    <strong>{String(s.scenario)}:</strong> {String(s.sample_text)}{" "}
                    <span className={`pill ${s.passed_style_gate ? "green" : "red"}`}>
                      {s.passed_style_gate ? "✅ سبز" : "❌ خطا"}
                    </span>
                  </div>
                ))}
              </div>
            )}
          </section>

          <section className="card">
            <h2>پیشرفت ۸ مرحله راه‌اندازی (F09)</h2>
            {steps.map((s) => (
              <div key={s.step_index} style={{ marginBottom: 12 }}>
                <div style={{ display: "flex", justifyContent: "space-between", fontSize: "0.86rem" }}>
                  <span>
                    {s.step_index}. {s.title}
                  </span>
                  <span className="mono">
                    {s.status} ({s.pct}%)
                  </span>
                </div>
                <div className="progress-bar">
                  <div className="progress-fill" style={{ width: `${s.pct}%` }} />
                </div>
              </div>
            ))}
          </section>
        </div>
      )}

      {/* TAB 2: Identity Studio, Visual Gallery, Voice Player, Intro Video, Products CSV/XLSX */}
      {tab === "identity" && (
        <div className="grid-2">
          <section className="card">
            <h2>تثبیت هویت چهره و صدا (F02 · F03 · F06)</h2>
            <input
              className="input"
              placeholder="اصلاح ظاهری گفتگومحور (مثلاً: موها فرتر، بینی ثابت)"
              value={refinementNote}
              onChange={(e) => setRefinementNote(e.target.value)}
            />
            <div style={{ display: "flex", flexWrap: "wrap", gap: 8, marginBottom: 12 }}>
              <button
                className="btn btn-primary"
                data-testid="gen-multiangle-btn"
                disabled={busy}
                onClick={() =>
                  void runAction("۶ زاویه هویت بصری + LoRA با ArcFace >= 0.72 قفل شد", async () => {
                    await apiCall("/api/v1/identity/multi-angle", {
                      method: "POST",
                      body: JSON.stringify({ refinement_note: refinementNote, approve: true }),
                    });
                  })
                }
              >
                ساخت و قفل ۶ زاویه چهره
              </button>

              <select
                className="select"
                style={{ width: "auto", marginBottom: 0 }}
                value={voiceLang}
                onChange={(e) => setVoiceLang(e.target.value as "fa" | "en")}
              >
                <option value="fa">صدای فارسی (pocket-tts-farsi)</option>
                <option value="en">صدای انگلیسی (Kokoro/F5)</option>
              </select>

              <button
                className="btn btn-secondary"
                disabled={busy}
                onClick={() =>
                  void runAction("۳ نمونه صدا ساخته و کلون صدا قفل شد", async () => {
                    const res = await apiCall("/api/v1/identity/voice-audition", {
                      method: "POST",
                      body: JSON.stringify({ language: voiceLang, selected_energy: "balanced", approve: true }),
                    });
                    setVoiceResult(res);
                  })
                }
              >
                تست و قفل صدا
              </button>

              <button
                className="btn btn-secondary"
                disabled={busy}
                onClick={() =>
                  void runAction("ویدیوی معرفی پیش‌نمایش (F06) آماده پخش است", async () => {
                    const res = await apiCall("/api/v1/identity/intro-preview", { method: "POST" });
                    setIntroVideo(res);
                  })
                }
              >
                تولید ویدیو معرفی (F06)
              </button>
            </div>

            {introVideo && typeof introVideo.preview_mp4_path === "string" && (
              <div style={{ marginBottom: 14 }}>
                <h3>پیش‌نمایش نهایی قبل از شروع فعالیت (F06)</h3>
                <video
                  controls
                  style={{ width: "100%", maxHeight: 320, borderRadius: 10, background: "#000" }}
                  src={mediaUrl(introVideo.preview_mp4_path)}
                />
              </div>
            )}

            {voiceResult && Array.isArray(voiceResult.candidates) && (
              <div style={{ marginBottom: 14 }}>
                <h3>نمونه‌های صدا (F03 · قفل: {String(voiceResult.voice_id)})</h3>
                {(voiceResult.candidates as Array<Record<string, unknown>>).map((c) => (
                  <div key={String(c.voice_id)} style={{ marginBottom: 8 }}>
                    <span className="mono" style={{ fontSize: "0.8rem" }}>
                      {String(c.energy)} ({String(c.duration_sec)}s):
                    </span>
                    <audio
                      controls
                      style={{ width: "100%", height: 34, marginTop: 4 }}
                      src={mediaUrl(String(c.sample_path))}
                    />
                  </div>
                ))}
              </div>
            )}

            <h3>گالری ۶ زاویه چهره تثبیت‌شده</h3>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 8 }}>
              {refImages.slice(-6).map((a) => (
                <div
                  key={String(a.id)}
                  style={{
                    background: "var(--bg)",
                    borderRadius: 10,
                    padding: 6,
                    border: "1px solid var(--border)",
                  }}
                >
                  <img
                    src={mediaUrl(String(a.path))}
                    alt={String(a.angle)}
                    style={{ width: "100%", height: 110, objectFit: "cover", borderRadius: 6 }}
                  />
                  <div style={{ fontSize: "0.75rem", marginTop: 4 }} className="mono">
                    {String(a.angle)} · {Number(a.arcface_score).toFixed(2)}
                  </div>
                </div>
              ))}
            </div>
          </section>

          <section className="card">
            <h2>کاتالوگ حقیقت محصول (F19) و محیط (F18)</h2>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
              <input className="input" value={prodSku} onChange={(e) => setProdSku(e.target.value)} placeholder="SKU" />
              <input className="input" value={prodName} onChange={(e) => setProdName(e.target.value)} placeholder="نام محصول" />
              <input className="input" value={prodPrice} onChange={(e) => setProdPrice(e.target.value)} placeholder="قیمت $" />
              <input className="input" value={prodStock} onChange={(e) => setProdStock(e.target.value)} placeholder="موجودی" />
            </div>
            <div style={{ display: "flex", flexWrap: "wrap", gap: 8, marginBottom: 12 }}>
              <button
                className="btn btn-primary"
                disabled={busy}
                onClick={() =>
                  void runAction("محصول در منبع حقیقت ثبت شد", async () => {
                    await apiCall("/api/v1/products", {
                      method: "POST",
                      body: JSON.stringify({
                        sku: prodSku,
                        name: prodName,
                        price: Number(prodPrice),
                        stock: Number(prodStock),
                        buy_link: `https://shop.hovita.local/${prodSku}`,
                      }),
                    });
                  })
                }
              >
                ثبت دستی محصول
              </button>

              <input
                ref={fileInputRef}
                type="file"
                accept=".csv,.xlsx"
                style={{ display: "none" }}
                onChange={(e) => {
                  const file = e.target.files?.[0];
                  if (!file) return;
                  void runAction("کاتالوگ اکسل/CSV با موفقیت وارد شد", async () => {
                    const fd = new FormData();
                    fd.append("file", file);
                    await apiCall("/api/v1/products/import", { method: "POST", body: fd });
                  });
                }}
              />
              <button
                className="btn btn-secondary"
                disabled={busy}
                onClick={() => fileInputRef.current?.click()}
              >
                ورود گروهی CSV / XLSX
              </button>

              <button
                className="btn btn-secondary"
                disabled={busy}
                onClick={() =>
                  void runAction("پک مرجع محیط کار نسخه‌بندی و قفل شد", async () => {
                    await apiCall("/api/v1/identity/environment", {
                      method: "POST",
                      body: JSON.stringify({
                        kind: "real",
                        capture_method: "multi_angle_photos",
                        media: ["desk_main.jpg", "shelf.jpg"],
                        palette: ["#1D2D44", "#F4EFE6"],
                        fixed_props: ["نور گرم", "میز چوبی"],
                        product_zones: ["مرکز کادر"],
                      }),
                    });
                  })
                }
              >
                قفل محیط مرجع (F18)
              </button>
            </div>

            <table className="table">
              <thead>
                <tr>
                  <th>SKU</th>
                  <th>نام</th>
                  <th>قیمت</th>
                  <th>موجودی</th>
                  <th>نسخه</th>
                </tr>
              </thead>
              <tbody>
                {products.map((p) => (
                  <tr key={String(p.id)}>
                    <td className="mono">{String(p.sku)}</td>
                    <td>{String(p.name)}</td>
                    <td className="mono">${String(p.price)}</td>
                    <td>{String(p.stock)}</td>
                    <td className="mono">v{String(p.version)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>
        </div>
      )}

      {/* TAB 3: Social Connect & Page Audit */}
      {tab === "social" && (
        <div className="grid-2">
          <section className="card">
            <h2>اتصال حساب رسمی (F07)</h2>
            <select
              className="select"
              value={socialPlatform}
              onChange={(e) => setSocialPlatform(e.target.value as "instagram" | "tiktok" | "youtube")}
            >
              <option value="instagram">Instagram Graph API</option>
              <option value="tiktok">TikTok Content Posting API</option>
              <option value="youtube">YouTube Data API</option>
            </select>
            <input
              className="input"
              value={socialHandle}
              onChange={(e) => setSocialHandle(e.target.value)}
              placeholder="@handle"
            />
            <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
              <button
                className="btn btn-primary"
                disabled={busy}
                onClick={() =>
                  void runAction("حساب شبکه اجتماعی با کلید رمزگذاری‌شده متصل شد", async () => {
                    await apiCall("/api/v1/social/connect", {
                      method: "POST",
                      body: JSON.stringify({
                        platform: socialPlatform,
                        handle: socialHandle,
                      }),
                    });
                  })
                }
              >
                اتصال رسمی OAuth
              </button>
              <button
                className="btn btn-secondary"
                disabled={busy}
                onClick={() =>
                  void runAction("ممیزی پیج و تکمیل ۸ گام اجرا شد", async () => {
                    const res = await apiCall("/api/v1/social/audit", {
                      method: "POST",
                      body: JSON.stringify({
                        platform: socialPlatform,
                        handle: socialHandle,
                        page_state: "existing",
                      }),
                    });
                    setAuditReport(res);
                  })
                }
              >
                ممیزی پیج (F08)
              </button>
              <button
                className="btn btn-secondary"
                disabled={busy}
                onClick={() =>
                  void runAction("انتشار آزمایشی در سندباکس و همگام‌سازی متریک انجام شد", async () => {
                    await apiCall("/api/v1/social/sandbox-test", { method: "POST", body: JSON.stringify({}) });
                    await apiCall("/api/v1/social/sync-metrics", { method: "POST" });
                  })
                }
              >
                تست سندباکس + خواندن آمار
              </button>
            </div>

            <h3 style={{ marginTop: 14 }}>حساب‌های متصل و وضعیت انقضای توکن</h3>
            <table className="table">
              <thead>
                <tr>
                  <th>پلتفرم</th>
                  <th>شناسه</th>
                  <th>انقضای توکن</th>
                  <th>انتشار امروز</th>
                </tr>
              </thead>
              <tbody>
                {accounts.map((a) => (
                  <tr key={String(a.id)}>
                    <td>{String(a.platform)}</td>
                    <td className="mono">{String(a.handle)}</td>
                    <td>
                      <span className={`pill ${a.expiring_soon_warning ? "red" : "green"}`}>
                        {a.hours_until_expiry ? `${String(a.hours_until_expiry)}h` : "فعال"}
                      </span>
                    </td>
                    <td className="mono">{String(a.daily_publish_count)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>

          <section className="card">
            <h2>گزارش ممیزی پیج (۵ یافته + ۳ پیشنهاد)</h2>
            {auditReport ? (
              <div>
                <div className="pill green" style={{ display: "inline-block", marginBottom: 10 }}>
                  تشخیص وضعیت: {String(auditReport.detected_state)}
                </div>
                <h3>یافته‌ها</h3>
                <ul>
                  {((auditReport.findings as string[]) || []).map((f, i) => (
                    <li key={i}>{f}</li>
                  ))}
                </ul>
                <h3>پیشنهادهای هفته اول</h3>
                <ul>
                  {((auditReport.recommendations as string[]) || []).map((r, i) => (
                    <li key={i}>{r}</li>
                  ))}
                </ul>
              </div>
            ) : (
              <p style={{ color: "var(--muted)" }}>دکمه «ممیزی پیج (F08)» را برای بررسی وضعیت پیج بزنید.</p>
            )}
          </section>
        </div>
      )}

      {/* TAB 4: Production Pipeline, 7 QA Gates & Editable Approval Queue */}
      {tab === "production" && (
        <div className="grid-2">
          <section className="card">
            <h2>برنامه‌ریز (Planner) و پایپلاین ۱۱ مرحله‌ای (M4)</h2>
            <input
              className="input"
              data-testid="produce-topic-input"
              value={produceTopic}
              onChange={(e) => setProduceTopic(e.target.value)}
            />
            <select
              className="select"
              value={produceMode}
              onChange={(e) => setProduceMode(e.target.value as "new_idea" | "trend_motion" | "hybrid")}
            >
              <option value="new_idea">حالت ۲: ایده جدید و ریلز صفر تا صد</option>
              <option value="trend_motion">حالت ۱: انتقال حرکت روی ترند (Wan Animate)</option>
              <option value="hybrid">حالت ۳: ترکیبی</option>
            </select>
            <div style={{ display: "flex", gap: 8, marginBottom: 12 }}>
              <button
                className="btn btn-secondary"
                disabled={busy}
                onClick={() =>
                  void runAction("گراف وظیفه و لاگ تصمیم برنامه‌ریز ثبت شد", async () => {
                    const res = await apiCall("/api/v1/planner/plan", {
                      method: "POST",
                      body: JSON.stringify({
                        preferred_platform: "sandbox",
                        user_directive: produceTopic,
                      }),
                    });
                    setPlanOutput(res);
                  })
                }
              >
                اجرای Planner Agent
              </button>
              <button
                className="btn btn-primary"
                data-testid="run-pipeline-btn"
                disabled={busy}
                onClick={() =>
                  void runAction("پایپلاین ۱۱ مرحله‌ای تولید و گیت‌های کیفیت اجرا شد", async () => {
                    await apiCall("/api/v1/pipeline/produce", {
                      method: "POST",
                      body: JSON.stringify({
                        topic: produceTopic,
                        mode: produceMode,
                        platform: "sandbox",
                      }),
                    });
                  })
                }
              >
                تولید ویدیو + ۷ گیت QA
              </button>
            </div>
            {planOutput && (
              <div className="alert">
                تصمیم برنامه‌ریز: <strong>{String(planOutput.decision)}</strong>
              </div>
            )}

            <h3>صف تایید انسانی (امکان ویرایش کپشن پیش از انتشار)</h3>
            {pending.length === 0 ? (
              <p style={{ color: "var(--muted)" }}>موردی در انتظار تایید نیست.</p>
            ) : (
              pending.map((p) => (
                <div
                  key={p.approval_id}
                  data-testid={`pending-approval-${p.content_id}`}
                  style={{
                    border: "1px solid var(--border)",
                    borderRadius: 10,
                    padding: 12,
                    marginBottom: 10,
                  }}
                >
                  <strong>{p.title}</strong>
                  {p.assets?.final_mp4 && (
                    <video
                      controls
                      style={{ width: "100%", maxHeight: 220, borderRadius: 8, margin: "8px 0", background: "#000" }}
                      src={mediaUrl(p.assets.final_mp4)}
                    />
                  )}
                  <textarea
                    className="textarea"
                    rows={2}
                    value={captionEdits[p.content_id] ?? p.caption}
                    onChange={(e) =>
                      setCaptionEdits((prev) => ({ ...prev, [p.content_id]: e.target.value }))
                    }
                  />
                  <div style={{ display: "flex", gap: 8 }}>
                    <button
                      className="btn btn-primary"
                      data-testid="approve-publish-btn"
                      disabled={busy}
                      onClick={() =>
                        void runAction("تایید و انتشار رسمی با موفقیت انجام شد", async () => {
                          await apiCall(`/api/v1/approvals/${p.content_id}/decide`, {
                            method: "POST",
                            body: JSON.stringify({
                              decision: "approved",
                              comment: "تایید مدیر",
                              edited_caption: captionEdits[p.content_id] ?? p.caption,
                            }),
                          });
                        })
                      }
                    >
                      تایید و انتشار رسمی
                    </button>
                    <button
                      className="btn btn-danger"
                      disabled={busy}
                      onClick={() =>
                        void runAction("محتوا رد شد", async () => {
                          await apiCall(`/api/v1/approvals/${p.content_id}/decide`, {
                            method: "POST",
                            body: JSON.stringify({ decision: "rejected", comment: "رد مدیر" }),
                          });
                        })
                      }
                    >
                      رد
                    </button>
                  </div>
                </div>
              ))
            )}
          </section>

          <section className="card">
            <h2>کتابخانه خروجی‌ها و ۷ گیت کنترل کیفیت</h2>
            {contents.map((c) => (
              <div
                key={c.id}
                style={{
                  border: "1px solid var(--border)",
                  borderRadius: 10,
                  padding: 12,
                  marginBottom: 10,
                }}
              >
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                  <strong>{c.title}</strong>
                  <span className={`pill ${c.status === "published" ? "green" : "amber"}`}>
                    {c.status}
                  </span>
                </div>
                {c.assets?.final_mp4 && (
                  <video
                    controls
                    style={{ width: "100%", maxHeight: 200, borderRadius: 8, margin: "8px 0", background: "#000" }}
                    src={mediaUrl(c.assets.final_mp4)}
                  />
                )}
                <div style={{ fontSize: "0.8rem", color: "var(--muted)", margin: "6px 0" }}>
                  پلتفرم: {c.platform} | C2PA: {c.c2pa_verified ? "✅ تاییدشده" : "❌"} | AI Label:{" "}
                  {c.ai_disclosure_applied ? "✅ فعال" : "❌"}
                  {c.published_id && ` | ID: ${c.published_id}`}
                </div>
                {c.qa_gates.length > 0 && (
                  <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
                    {c.qa_gates.map((g, idx) => (
                      <span
                        key={idx}
                        className={`pill ${g.verdict === "pass" ? "green" : "red"}`}
                        style={{ fontSize: "0.75rem" }}
                      >
                        {g.gate}: {g.score}
                      </span>
                    ))}
                  </div>
                )}
              </div>
            ))}
          </section>
        </div>
      )}

      {/* TAB 5: Direct Chat (F16) with Audio+Attachment & WebSocket Live Call (F17) */}
      {tab === "live" && (
        <div className="grid-2">
          <section className="card">
            <h2>چت مستقیم متنی و صوتی با شخصیت (F16)</h2>
            <div
              style={{
                minHeight: 160,
                maxHeight: 260,
                overflowY: "auto",
                background: "var(--bg)",
                borderRadius: 10,
                padding: 12,
                marginBottom: 10,
              }}
            >
              {chatHistory.map((m, idx) => (
                <div key={idx} style={{ marginBottom: 10 }}>
                  <strong>{m.role === "user" ? "شما" : status?.character.name || "شخصیت"}: </strong>
                  <span>{m.text}</span>
                  {m.audioPath && (
                    <audio
                      controls
                      style={{ width: "100%", height: 32, marginTop: 4 }}
                      src={mediaUrl(m.audioPath)}
                    />
                  )}
                </div>
              ))}
            </div>
            <input
              className="input"
              data-testid="direct-chat-input"
              value={chatInput}
              onChange={(e) => setChatInput(e.target.value)}
            />
            <input
              className="input"
              placeholder="پیوست لینک/مرجع (اختیاری، مثلاً https://instagram.com/reel/...)"
              value={chatAttachUrl}
              onChange={(e) => setChatAttachUrl(e.target.value)}
            />
            <button
              className="btn btn-primary"
              data-testid="direct-chat-send"
              disabled={busy}
              onClick={() =>
                void runAction("پیام ارسال و در صورت نیاز تبدیل به برنامه تولید شد", async () => {
                  const res = await apiCall("/api/v1/chat", {
                    method: "POST",
                    body: JSON.stringify({
                      session_id: "web-chat-1",
                      message: chatInput,
                      with_voice: true,
                      attachments: chatAttachUrl ? [{ type: "link", url: chatAttachUrl }] : [],
                    }),
                  });
                  setChatHistory((prev) => [
                    ...prev,
                    { role: "user", text: chatInput },
                    { role: "persona", text: res.reply, audioPath: res.audio_path },
                  ]);
                })
              }
            >
              ارسال پیام و تبدیل به تسک
            </button>
          </section>

          <section className="card">
            <h2>تماس زنده چهره به چهره (F17 · WebRTC / WebSocket &lt; 2s)</h2>
            {frontPortraitPath && (
              <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 12 }}>
                <img
                  src={mediaUrl(frontPortraitPath)}
                  alt="Avatar"
                  style={{ width: 76, height: 76, borderRadius: "50%", objectFit: "cover", border: "2px solid var(--primary)" }}
                />
                <div style={{ fontSize: "0.84rem" }}>
                  <div><strong>{status?.character.name}</strong></div>
                  <div className="mono" style={{ color: "var(--muted)" }}>
                    WebSocket: {wsConnected ? "🟢 Connected" : "⚪ Ready"}
                  </div>
                </div>
              </div>
            )}

            <div style={{ display: "flex", flexWrap: "wrap", gap: 8, marginBottom: 10 }}>
              <button
                className="btn btn-secondary"
                disabled={busy}
                onClick={() =>
                  void runAction("اتاق WebRTC LiveKit آماده و WebSocket متصل شد", async () => {
                    const tok = await apiCall("/api/v1/live-call/token", { method: "POST" });
                    setLivekitInfo(tok);
                    connectLiveWebSocket();
                  })
                }
              >
                شروع تماس زنده LiveKit + WS
              </button>
            </div>
            {livekitInfo && (
              <div className="alert mono" style={{ fontSize: "0.78rem" }}>
                Room: {String(livekitInfo.room_name)} | Avatar: {String(livekitInfo.avatar_backend)}
              </div>
            )}
            <input
              className="input"
              value={callInput}
              onChange={(e) => setCallInput(e.target.value)}
            />
            <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
              <button
                className="btn btn-primary"
                disabled={busy}
                onClick={() => {
                  if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
                    wsRef.current.send(JSON.stringify({ utterance: callInput, end_call: false }));
                  } else {
                    void runAction("نوبت مکالمه زنده پاسخ داده شد", async () => {
                      const res = await apiCall("/api/v1/live-call/turn", {
                        method: "POST",
                        body: JSON.stringify({
                          session_id: "web-live-call",
                          utterance: callInput,
                          end_call_and_summarize: false,
                        }),
                      });
                      setCallResult(res);
                    });
                  }
                }}
              >
                ارسال گفتار زنده
              </button>
              <button
                className="btn btn-secondary"
                disabled={busy}
                onClick={() =>
                  void runAction("تماس پایان یافت و خلاصه در حافظه ثبت شد", async () => {
                    const res = await apiCall("/api/v1/live-call/turn", {
                      method: "POST",
                      body: JSON.stringify({
                        session_id: "web-live-call",
                        utterance: callInput,
                        end_call_and_summarize: true,
                      }),
                    });
                    setCallResult(res);
                  })
                }
              >
                پایان تماس و ثبت خلاصه در حافظه
              </button>
            </div>
            {callResult && (
              <div className="alert" style={{ marginTop: 12 }}>
                <div>پاسخ شخصیت: {String(callResult.reply)}</div>
                {typeof callResult.audio_path === "string" && (
                  <audio
                    controls
                    style={{ width: "100%", height: 34, marginTop: 6 }}
                    src={mediaUrl(callResult.audio_path)}
                  />
                )}
                <div className="mono" style={{ marginTop: 6, fontSize: "0.8rem" }}>
                  Latency: {String(callResult.latency_ms)} ms | SLA (&lt;2s):{" "}
                  {callResult.within_2s_sla ? "✅ PASS" : "⚠️"}
                  {callResult.call_summary_memory_id
                    ? ` | Memory ID: ${String(callResult.call_summary_memory_id)}`
                    : ""}
                </div>
              </div>
            )}
          </section>
        </div>
      )}

      {/* TAB 6: Cost Dashboard, 12-Module Hot-Swapper, GPU Control, Learning & Audit Logs */}
      {tab === "economy" && (
        <div className="grid-2">
          <section className="card">
            <h2>کنترل GPU، حلقه یادگیری (F14) و هزینه‌ها</h2>
            <div style={{ display: "flex", flexWrap: "wrap", gap: 8, marginBottom: 12 }}>
              <button
                className="btn btn-primary"
                disabled={busy}
                onClick={() =>
                  void runAction("حلقه یادگیری هفتگی اجرا شد", async () => {
                    const res = await apiCall("/api/v1/learning/weekly", { method: "POST" });
                    setLearningReport(res);
                  })
                }
              >
                اجرای حلقه یادگیری هفتگی
              </button>
              <button
                className="btn btn-secondary"
                disabled={busy}
                onClick={() =>
                  void runAction("دستور بیداری GPU ثبت شد", async () => {
                    await apiCall("/api/v1/system/gpu", {
                      method: "POST",
                      body: JSON.stringify({ action: "wake" }),
                    });
                  })
                }
              >
                بیدار کردن GPU
              </button>
              <button
                className="btn btn-secondary"
                disabled={busy}
                onClick={() =>
                  void runAction("GPU به حالت خواب (صرفه‌جویی انرژی) رفت", async () => {
                    await apiCall("/api/v1/system/gpu", {
                      method: "POST",
                      body: JSON.stringify({ action: "sleep" }),
                    });
                  })
                }
              >
                خواباندن GPU
              </button>
            </div>
            {learningReport && (
              <div className="alert">
                <strong>تصمیم تغییر برنامه:</strong> {String(learningReport.plan_change)}
              </div>
            )}

            <table className="table">
              <thead>
                <tr>
                  <th>Provider</th>
                  <th>اجراها</th>
                  <th>Tokens</th>
                  <th>GPU min</th>
                  <th>هزینه $</th>
                </tr>
              </thead>
              <tbody>
                {((costs?.by_provider as Array<Record<string, unknown>>) || []).map((row) => (
                  <tr key={String(row.provider_id)}>
                    <td className="mono">{String(row.provider_id)}</td>
                    <td>{String(row.runs)}</td>
                    <td className="mono">
                      {String(row.tokens_in)}/{String(row.tokens_out)}
                    </td>
                    <td className="mono">{String(row.gpu_min)}</td>
                    <td className="mono">${Number(row.cost_usd).toFixed(5)}</td>
                  </tr>
                ))}
              </tbody>
            </table>

            <h3 style={{ marginTop: 16 }}>تعویض زنده ۱۲ ماژول (بدون تغییر کد)</h3>
            <table className="table">
              <thead>
                <tr>
                  <th>ماژول</th>
                  <th>ارائه‌دهنده فعال / جایگزین</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(modules).map(([modName, provs]) => (
                  <tr key={modName}>
                    <td className="mono">{modName}</td>
                    <td>
                      <select
                        className="select"
                        style={{ marginBottom: 0 }}
                        value={activeOverrides[modName] || ""}
                        onChange={(e) =>
                          void runAction(
                            `ارائه‌دهنده ماژول ${modName} تعویض شد`,
                            async () => {
                              await apiCall("/api/v1/modules/override", {
                                method: "POST",
                                body: JSON.stringify({
                                  module_name: modName,
                                  provider_id: e.target.value,
                                }),
                              });
                            }
                          )
                        }
                      >
                        <option value="">پیش‌فرض خودکار (Free-First S0→S4)</option>
                        {provs.map((p) => (
                          <option key={p.provider_id} value={p.provider_id}>
                            {p.display_name} ({p.tier})
                          </option>
                        ))}
                      </select>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>

          <section className="card">
            <h2>لاگ تصمیم‌های برنامه‌ریز و ممیزی امنیتی زنجیره‌ای</h2>
            <h3>تصمیم‌های اخیر (Decision Log)</h3>
            {decisions.slice(0, 5).map((d) => (
              <div key={String(d.id)} className="alert" style={{ marginBottom: 8 }}>
                <div className="mono" style={{ fontSize: "0.78rem" }}>{String(d.decision)}</div>
                <div style={{ fontSize: "0.82rem", color: "var(--muted)" }}>{String(d.reason)}</div>
              </div>
            ))}

            <h3>رویدادهای ممیزی تغییرناپذیر (Audit Log)</h3>
            <table className="table">
              <thead>
                <tr>
                  <th>عامل</th>
                  <th>عملیات</th>
                  <th>هش</th>
                </tr>
              </thead>
              <tbody>
                {logs.slice(0, 10).map((l) => (
                  <tr key={String(l.id)}>
                    <td>{String(l.actor)}</td>
                    <td className="mono">{String(l.action)}</td>
                    <td className="mono">{String(l.entry_hash)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>
        </div>
      )}
    </div>
  );
}
