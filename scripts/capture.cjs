// Optional browser QA: install Playwright separately; the application needs no Node runtime.
const { chromium } = require('playwright');
const fs = require('node:fs');
const path = require('node:path');

(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1140 }, deviceScaleFactor: 1 });
    const errors = [];
    page.on('pageerror', e => errors.push(e.message));
    await page.goto(process.env.QUARRY_URL || 'http://127.0.0.1:18765', { waitUntil: 'networkidle' });
    await page.getByRole('button', { name: 'Search catalogue' }).click();
    await page.locator('#output').waitFor({ state: 'visible' });
    if (await page.locator('.product').count() !== 3) throw new Error('Expected three real product recommendations');
    const root = path.resolve(__dirname, '../docs/screenshots');
    await page.screenshot({ path: path.join(root, 'search.png'), fullPage: true });
    await page.selectOption('#mode', 'parallel');
    await page.selectOption('#workers', '2');
    const response = page.waitForResponse(r => r.url().endsWith('/search') && r.request().method() === 'POST');
    await page.getByRole('button', { name: 'Search catalogue' }).click();
    const data = await (await response).json();
    await page.locator('#output').waitFor({ state: 'visible' });
    if (data.execution.actual_mode !== 'parallel' || data.execution.worker_pids.length !== 2) throw new Error('Parallel request did not use two workers');
    if (!data.recommendations.every(r => Number(r.product.price) <= 5000 && r.product.available)) throw new Error('Eligibility mismatch');
    await page.screenshot({ path: path.join(root, 'workers.png'), fullPage: true });
    await page.setViewportSize({ width: 390, height: 844 });
    if (await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)) throw new Error('Mobile page overflows horizontally');
    if (errors.length) throw new Error(errors.join('\n'));
    console.log(JSON.stringify({ screenshots: ['search.png','workers.png'], viewport: '1440x1140', browser: await browser.version(), consoleErrors: errors, mobileOverflow: false, actualMode: data.execution.actual_mode, workerCount: data.execution.worker_count }));
  } finally { await browser.close(); }
})().catch(e => { console.error(e.message); process.exitCode = 1; });
