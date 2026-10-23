/* Read-only analytics checks against a synthetic demo. The existing workflow
 * walkthrough may be run afterwards on an isolated copy to check mutations.
 */
const { chromium } = require(process.env.LEXFLOW_PLAYWRIGHT_MODULE || 'playwright');
const fs = require('node:fs');
const assert = require('node:assert/strict');
const base = process.env.LEXFLOW_DEMO_URL || 'http://127.0.0.1:8010';

(async () => {
  const browser = await chromium.launch({ headless: true,
    executablePath: process.env.LEXFLOW_CHROMIUM_EXECUTABLE || undefined, args: ['--no-sandbox'] });
  try {
    const context = await browser.newContext({ viewport: { width: 1440, height: 1050 }, reducedMotion: 'reduce' });
    await context.route('**/*', route => route.request().url().startsWith(base + '/') ? route.continue() : route.abort());
    const page = await context.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    page.on('response', response => { if (response.status() >= 500) errors.push(response.status() + ' ' + response.url()); });
    await page.goto(base + '/login/');
    await page.getByLabel('Username').fill('admin');
    await page.getByLabel('Password', { exact: true }).fill('lexflow-demo-2026');
    await Promise.all([page.waitForURL(base + '/'), page.getByRole('button', { name: 'Sign in', exact: true }).click()]);

    const sections = ['analytics-summary', 'analytics-quality', 'analytics-workload', 'analytics-delivery'];
    const selected = async id => assert.equal(await page.locator('[aria-controls="' + id + '"]').getAttribute('aria-selected'), 'true');
    const intakeCount = async () => Number((await page.locator('[data-kpi="intakes"] .lf-analytics-value').innerText()).replaceAll(',', ''));
    async function chartsReady() {
      await page.waitForFunction(() => Array.from(document.querySelectorAll('[data-analytics-chart]'))
        .filter(element => element.offsetWidth && !element.closest('[hidden]'))
        .every(element => echarts.getInstanceByDom(element)?.getWidth() > 100));
    }
    async function noOverflow() {
      const size = await page.evaluate(() => ({ width: innerWidth, content: document.documentElement.scrollWidth }));
      assert(size.content <= size.width + 2, 'Horizontal page overflow: ' + JSON.stringify(size) + ' at ' + page.url());
    }
    async function applyFilters() {
      await Promise.all([page.waitForNavigation(), page.getByRole('button', { name: 'Apply filters', exact: true }).click()]);
      await selected('overview-analytics');
    }

    await page.goto(base + '/#analytics-summary');
    await selected('overview-analytics');
    assert.equal(await page.locator('[data-kpi]').count(), 6);
    assert.equal(await page.getByLabel('Data cohort').inputValue(), 'demo');
    assert(await page.getByText('Values are illustrative;', { exact: false }).isVisible());
    const total = await intakeCount();
    assert(total > 25, 'Use the extended synthetic demo to exercise pagination');
    await chartsReady();
    fs.mkdirSync('presentation/screenshots', { recursive: true });
    await page.evaluate(() => scrollTo(0, 0));
    await page.screenshot({ path: 'presentation/screenshots/13-analytics-summary.png', fullPage: true });

    for (const id of sections) {
      await page.locator('[data-tab][href="#' + id + '"]').click();
      await selected(id);
      await selected('overview-analytics');
      await chartsReady();
      await noOverflow();
    }
    const initialized = await page.evaluate(() => Array.from(document.querySelectorAll('[data-analytics-chart]'))
      .filter(element => echarts.getInstanceByDom(element)).length);
    assert.equal(initialized, 7);
    const tabs = page.getByRole('tablist', { name: 'Analytics sections', exact: true }).getByRole('tab');
    await tabs.first().focus();
    await tabs.first().press('End');
    await selected('analytics-delivery');
    await tabs.last().press('ArrowRight');
    await selected('analytics-summary');
    await page.locator('#analytics-summary details').first().getByText('View chart data', { exact: true }).click();
    assert(await page.locator('#analytics-summary details').first().locator('table').isVisible());

    const href = await page.getByRole('link', { name: 'Download analytics CSV', exact: true }).getAttribute('href');
    assert(href.startsWith('/analytics/export/?'));
    const csv = await context.request.get(base + href);
    assert.equal(csv.status(), 200);
    assert(csv.headers()['cache-control'].includes('no-store'));
    assert(csv.headers()['content-disposition'].includes('.csv'));
    const exported = await csv.text();
    assert(exported.includes('Section,Metric,Group,Value,Unit,Denominator'));
    assert(exported.includes('Intakes received,,' + total + ',intakes,'));
    for (const privateValue of ['Anna Schmidt', 'Sonnenallee', '850000', '%PDF']) assert(!exported.includes(privateValue));

    // Workflow and source drill-downs preserve the report's other filters.
    const workflow = page.locator('#analytics-summary table tbody a').first();
    const workflowHref = await workflow.getAttribute('href');
    const workflowKey = new URL(workflowHref, base).searchParams.get('workflow');
    await Promise.all([page.waitForNavigation(), workflow.click()]);
    assert.equal(await page.getByLabel('Workflow', { exact: true }).inputValue(), workflowKey);
    assert(await intakeCount() > 0 && await intakeCount() <= total);
    assert.equal(await page.getByLabel('Data cohort').inputValue(), 'demo');
    await page.goto(base + '/#analytics-workload');
    const chartData = JSON.parse(await page.locator('#analytics-chart-data').textContent());
    const sourceIndex = chartData.sources.series[0].data.findIndex(value => value > 0);
    const sourceKey = new URL(chartData.sources.links[sourceIndex], base).searchParams.get('source');
    await Promise.all([page.waitForNavigation(), page.getByRole('link', { name: chartData.sources.labels[sourceIndex], exact: true }).click()]);
    assert.equal(await page.getByLabel('Intake source', { exact: true }).inputValue(), sourceKey);
    assert.equal(await intakeCount(), chartData.sources.series[0].data[sourceIndex]);

    // Presets populate dates; editing dates selects a custom interval.
    await page.getByLabel('Period', { exact: true }).selectOption('7');
    const start = await page.getByLabel('From', { exact: true }).inputValue();
    const end = await page.getByLabel('Through', { exact: true }).inputValue();
    assert.equal((Date.parse(end) - Date.parse(start)) / 86400000, 6);
    await applyFilters();
    assert.equal(await page.getByLabel('Period', { exact: true }).inputValue(), '7');
    await page.getByLabel('From', { exact: true }).fill('2000-01-01');
    await page.getByLabel('Through', { exact: true }).fill('2000-01-07');
    await page.getByLabel('Through', { exact: true }).blur();
    assert.equal(await page.getByLabel('Period', { exact: true }).inputValue(), 'custom');
    await applyFilters();
    assert.equal(await intakeCount(), 0);
    assert.equal((await page.locator('[data-kpi="coverage"] .lf-analytics-value').innerText()).trim(), '—');
    for (const id of sections) {
      await page.locator('[data-tab][href="#' + id + '"]').click();
      assert(await page.locator('#' + id + ' .lf-analytics-empty').count() > 0);
    }

    // Invalid input never silently falls back to broader data.
    await page.goto(base + '/?period=custom&start_date=2000-01-07&end_date=2000-01-01');
    await selected('overview-analytics');
    assert(await page.getByText('Correct the filters above', { exact: false }).isVisible());
    assert.equal(await page.locator('[data-kpi]').count(), 0);
    assert.equal((await context.request.get(base + '/analytics/export/?workflow=not-owned')).status(), 400);

    await page.goto(base + '/#analytics-summary');
    await page.getByLabel('Data cohort').selectOption('live');
    await applyFilters();
    assert.equal(await intakeCount(), 0);
    assert(await page.locator('.lf-analytics-scope').getByText('Live office data', { exact: true }).isVisible());
    await page.getByLabel('Data cohort').selectOption('demo');
    await applyFilters();
    assert.equal(await intakeCount(), total);
    await page.locator('[data-tab][href="#analytics-workload"]').click();
    await Promise.all([page.waitForNavigation(), page.getByRole('navigation', { name: 'Analytics case pages' }).getByRole('link', { name: 'Next', exact: true }).click()]);
    await selected('analytics-workload');
    await selected('overview-analytics');
    assert.equal(new URL(page.url()).searchParams.get('case_page'), '2');
    assert.equal(await page.getByLabel('Data cohort').inputValue(), 'demo');
    assert.equal(await intakeCount(), total);

    // Deep links reveal both levels of tabs, and charts resize to narrow screens.
    await page.setViewportSize({ width: 390, height: 844 });
    for (const id of sections) {
      await page.goto(base + '/#' + id);
      await selected(id);
      await selected('overview-analytics');
      await chartsReady();
      await noOverflow();
      if (id === 'analytics-quality') await page.screenshot({ path: 'presentation/screenshots/14-analytics-mobile.png', fullPage: true });
    }
    await page.goto(base + '/?cohort=live#analytics-delivery');
    await noOverflow();

    const fallback = await browser.newContext({ javaScriptEnabled: false, storageState: await context.storageState() });
    await fallback.route('**/*', route => route.request().url().startsWith(base + '/') ? route.continue() : route.abort());
    const plain = await fallback.newPage();
    await plain.goto(base + '/');
    assert.equal(await plain.locator('[data-tab-panel][hidden]').count(), 0);
    assert.equal(await plain.locator('.lf-chart-data').count(), 7);
    await plain.locator('.lf-chart-data').first().getByText('View chart data', { exact: true }).click();
    assert(await plain.locator('.lf-chart-data').first().locator('table').isVisible());
    await fallback.close();

    assert.deepEqual(errors, []);
    const result = { passed: true, intakeCount: total, sections: sections.length, initializedCharts: initialized,
      keyboard: true, deepLinks: true, datePresets: true, workflowAndSourceDrilldowns: true,
      emptyAndInvalidFilters: true, liveDemoSeparation: true, csvScopeAndPrivacy: true,
      pagination: true, mobilePanels: sections.length, noJavaScriptTables: true, browserErrors: errors };
    fs.writeFileSync('presentation/analytics-browser-verification.json', JSON.stringify(result, null, 2) + '\n');
    console.log(JSON.stringify(result));
  } finally { await browser.close(); }
})().catch(error => { console.error(error.stack); process.exit(1); });
