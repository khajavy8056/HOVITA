import { expect, test } from "@playwright/test";

// Browser UI End-to-End Test (requires Chromium binary installed via `npx playwright install chromium`)
test.describe("HOVITA Browser UI Flow", () => {
  test.skip(
    !process.env.PW_BROWSER_E2E,
    "Set PW_BROWSER_E2E=1 when Chromium browser binary is available (e.g. GitHub CI)"
  );

  test("User builds persona, locks face identity, produces video, approves & tests kill switch", async ({
    page,
  }) => {
    await page.goto("/");
    await expect(page.getByTestId("hovita-topbar")).toBeVisible();

    await page.getByTestId("builder-submit-btn").click();
    await expect(page.getByTestId("status-banner")).toBeVisible();

    await page.getByTestId("tab-identity").click();
    await page.getByTestId("gen-multiangle-btn").click();
    await expect(page.getByTestId("status-banner")).toContainText("ArcFace >= 0.72");

    await page.getByTestId("tab-production").click();
    await page.getByTestId("run-pipeline-btn").click();
    await expect(page.getByTestId("approve-publish-btn")).toBeVisible();
    await page.getByTestId("approve-publish-btn").click();
    await expect(page.getByTestId("status-banner")).toContainText("انتشار رسمی");

    await page.getByTestId("emergency-stop-btn").click();
    await expect(page.getByTestId("emergency-resume-btn")).toBeVisible();
    await page.getByTestId("emergency-resume-btn").click();
  });
});
