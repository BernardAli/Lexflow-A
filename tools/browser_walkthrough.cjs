/* Exercise the synthetic demo through its public UI. Uses an installed Playwright
 * package via LEXFLOW_PLAYWRIGHT_MODULE, or a normal local playwright dependency.
 * Start a freshly prepared demo server first. All requests stay on localhost.
 */
const { chromium } = require(process.env.LEXFLOW_PLAYWRIGHT_MODULE || 'playwright');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const base = process.env.LEXFLOW_DEMO_URL || 'http://127.0.0.1:8010';
const output = path.resolve('presentation/screenshots');
fs.mkdirSync(output, { recursive: true });
(async () => {
  const browser = await chromium.launch({ headless: true,
    executablePath: process.env.LEXFLOW_CHROMIUM_EXECUTABLE || undefined,
    args: ['--no-sandbox'] });
  const page = await browser.newPage({ viewport: { width: 1440, height: 1050 } });
  await page.route('**/*', route => {
    const url = route.request().url();
    if (url.startsWith(base + '/') || url.startsWith('data:')) route.continue();
    else route.abort();
  });
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('response', response => { if (response.status() >= 500) errors.push(response.status() + ' ' + response.url()); });
  async function login(user) {
    await page.goto(base + '/login/');
    await page.getByLabel('Username').fill(user);
    await page.getByLabel('Password', { exact: true }).fill('lexflow-demo-2026');
    await Promise.all([page.waitForURL(base + '/'), page.getByRole('button', { name: 'Sign in', exact: true }).click()]);
  }
  await login('keller.clerk');
  await page.goto(base + '/presentation/');
  await page.screenshot({ path: path.join(output, '00-presenter-guide.png'), fullPage: true });
  await page.getByRole('link', { name: 'Open the property conflict', exact: true }).click();
  const matterUrl = page.url();
  await page.getByRole('heading', { name: 'Transfer blocked', exact: true }).waitFor({ state: 'visible' });
  await page.locator('#field-grundbuch .lf-field-value').click();
  assert(await page.locator('mark.is-focus').count() > 0);
  await page.screenshot({ path: path.join(output, '01-conflict.png'), fullPage: true });
  await page.screenshot({ path: path.join(output, '01-conflict-viewport.png'), fullPage: false });
  await page.locator('#field-grundbuch .lf-candidate').filter({ hasText: 'Grundbuchauszug.pdf' }).getByRole('button', { name: 'Use this', exact: true }).click();
  await page.locator('#field-purchase_price').getByRole('button', { name: 'Confirm', exact: true }).click();
  await page.getByRole('heading', { name: 'Ready for approval', exact: true }).waitFor({ state: 'visible' });
  assert.equal(await page.getByRole('button', { name: 'Approve for transfer', exact: true }).count(), 0);
  await page.locator('.lf-user .dropdown > button').click();
  await page.getByRole('button', { name: 'Sign out', exact: true }).click();
  await login('keller.notar');
  await page.goto(matterUrl);
  await page.getByRole('button', { name: 'Approve for transfer', exact: true }).click();
  assert.equal(await page.getByRole('button', { name: 'Edit value', exact: true }).count(), 0);
  await page.screenshot({ path: path.join(output, '02-approved.png'), fullPage: true });
  await page.getByRole('button', { name: 'Create approved export', exact: true }).click();
  await page.getByText('Awaiting destination receipt', { exact: true }).waitFor({ state: 'visible' });
  await page.getByRole('button', { name: 'Run demo receiver and verify receipt', exact: true }).click();
  await page.getByText('Delivered', { exact: true }).waitFor({ state: 'visible' });
  await page.getByRole('tab', { name: 'Activity', exact: true }).click();
  const activityWidth = (await page.locator('.lf-timeline li > span').first().boundingBox()).width;
  assert(activityWidth > 250, 'Activity text must use the available column width');
  await page.getByRole('tab', { name: /^Extracted data/ }).click();
  await page.screenshot({ path: path.join(output, '03-receipt.png'), fullPage: true });
  await page.evaluate(() => scrollTo(0, 0));
  await page.screenshot({ path: path.join(output, '03-receipt-viewport.png'), fullPage: false });
  const extended = { httpReceipt: false, scanReview: false };
  if (await page.locator('#connector option').filter({ hasText: 'HTTP reference receiver' }).count()) {
    await page.locator('#connector').selectOption({ label: 'HTTP reference receiver' });
    await page.getByRole('button', { name: 'Get approved export', exact: true }).click();
    await page.getByRole('button', { name: 'Send to HTTP destination', exact: true }).click();
    assert.equal(await page.getByText('Delivered', { exact: true }).count(), 2);
    await page.screenshot({ path: path.join(output, '06-http-receipt.png'), fullPage: true });
    extended.httpReceipt = true;
  }
  await page.goto(base + '/presentation/');
  if (await page.getByRole('link', { name: 'Open the scanned property purchase', exact: true }).count()) {
    await page.getByRole('link', { name: 'Open the scanned property purchase', exact: true }).click();
    await page.getByRole('heading', { name: 'Transfer blocked', exact: true }).waitFor({ state: 'visible' });
    assert((await page.locator('.lf-sheet-text').textContent()).includes('Maria Beispiel'));
    await page.screenshot({ path: path.join(output, '07-scan-review.png'), fullPage: true });
    await page.getByRole('checkbox', { name: 'I read the original and checked its values against the matter.', exact: true }).check();
    await page.getByRole('button', { name: 'Record source review', exact: true }).click();
    await page.locator('#field-purchase_price').getByRole('button', { name: 'Confirm', exact: true }).click();
    await page.getByRole('heading', { name: 'Ready for approval', exact: true }).waitFor({ state: 'visible' });
    extended.scanReview = true;
  }
  if (process.env.LEXFLOW_EXTENDED_DEMO === '1') assert.deepEqual(extended, { httpReceipt: true, scanReview: true });
  await page.goto(base + '/audit/');
  assert((await page.locator('.lf-chain').textContent()).includes('Chain verified'));
  await page.screenshot({ path: path.join(output, '04-audit.png'), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(base + '/presentation/');
  const mobile = await page.evaluate(() => ({ width: innerWidth, content: document.documentElement.scrollWidth }));
  assert(mobile.content <= mobile.width + 2, JSON.stringify(mobile));
  await page.screenshot({ path: path.join(output, '05-mobile-guide.png'), fullPage: true });
  assert.deepEqual(errors, []);
  const result = { passed: true, workflow: ['login', 'source highlighting', 'blocked conflict', 'clerk review', 'role restriction',
      'notary approval', 'frozen UI', 'file export awaiting receipt', 'checked demo receipt', 'verified audit', 'readable activity layout', 'mobile guide'],
      browserErrors: errors, mobile, extended, syntheticDataOnly: true };
  fs.writeFileSync('presentation/browser-verification.json', JSON.stringify(result, null, 2));
  console.log(JSON.stringify(result));
  await browser.close();
})().catch(error => { console.error(error.stack); process.exit(1); });
