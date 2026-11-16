// Standalone local browser check. No npm/Python dependency changes are needed.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE_PATH || 'playwright');

(async () => {
  const base = process.argv[2] || 'http://127.0.0.1:8765';
  const output = process.argv[3] || 'tests/screenshots/issue16';
  const origin = new URL(base);
  assert(['127.0.0.1', 'localhost'].includes(origin.hostname), 'Use the local fixture server');
  fs.mkdirSync(output, { recursive: true });
  const browser = await chromium.launch({
    headless: true,
    executablePath: process.env.CHROMIUM_EXECUTABLE_PATH,
    args: ['--no-sandbox'],
  });
  try {
    const context = await browser.newContext({ viewport: { width: 390, height: 844 },
      isMobile: true, hasTouch: true, deviceScaleFactor: 1 });
    const external = [];
    await context.route('**/*', route => {
      if (new URL(route.request().url()).origin !== origin.origin) {
        external.push(route.request().url());
        return route.abort();
      }
      return route.continue();
    });
    const page = await context.newPage();
    const noOverflow = async () => {
      assert.equal(await page.evaluate(() => window.innerWidth), 390);
      assert.equal(await page.evaluate(() => window.innerHeight), 844);
      assert(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth),
        'Horizontal page scrolling at 390×844');
    };
    await page.goto(base);
    await page.getByRole('searchbox').fill('corp');
    await page.getByRole('button', { name: 'Research' }).tap();
    await page.waitForURL('**/?q=corp');
    const matches = page.locator('.directory-results li');
    assert(await matches.count() > 1 && await matches.count() <= 20);
    await noOverflow();
    await page.screenshot({ path: path.join(output, 'search-390x844.png') });
    await page.getByRole('link', { name: /NVIDIA CORP/ }).tap();
    await page.waitForURL('**/companies/CIK0001045810');
    assert(await page.getByRole('heading', { name: 'Not yet ingested' }).isVisible());
    assert((await page.locator('.command').textContent()).includes('--ticker NVDA'));
    assert((await page.locator('.listing-provenance').textContent()).includes('source document 1'));
    assert.equal(await page.locator('table').count(), 0);
    await noOverflow();
    await page.screenshot({ path: path.join(output, 'not-ingested-390x844.png') });
    await page.goto(base);
    await page.getByRole('searchbox').fill('aapl');
    await page.getByRole('button', { name: 'Research' }).tap();
    await page.waitForURL('**/companies/CIK0000320193');
    assert(await page.getByRole('heading', { name: 'Income statement', exact: true }).isVisible());
    // Table layout is outside #16; do not change or assert it here.
    assert.deepEqual(external, []);
    console.log('PASS: ticker/name search, CIK links, not-ingested command/provenance, exact redirect');
    console.log('PASS: 390×844, no horizontal scroll on both new pages, touch, zero external requests');
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exit(1); });
