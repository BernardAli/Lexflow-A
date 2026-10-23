(function () {
  'use strict';

  var owners = new Map();

  function hashTarget() {
    try { return document.getElementById(decodeURIComponent(location.hash.slice(1))); }
    catch (error) { return null; }
  }

  function reveal(target) {
    if (typeof target === 'string') target = document.getElementById(target);
    var panels = [];
    for (var node = target; node; node = node.parentElement) {
      if (owners.has(node)) panels.unshift(node);
    }
    panels.forEach(function (panel) { owners.get(panel).activate(panel.id); });
  }

  document.querySelectorAll('[data-tabs]').forEach(function (group) {
    function owned(selector) {
      return Array.from(group.querySelectorAll(selector)).filter(function (element) {
        return element.closest('[data-tabs]') === group;
      });
    }
    var list = owned('[data-tab-list]')[0];
    var panels = owned('[data-tab-panel]');
    var tabs = owned('[data-tab]').filter(function (tab) {
      return panels.some(function (panel) { return tab.getAttribute('href') === '#' + panel.id; });
    });
    if (!list || !tabs.length) return;

    group.classList.add('lf-tabs-ready');
    var remember = group.dataset.tabsRemember !== 'false';
    var storageKey = 'lexflow.tabs:' + location.pathname + ':' + group.dataset.tabs;
    list.setAttribute('role', 'tablist');
    tabs.forEach(function (tab) {
      var id = tab.getAttribute('href').slice(1);
      var panel = document.getElementById(id);
      tab.id = tab.id || 'tab-' + id;
      tab.setAttribute('role', 'tab');
      tab.setAttribute('aria-controls', id);
      panel.setAttribute('role', 'tabpanel');
      panel.setAttribute('aria-labelledby', tab.id);
      panel.tabIndex = 0;
    });

    function activate(id, options) {
      options = options || {};
      var active = tabs.find(function (tab) { return tab.getAttribute('href') === '#' + id; });
      if (!active) return;
      tabs.forEach(function (tab) {
        var selected = tab === active;
        tab.setAttribute('aria-selected', String(selected));
        tab.tabIndex = selected ? 0 : -1;
        document.getElementById(tab.getAttribute('aria-controls')).hidden = !selected;
      });
      if (remember) {
        try { sessionStorage.setItem(storageKey, id); } catch (error) { /* Optional browser storage. */ }
      }
      if (options.hash) {
        try { history.replaceState(null, '', '#' + encodeURIComponent(id)); } catch (error) { /* Keep tabs usable. */ }
      }
      if (options.focus) {
        active.focus({preventScroll: true});
        active.scrollIntoView({block: 'nearest', inline: 'nearest'});
      }
      group.dispatchEvent(new CustomEvent('lexflow:tabchange', {bubbles: true, detail: {panelId: id}}));
    }

    var controller = {activate: activate};
    panels.forEach(function (panel) { owners.set(panel, controller); });
    tabs.forEach(function (tab, index) {
      tab.addEventListener('click', function (event) {
        if (event.button || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
        event.preventDefault();
        activate(tab.getAttribute('aria-controls'), {hash: true});
      });
      tab.addEventListener('keydown', function (event) {
        var next;
        if (event.key === 'ArrowRight') next = (index + 1) % tabs.length;
        else if (event.key === 'ArrowLeft') next = (index + tabs.length - 1) % tabs.length;
        else if (event.key === 'Home') next = 0;
        else if (event.key === 'End') next = tabs.length - 1;
        else if (event.key === ' ') next = index;
        else return;
        event.preventDefault();
        activate(tabs[next].getAttribute('aria-controls'), {hash: true, focus: true});
      });
    });

    var target = hashTarget();
    var linked = target && panels.find(function (panel) { return panel === target || panel.contains(target); });
    var saved;
    if (remember) {
      try { saved = sessionStorage.getItem(storageKey); } catch (error) { /* Use the default tab. */ }
    }
    var initial = linked ? linked.id : saved || group.dataset.tabsDefault;
    if (!tabs.some(function (tab) { return tab.getAttribute('aria-controls') === initial; })) {
      initial = tabs[0].getAttribute('aria-controls');
    }
    activate(initial);
  });

  window.LexFlowTabs = {reveal: reveal};
  window.addEventListener('hashchange', function () { reveal(hashTarget()); });
  var firstInvalid;
  document.addEventListener('invalid', function (event) {
    if (firstInvalid) return;
    firstInvalid = event.target;
    reveal(firstInvalid);
    requestAnimationFrame(function () { firstInvalid = null; });
  }, true);
  // A bookmarked field or form control may belong to a nested tab panel.
  var initialTarget = hashTarget();
  if (initialTarget) {
    reveal(initialTarget);
    requestAnimationFrame(function () { initialTarget.scrollIntoView({block: 'nearest'}); });
  }
})();
