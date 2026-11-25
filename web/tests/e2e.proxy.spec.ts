import { expect, test } from "@playwright/test";

test.describe("HOVITA Fullstack Proxy & SSR E2E", () => {
  test("SSR page renders and Next.js /api proxy serves full persona lifecycle", async ({ request }) => {
    const htmlRes = await request.get("/");
    expect(htmlRes.status()).toBe(200);
    const html = await htmlRes.text();
    expect(html).toContain("HOVITA");

    const healthRes = await request.get("/healthz");
    expect(healthRes.status()).toBe(200);
    expect((await healthRes.json()).status).toBe("ok");

    // 1. Conversational Persona Builder
    const buildRes = await request.post("/api/v1/persona/builder", {
      data: {
        thread_id: "pw-e2e-thread",
        message: "شخصیت فروشنده عطر جوان برای اینستاگرام و تیک‌تاک بساز با بودجه ۶۰ دلار",
        explicit_fields: {
          name: "آوا ای۲ای",
          appearance_desc: "۲۶ ساله، موهای قهوه‌ای موج‌دار و استایل مدرن",
          tone_desc: "صمیمی، شفاف و حرفه‌ای",
          platforms: ["instagram_reels", "tiktok"],
        },
      },
    });
    expect(buildRes.status()).toBe(200);
    const buildJson = await buildRes.json();
    expect(buildJson.completed).toBe(true);

    // 2. Identity Multi-angle + Voice Clone
    const idRes = await request.post("/api/v1/identity/multi-angle", {
      data: { refinement_note: "موها طبیعی", approve: true },
    });
    expect(idRes.status()).toBe(200);
    const idJson = await idRes.json();
    expect(idJson.min_arcface_score).toBeGreaterThanOrEqual(0.72);

    const voiceRes = await request.post("/api/v1/identity/voice-audition", {
      data: { language: "fa", selected_energy: "balanced", approve: true },
    });
    expect(voiceRes.status()).toBe(200);

    // 3. L1 Pipeline -> Pause at Human Approval -> Approve -> Publish
    await request.post("/api/v1/system/autonomy", { data: { autonomy_level: "L1" } });
    const prodRes = await request.post("/api/v1/pipeline/produce", {
      data: { topic: "معرفی عطر ای۲ای", mode: "new_idea", platform: "sandbox" },
    });
    expect(prodRes.status()).toBe(200);
    const prodJson = await prodRes.json();
    expect(prodJson.status).toBe("awaiting_approval");

    const decideRes = await request.post(`/api/v1/approvals/${prodJson.content_id}/decide`, {
      data: { decision: "approved", comment: "Playwright E2E approved" },
    });
    expect(decideRes.status()).toBe(200);
    const decideJson = await decideRes.json();
    expect(decideJson.status).toBe("published");
    expect(decideJson.c2pa_verified).toBe(true);

    // 4. Module hot-swap + GPU + Pause/Resume + Direct Chat -> Audit Integrity
    const modSwap = await request.post("/api/v1/modules/override", {
      data: { module_name: "tts", provider_id: "kokoro_82m_local" },
    });
    expect(modSwap.status()).toBe(200);
    await request.post("/api/v1/modules/override", {
      data: { module_name: "tts", provider_id: "" },
    });

    const gpuRes = await request.post("/api/v1/system/gpu", {
      data: { action: "wake" },
    });
    expect(gpuRes.status()).toBe(200);

    const chatRes = await request.post("/api/v1/chat", {
      data: { session_id: "pw-chat", message: "این ترند جدید عطر را بررسی کن", with_voice: true },
    });
    expect(chatRes.status()).toBe(200);
    expect((await chatRes.json()).extracted_plan).not.toBeNull();

    const logsRes = await request.get("/api/v1/logs");
    expect(logsRes.status()).toBe(200);
    expect((await logsRes.json()).chain_valid).toBe(true);
  });
});
