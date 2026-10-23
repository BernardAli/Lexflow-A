/* Browser part of record_demo_video.py. All case mutations occur on a new,
 * disposable synthetic office created by that launcher.
 */
const { chromium } = require(process.env.LEXFLOW_PLAYWRIGHT_MODULE || 'playwright');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const runtime = process.env.LEXFLOW_VIDEO_RUNTIME;
const base = process.env.LEXFLOW_DEMO_URL;
assert(runtime && base, 'Use tools/record_demo_video.py to launch an isolated recording.');
const definitions = JSON.parse(fs.readFileSync(path.join(runtime, 'scenes.json'), 'utf8'));
const quick = process.env.LEXFLOW_VIDEO_CHECK_ONLY === '1';

(async () => {
  const browser = await chromium.launch({ headless: true,
    executablePath: process.env.LEXFLOW_CHROMIUM_EXECUTABLE || undefined, args: ['--no-sandbox'] });
  let context;
  try {
    async function signIn(username) {
      const auth = await browser.newContext();
      const login = await auth.newPage();
      await login.goto(base + '/login/');
      await login.getByLabel('Username').fill(username);
      await login.getByLabel('Password', { exact: true }).fill('lexflow-demo-2026');
      await Promise.all([login.waitForURL(base + '/'), login.getByRole('button', { name: 'Sign in', exact: true }).click()]);
      const state = await auth.storageState();
      await auth.close();
      return state;
    }
    const clerk = await signIn('keller.clerk');
    const notary = await signIn('keller.notar');
    context = await browser.newContext({ viewport: { width: 1600, height: 800 },
      recordVideo: { dir: path.join(runtime, 'raw'), size: { width: 1600, height: 800 } },
      storageState: clerk, reducedMotion: 'reduce', locale: 'en-GB' });
    await context.route('**/*', route => {
      const url = route.request().url();
      return url.startsWith(base + '/') || url.startsWith('data:') || url === 'about:blank' ? route.continue() : route.abort();
    });
    await context.addInitScript(() => {
      document.addEventListener('DOMContentLoaded', () => {
        const dot = document.createElement('div');
        dot.style.cssText = 'position:fixed;left:-100px;top:-100px;width:22px;height:22px;border:2px solid #bc8c25;border-radius:50%;background:#f4e4a144;pointer-events:none;z-index:2147483647;transform:translate(-50%,-50%);box-shadow:0 0 0 5px #f4e4a11a;';
        document.body.append(dot);
        document.addEventListener('mousemove', event => { dot.style.left = event.clientX + 'px'; dot.style.top = event.clientY + 'px'; });
        document.addEventListener('mousedown', () => dot.animate([{ scale: 1 }, { scale: 1.7 }, { scale: 1 }], { duration: 450 }));
      });
    });
    const origin = Date.now();
    const page = await context.newPage();
    page.setDefaultTimeout(quick ? 10000 : 20000);
    const video = page.video();
    const scenes = [], errors = [];
    const checks = {};
    page.on('pageerror', error => errors.push(error.message));
    page.on('response', response => { if (response.status() >= 500) errors.push(response.status() + ' ' + response.url()); });
    const now = () => (Date.now() - origin) / 1000;
    const pause = seconds => page.waitForTimeout(quick ? 120 : seconds * 1000);
    async function stage(identifier, action) {
      const scene = definitions.find(entry => entry.id === identifier);
      assert(scene, identifier);
      console.log('Recording: ' + scene.chapter);
      const start = now();
      await action();
      const remaining = (quick ? .3 : scene.seconds) - (now() - start);
      if (remaining > 0) await page.waitForTimeout(remaining * 1000);
      scenes.push({ ...scene, start, end: now() });
    }
    async function go(url) {
      await page.goto(url.startsWith('http') ? url : base + url, { waitUntil: 'networkidle' });
      await page.evaluate(() => document.fonts.ready);
    }
    async function frame(locator, align = 'center') {
      await locator.evaluate((element, block) => element.scrollIntoView({ behavior: 'smooth', block }), align);
      await pause(.45);
      if (align === 'start') await page.evaluate(() => window.scrollBy(0, -90));
    }
    async function point(locator) {
      const box = await locator.boundingBox();
      assert(box, 'A recording target needs a visible rectangle');
      await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2, { steps: quick ? 1 : 28 });
      await pause(.25);
    }
    async function click(locator, { navigate = false } = {}) {
      await frame(locator);
      await point(locator);
      if (navigate) await Promise.all([page.waitForNavigation(), locator.click()]);
      else await locator.click();
    }
    async function titleCard(final = false) {
      await page.setContent(`<!doctype html><html lang="en"><head><meta charset="utf-8"><style>
        *{box-sizing:border-box}body{margin:0;background:#f3f5f1;color:#142d2c;font-family:Arial,Helvetica,sans-serif;height:100vh;padding:82px 100px;display:flex;flex-direction:column;justify-content:center}
        .brand{font-size:22px;letter-spacing:5px;font-weight:700;color:#1e5250;margin-bottom:32px}.rule{height:5px;width:92px;background:#c29a43;margin-bottom:26px}
        h1{font-size:72px;line-height:1.12;letter-spacing:-3px;margin:0 0 24px;max-width:1250px}p{font-size:25px;line-height:1.5;color:#526761;margin:0;max-width:1130px}
        .tags{display:flex;gap:14px;margin-top:34px}.tags span{border:1px solid #c6d4cc;padding:13px 20px;border-radius:7px;color:#1e5250;font-size:18px}
        .note{font-size:17px;margin-top:42px;color:#526761}
      </style></head><body><div class="brand">LEXFLOW AI</div><div class="rule"></div>
      <h1>${final ? 'Receive. Review. Approve.<br>Verify. Measure.' : 'From documents<br>to a verified handoff.'}</h1>
      <p>${final ? 'A controlled workflow for the first dedicated-office pilot.' : 'A narrated walkthrough of property-purchase intake, source review, human approval and office analytics.'}</p>
      <div class="tags"><span>Source-linked review</span><span>Frozen approvals</span><span>Verified receipts</span><span>Office analytics</span></div>
      <div class="note">Fictional cases only · Independent reference destination · Native plugins and vendor adapters remain planned</div></body></html>`);
    }
    let matterUrl;
    await stage('intro', () => titleCard());
    await stage('overview', async () => {
      await go('/#overview-queue');
      await page.evaluate(() => scrollTo(0, 0));
      assert(await page.getByRole('heading', { name: 'Overview', exact: true }).isVisible());
      await point(page.getByRole('link', { name: 'New intake', exact: true }));
    });
    await stage('upload', async () => {
      await click(page.getByRole('link', { name: 'New intake', exact: true }), { navigate: true });
      await page.getByLabel('Sender name', { exact: true }).fill('Anna Schmidt');
      await page.getByLabel('Sender email', { exact: true }).fill('anna@example.test');
      await page.getByLabel('Subject', { exact: true }).pressSequentially('Kaufvertrag - synthetic video demonstration', { delay: quick ? 0 : 35 });
      await page.getByLabel('Email text', { exact: true }).fill('Please review the attached buyer questionnaire, register extract and draft contract. Fictional sample documents.');
      await page.locator('#files').setInputFiles(['01_Kaeuferfragebogen.pdf', '02_Grundbuchauszug.pdf', '03_Kaufvertragsentwurf.pdf']
        .map(name => path.resolve('presentation/demo-documents/property-conflict', name)));
      await page.locator('#source').selectOption('upload');
      await frame(page.getByRole('button', { name: 'Add to inbox', exact: true }));
    });
    await stage('process', async () => {
      await click(page.getByRole('button', { name: 'Add to inbox', exact: true }), { navigate: true });
      await page.getByRole('heading', { name: 'Transfer blocked', exact: true }).waitFor();
      matterUrl = page.url();
      assert(await page.locator('#field-grundbuch .lf-candidate').count() >= 2);
      await frame(page.locator('#transfer'), 'start');
      checks.uploaded_three_pdfs = true;
      checks.conflict_blocks_approval = true;
    });
    await stage('evidence', async () => {
      await click(page.locator('#field-grundbuch .lf-field-value'));
      assert(await page.locator('mark.is-focus:visible').count() > 0);
      await frame(page.locator('#field-grundbuch'), 'start');
      checks.source_highlighting = true;
    });
    await stage('resolve', async () => {
      await click(page.locator('#field-grundbuch .lf-candidate').filter({ hasText: 'Grundbuchauszug.pdf' }).getByRole('button', { name: 'Use this', exact: true }), { navigate: true });
      assert.equal(await page.locator('#field-grundbuch .lf-candidate').count(), 0);
      await frame(page.locator('#field-grundbuch'));
    });
    await stage('confirm', async () => {
      await click(page.locator('#field-purchase_price').getByRole('button', { name: 'Confirm', exact: true }), { navigate: true });
      await page.getByRole('heading', { name: 'Ready for approval', exact: true }).waitFor();
      assert.equal(await page.getByRole('button', { name: 'Approve for transfer', exact: true }).count(), 0);
      await frame(page.locator('#transfer'), 'start');
      checks.clerk_cannot_approve = true;
    });
    await stage('notary', async () => {
      await context.clearCookies();
      await context.addCookies(notary.cookies);
      await go(matterUrl);
      await page.evaluate(() => scrollTo(0, 0));
      assert(await page.getByRole('button', { name: 'Approve for transfer', exact: true }).isEnabled());
    });
    await stage('approve', async () => {
      await click(page.getByRole('button', { name: 'Approve for transfer', exact: true }), { navigate: true });
      await page.getByRole('heading', { name: /^Approved by/ }).waitFor();
      assert.equal(await page.getByRole('button', { name: 'Edit value', exact: true }).count(), 0);
      await frame(page.locator('#transfer'), 'start');
      checks.notary_approval_and_frozen_ui = true;
    });
    await stage('export', async () => {
      await page.getByLabel('Connector', { exact: true }).selectOption({ label: 'HTTP reference receiver' });
      await click(page.getByRole('button', { name: 'Create approved export', exact: true }), { navigate: true });
      await page.getByText('Export created — receipt required', { exact: true }).waitFor();
      await frame(page.locator('#transfer'), 'start');
      checks.export_awaits_receipt = true;
    });
    await stage('send', async () => {
      await click(page.getByRole('button', { name: 'Send to HTTP destination', exact: true }), { navigate: true });
      await page.getByText('Delivered', { exact: true }).waitFor();
      await frame(page.locator('#transfer'), 'start');
    });
    await stage('receipt', async () => {
      assert(await page.getByText('Delivered', { exact: true }).isVisible());
      await point(page.getByText('Delivered', { exact: true }));
      checks.actual_http_signed_receipt = true;
    });
    await stage('audit', async () => {
      const ref = new URL(matterUrl).pathname.split('/').filter(Boolean).at(-1);
      await go('/audit/?q=' + encodeURIComponent(ref));
      assert((await page.locator('.lf-chain').textContent()).includes('Chain verified'));
      assert((await page.locator('main').textContent()).includes('matter.approved'));
      await page.evaluate(() => scrollTo(0, 0));
      checks.audit_chain_verified = true;
    });
    await stage('scan', async () => {
      await go('/presentation/');
      const link = page.getByRole('link', { name: 'Open the scanned property purchase', exact: true });
      assert.equal(await link.count(), 1, 'Enable local OCR for the narrated scan scene.');
      await go(await link.getAttribute('href'));
      await frame(page.getByRole('checkbox', { name: 'I read the original and checked its values against the matter.', exact: true }));
      assert(await page.getByRole('button', { name: 'Approve for transfer', exact: true }).isDisabled());
      checks.scan_requires_original_acknowledgement = true;
    });
    await stage('analytics', async () => {
      await go('/#analytics-summary');
      await page.evaluate(() => scrollTo(0, 0));
      assert.equal(await page.getByLabel('Data cohort', { exact: true }).inputValue(), 'demo');
      await pause(2);
      await frame(page.locator('.lf-analytics-kpis'));
      assert.equal(await page.locator('[data-kpi]').count(), 6);
      checks.synthetic_analytics = true;
    });
    for (const [identifier, tab, panel] of [
      ['quality', 'Extraction and review', 'analytics-quality'],
      ['workload', 'Workload and timing', 'analytics-workload'],
      ['delivery', 'Delivery and audit', 'analytics-delivery'],
    ]) {
      await stage(identifier, async () => {
        await click(page.getByRole('tab', { name: tab, exact: true }));
        await frame(page.locator('#' + panel), 'start');
        await page.waitForFunction(id => Array.from(document.querySelectorAll('#' + id + ' [data-analytics-chart]'))
          .every(element => echarts.getInstanceByDom(element)?.getWidth() > 100), panel);
      });
    }
    checks.all_four_analytics_tabs = true;
    await stage('roadmap', async () => {
      await go('/presentation/#demo-scope');
      await frame(page.getByRole('heading', { name: 'Plugin and destination expansion', exact: true }), 'start');
      assert(await page.getByText('Planned work starts with integration setup', { exact: false }).isVisible());
    });
    await stage('outro', () => titleCard(true));
    assert.deepEqual(errors, []);
    assert.equal(scenes.length, definitions.length);
    await context.close();
    context = null;
    await video.saveAs(path.join(runtime, 'capture.webm'));
    fs.writeFileSync(path.join(runtime, 'timeline.json'), JSON.stringify({ scenes, checks, browser_errors: errors }, null, 2));
    console.log('All recording scenes and workflow checks completed.');
  } finally {
    if (context) await context.close();
    await browser.close();
  }
})().catch(error => { console.error(error.stack); process.exit(1); });
