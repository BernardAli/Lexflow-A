/* Run against a synthetic demo. Set LEXFLOW_TABS_MUTATION_CHECK=1 only for an
 * isolated copy: that optional check saves an illustrative office baseline.
 */
const { chromium } = require(process.env.LEXFLOW_PLAYWRIGHT_MODULE || 'playwright');
const fs = require('node:fs');
const assert = require('node:assert/strict');
const base = process.env.LEXFLOW_DEMO_URL || 'http://127.0.0.1:8010';

(async () => {
  const browser = await chromium.launch({ headless: true,
    executablePath: process.env.LEXFLOW_CHROMIUM_EXECUTABLE || undefined, args: ['--no-sandbox'] });
  try {
    const context = await browser.newContext({ viewport: { width: 1440, height: 1050 } });
    await context.route('**/*', route => route.request().url().startsWith(base + '/') ? route.continue() : route.abort());
    const page = await context.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    page.on('response', response => { if (response.status() >= 500) errors.push(response.status() + ' ' + response.url()); });
    await page.goto(base + '/login/');
    await page.getByLabel('Username').fill('admin');
    await page.getByLabel('Password', { exact: true }).fill('lexflow-demo-2026');
    await Promise.all([page.waitForURL(base + '/'), page.getByRole('button', { name: 'Sign in', exact: true }).click()]);

    const cases = [
      ['/', 'overview', 2], ['/studio/', 'studio', 2],
      ['/studio/property-purchase/', 'workflow-rules', 2],
      ['/connectors/', 'connectors', 2], ['/security/', 'security', 4],
      ['/presentation/', 'presentation', 2],
    ];
    for (const [url, name, count] of cases) {
      await page.goto(base + url);
      const group = page.locator('[data-tabs="' + name + '"]');
      const tabs = group.locator(':scope > [data-tab-list]').getByRole('tab');
      assert.equal(await tabs.count(), count);
      for (let index = 0; index < count; index++) {
        const tab = tabs.nth(index);
        await tab.click();
        assert.equal(await tab.getAttribute('aria-selected'), 'true');
        const panel = page.locator('#' + await tab.getAttribute('aria-controls'));
        assert(await panel.isVisible());
        assert.equal(await group.evaluate(element => Array.from(element.querySelectorAll('[role="tabpanel"]'))
          .filter(panel => panel.closest('[data-tabs]') === element && !panel.hidden).length), 1);
        assert.equal(await panel.getAttribute('aria-labelledby'), await tab.getAttribute('id'));
      }
      await tabs.first().focus();
      await tabs.first().press('End');
      assert.equal(await tabs.last().getAttribute('aria-selected'), 'true');
      await tabs.last().press('Home');
      assert.equal(await tabs.first().getAttribute('aria-selected'), 'true');
      await tabs.first().press('ArrowLeft');
      assert.equal(await tabs.last().getAttribute('aria-selected'), 'true');
      await tabs.last().press('ArrowRight');
      assert.equal(await tabs.first().getAttribute('aria-selected'), 'true');
      await tabs.last().click();
      await page.goto(base + url); // Return after a redirect without a fragment.
      assert.equal(await page.locator('[data-tabs="' + name + '"] > [data-tab-list]').getByRole('tab').last().getAttribute('aria-selected'), 'true');
    }

    await page.goto(base + '/#analytics-summary');
    await page.waitForFunction(() => ['chart-daily', 'chart-conf'].every(id => {
      const element = document.getElementById(id);
      return element && echarts.getInstanceByDom(element)?.getWidth() > 200;
    }));
    const charts = await page.evaluate(() => ['chart-daily', 'chart-conf'].map(id => {
      const chart = echarts.getInstanceByDom(document.getElementById(id));
      return chart && chart.getWidth();
    }));
    assert(charts.every(width => width > 200), 'Charts need a visible, measured container');
    await page.getByRole('link', { name: 'Edit baseline', exact: true }).click();
    assert.equal(await page.getByRole('tab', { name: 'Pilot baseline', exact: true }).getAttribute('aria-selected'), 'true');
    const original = await page.locator('#ret').inputValue();
    const baseline = await page.locator('#b1').inputValue();
    const errorsBaseline = await page.locator('#b2').inputValue();
    const auditBaseline = await page.locator('#b3').inputValue();
    await page.locator('#b1').fill(String(Number(baseline) + 1));
    await page.getByRole('tab', { name: 'Data protection', exact: true }).click();
    await page.locator('#ret').fill('5');
    await page.getByRole('tab', { name: 'Pilot baseline', exact: true }).click();
    await page.getByRole('button', { name: 'Save security settings', exact: true }).filter({ visible: true }).click();
    assert.equal(await page.getByRole('tab', { name: 'Data protection', exact: true }).getAttribute('aria-selected'), 'true');
    assert(await page.locator('#ret').evaluate(element => element === document.activeElement));
    await page.locator('#ret').fill(original);
    await page.getByRole('tab', { name: 'Pilot baseline', exact: true }).click();
    assert.equal(await page.locator('#b1').inputValue(), String(Number(baseline) + 1));
    if (process.env.LEXFLOW_TABS_MUTATION_CHECK === '1') {
      await page.getByRole('button', { name: 'Save security settings', exact: true }).filter({ visible: true }).click();
      await page.waitForLoadState();
      assert.equal(Number(await page.locator('#b1').inputValue()), Number(baseline) + 1);
      assert.equal(await page.locator('#ret').inputValue(), original);
      assert.equal(Number(await page.locator('#b2').inputValue()), Number(errorsBaseline));
      assert.equal(Number(await page.locator('#b3').inputValue()), Number(auditBaseline));
      assert.equal(await page.getByRole('tab', { name: 'Pilot baseline', exact: true }).getAttribute('aria-selected'), 'true');
    }
    await page.screenshot({ path: 'presentation/screenshots/11-security-tabs.png', fullPage: true });

    await page.goto(base + '/studio/property-purchase/#documents');
    assert.equal(await page.getByRole('tab', { name: /^Document types/ }).getAttribute('aria-selected'), 'true');
    await page.goto(base + '/studio/property-purchase/#f-buyer_name');
    assert.equal(await page.getByRole('tab', { name: /^Fields and rules/ }).getAttribute('aria-selected'), 'true');
    await page.goto(base + '/presentation/#demo-walkthrough');
    const matterUrl = await page.getByRole('link', { name: 'Open the property conflict', exact: true }).getAttribute('href');
    await page.goto(base + matterUrl);
    const sourceTabs = page.getByRole('tablist', { name: 'Source documents', exact: true });
    assert(await sourceTabs.getByRole('tab').count() > 1);
    await sourceTabs.getByRole('tab').first().focus();
    await sourceTabs.getByRole('tab').first().press('ArrowRight');
    assert.equal(await sourceTabs.getByRole('tab').nth(1).getAttribute('aria-selected'), 'true');
    const mark = page.locator('.lf-sheet mark[data-field]').first();
    const sourceId = await mark.evaluate(element => element.closest('[data-tab-panel]').id);
    await sourceTabs.locator('[aria-controls="' + sourceId + '"]').click();
    await page.getByRole('tab', { name: 'Activity', exact: true }).click();
    await mark.click();
    assert.equal(await page.getByRole('tab', { name: /^Extracted data/ }).getAttribute('aria-selected'), 'true');
    assert(new URL(page.url()).hash.startsWith('#field-'));
    await page.getByRole('tab', { name: 'Activity', exact: true }).click();
    await page.goto(base + matterUrl + '#field-grundbuch');
    assert(await page.locator('#field-grundbuch').isVisible());
    assert.equal(await page.getByRole('tab', { name: /^Extracted data/ }).getAttribute('aria-selected'), 'true');
    await page.locator('#field-grundbuch .lf-field-value').click();
    assert(await page.locator('mark.is-focus:visible').count() > 0);

    const mobile = [];
    await page.setViewportSize({ width: 390, height: 844 });
    for (const [url, name] of [...cases, [matterUrl, 'matter-details']]) {
      await page.goto(base + url);
      const tabs = page.locator('[data-tabs="' + name + '"] > [data-tab-list]').getByRole('tab');
      for (let index = 0; index < await tabs.count(); index++) {
        await tabs.nth(index).click();
        const size = await page.evaluate(() => ({ width: innerWidth, content: document.documentElement.scrollWidth }));
        if (size.content > size.width + 2) {
          const overflow = await page.evaluate(() => Array.from(document.querySelectorAll('main *')).filter(element => {
            const rect = element.getBoundingClientRect();
            if (!rect.width || rect.right <= innerWidth + 1) return false;
            for (let parent = element.parentElement; parent && parent.tagName !== 'BODY'; parent = parent.parentElement) {
              if (['auto', 'scroll', 'hidden'].includes(getComputedStyle(parent).overflowX)) return false;
            }
            return true;
          }).slice(0, 8).map(element => ({ tag: element.tagName, className: element.className, right: element.getBoundingClientRect().right })));
          assert.fail(url + ' mobile overflow: ' + JSON.stringify({ ...size, panel: await tabs.nth(index).getAttribute('aria-controls'), overflow }));
        }
      }
      mobile.push(url);
    }
    await page.goto(base + '/security/#security-people');
    await page.screenshot({ path: 'presentation/screenshots/12-tabs-mobile.png', fullPage: true });

    const fallback = await browser.newContext({ javaScriptEnabled: false, storageState: await context.storageState() });
    const plain = await fallback.newPage();
    for (const [url] of [...cases, [matterUrl]]) {
      await plain.goto(base + url);
      assert.equal(await plain.locator('[data-tab-panel][hidden]').count(), 0);
      assert(await plain.locator('[data-tab-panel]').count() > 0);
    }
    assert.deepEqual(errors, []);
    const result = { passed: true, sections: cases.map(item => item[1]).concat('matter-details', 'matter-documents'),
      keyboard: true, deepLinks: true, rememberedSelection: true, draftValuesPreserved: true,
      hiddenInvalidFieldRevealed: true, settingsSave: process.env.LEXFLOW_TABS_MUTATION_CHECK === '1',
      chartsVisible: true, sourceAndFieldLinks: true, mobilePages: mobile.length, noJavaScriptFallback: true, browserErrors: errors };
    fs.writeFileSync('presentation/tabs-browser-verification.json', JSON.stringify(result, null, 2) + '\n');
    console.log(JSON.stringify(result));
  } finally { await browser.close(); }
})().catch(error => { console.error(error.stack); process.exit(1); });
