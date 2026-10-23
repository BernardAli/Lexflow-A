(function () {
  'use strict';

  var filters = document.querySelector('[data-analytics-filters]');
  if (filters) {
    var period = filters.elements.period;
    ['start_date', 'end_date'].forEach(function (name) {
      filters.elements[name].addEventListener('change', function () { period.value = 'custom'; });
    });
    period.addEventListener('change', function () {
      if (period.value === 'custom') return;
      var end = filters.dataset.reportToday;
      var start = new Date(end + 'T00:00:00Z');
      start.setUTCDate(start.getUTCDate() - Number(period.value) + 1);
      filters.elements.start_date.value = start.toISOString().slice(0, 10);
      filters.elements.end_date.value = end;
    });
  }

  var encoded = document.getElementById('analytics-chart-data');
  if (!encoded || typeof echarts === 'undefined') return;
  var charts = JSON.parse(encoded.textContent);
  var palette = ['#1e5250', '#d9a64a', '#a3242c', '#719c83', '#527c99', '#9b83a4', '#7d8781'];
  var text = { fontFamily: 'Schibsted Grotesk, Helvetica, Arial, sans-serif', fontSize: 12, color: '#5d6a67' };
  var reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  function axis(type) {
    return { type: type, minInterval: type === 'value' ? 1 : undefined,
      axisLine: { lineStyle: { color: '#c6cec7' } }, axisTick: { show: false },
      axisLabel: Object.assign({}, text, { hideOverlap: true }),
      splitLine: { show: type === 'value', lineStyle: { color: '#e8ede7' } } };
  }

  function options(chart, width) {
    var config = { animation: !reducedMotion, color: palette, textStyle: text,
      aria: { enabled: true }, tooltip: { trigger: 'axis', renderMode: 'richText' } };
    if (chart.kind === 'donut') {
      config.tooltip.trigger = 'item';
      config.legend = { bottom: 0, textStyle: text, type: 'scroll' };
      config.series = [{ type: 'pie', radius: ['38%', '65%'], center: ['50%', '44%'],
        label: { show: false }, data: chart.labels.map(function (name, index) {
          return { name: name, value: chart.series[0].data[index] };
        }) }];
      return config;
    }
    var horizontal = chart.kind === 'horizontal';
    var category = Object.assign(axis('category'), { data: chart.labels });
    var numeric = axis('value');
    if (horizontal) category.axisLabel = Object.assign({}, text, { width: width < 400 ? 112 : 155, overflow: 'break' });
    config.grid = { left: 8, right: 14, bottom: 8, top: chart.series.length > 1 ? 58 : 16, containLabel: true };
    config.xAxis = horizontal ? numeric : category;
    config.yAxis = horizontal ? category : numeric;
    if (chart.series.length > 1) config.legend = { top: 0, left: 0, type: 'scroll', textStyle: text, itemWidth: 10, itemHeight: 10 };
    config.series = chart.series.map(function (series) {
      return { name: series.name, type: 'bar', stack: chart.kind === 'stacked' ? 'intakes' : undefined,
        data: series.data, barMaxWidth: 36, itemStyle: { borderRadius: horizontal ? [0, 3, 3, 0] : [3, 3, 0, 0] } };
    });
    return config;
  }

  function renderVisibleCharts() {
    document.querySelectorAll('[data-analytics-chart]').forEach(function (element) {
      if (!element.offsetWidth || element.closest('[hidden]')) return;
      var data = charts[element.dataset.analyticsChart];
      if (!data || data.empty) return;
      var instance = echarts.getInstanceByDom(element);
      if (!instance) {
        instance = echarts.init(element);
        instance.on('click', function (event) {
          var link = data.links[event.dataIndex];
          if (link && link.startsWith('/') && !link.startsWith('//')) location.href = link;
        });
      }
      instance.resize();
      instance.setOption(options(data, element.clientWidth));
    });
  }
  var frame;
  function schedule() {
    if (frame) cancelAnimationFrame(frame);
    frame = requestAnimationFrame(renderVisibleCharts);
  }
  renderVisibleCharts();
  document.addEventListener('lexflow:tabchange', schedule);
  window.addEventListener('resize', schedule);
})();
