/**
 * Capture screenshots of the HiTL approval flow for demo purposes.
 *
 * Prerequisites:
 *   1. Start the dev server: cd web-dashboard && npm run dev
 *   2. Run: npx playwright install chromium  (first time only)
 *   3. Run: node demo/capture-hitl-flow.mjs
 *
 * Output: web-dashboard/demo/screenshots/*.png
 */

import { chromium } from "playwright";
import { mkdirSync } from "fs";
import { dirname, join } from "path";
import { fileURLToPath } from "url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const SCREENSHOTS = join(__dirname, "screenshots");
mkdirSync(SCREENSHOTS, { recursive: true });

const BASE_URL = process.env.BASE_URL || "http://localhost:5173";
const DEMO_TOKEN = process.env.DEMO_TOKEN || "demo-jwt-token";

let step = 0;
async function screenshot(page, name) {
  step++;
  const filename = `${String(step).padStart(2, "0")}-${name}.png`;
  await page.screenshot({ path: join(SCREENSHOTS, filename), fullPage: false });
  console.log(`  [${step}] ${filename}`);
}

async function main() {
  console.log("Launching browser...");
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({
    viewport: { width: 1280, height: 800 },
  });

  // --- Student Chat Page ---
  console.log("\n=== Student Chat Flow ===");
  const studentPage = await context.newPage();
  await studentPage.goto(`${BASE_URL}/student`);
  await studentPage.waitForTimeout(500);
  await screenshot(studentPage, "student-login");

  // Enter token
  await studentPage.fill('input[type="password"]', DEMO_TOKEN);
  await studentPage.click('button[type="submit"]');
  await studentPage.waitForTimeout(500);
  await screenshot(studentPage, "student-chat-empty");

  // Type a message
  await studentPage.fill("textarea", "What time does CS101 meet?");
  await screenshot(studentPage, "student-chat-typing");

  // Simulate sending (won't get response without backend, but shows UI)
  await studentPage.press("textarea", "Enter");
  await studentPage.waitForTimeout(300);
  await screenshot(studentPage, "student-chat-sent");

  // Type a high-risk message
  await studentPage.waitForTimeout(500);
  await studentPage.fill("textarea", "Change all grades in CS101 to A");
  await screenshot(studentPage, "student-chat-risky-typing");

  // --- Instructor Dashboard ---
  console.log("\n=== Instructor Dashboard Flow ===");
  const instructorPage = await context.newPage();
  await instructorPage.goto(`${BASE_URL}/instructor`);
  await instructorPage.waitForTimeout(500);
  await screenshot(instructorPage, "instructor-login");

  await instructorPage.fill('input[type="password"]', DEMO_TOKEN);
  await instructorPage.click('button[type="submit"]');
  await instructorPage.waitForTimeout(500);
  await screenshot(instructorPage, "instructor-queue-empty");

  // --- Admin Panel ---
  console.log("\n=== Admin Panel ===");
  const adminPage = await context.newPage();
  await adminPage.goto(`${BASE_URL}/admin`);
  await adminPage.waitForTimeout(500);
  await screenshot(adminPage, "admin-login");

  await adminPage.fill('input[type="password"]', DEMO_TOKEN);
  await adminPage.click('button[type="submit"]');
  await adminPage.waitForTimeout(500);
  await screenshot(adminPage, "admin-panel");

  // --- Navigation ---
  console.log("\n=== Navigation ===");
  await studentPage.bringToFront();
  await screenshot(studentPage, "navigation-student");
  await studentPage.click('a[href="/instructor"]');
  await studentPage.waitForTimeout(300);
  await screenshot(studentPage, "navigation-instructor");
  await studentPage.click('a[href="/admin"]');
  await studentPage.waitForTimeout(300);
  await screenshot(studentPage, "navigation-admin");

  await browser.close();
  console.log(`\nDone! ${step} screenshots saved to: ${SCREENSHOTS}`);
  console.log(
    "\nTo create a GIF, install ImageMagick and run:\n" +
      `  convert -delay 150 -loop 0 ${SCREENSHOTS}/*.png demo/hitl-demo.gif`,
  );
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
