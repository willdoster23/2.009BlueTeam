'use strict';
(() => {
  const el = id => document.getElementById(id);
  const pages = new Set(['setup', 'manual', 'automatic']);
  const titles = {setup: 'Setup', manual: 'Manual', automatic: 'Automatic inspection'};

  function showPage() {
    const requested = location.hash.slice(1);
    const current = pages.has(requested) ? requested : 'manual';
    for (const page of document.querySelectorAll('[data-page]')) {
      page.hidden = page.dataset.page !== current;
    }
    for (const link of document.querySelectorAll('[data-page-link]')) {
      if (link.dataset.pageLink === current) link.setAttribute('aria-current', 'page');
      else link.removeAttribute('aria-current');
    }
    document.querySelector('.map-section').hidden = current === 'setup';
    document.title = 'Brick Mapper · ' + titles[current];
    window.scrollTo({top: 0});
  }

  window.addEventListener('hashchange', showPage);
  showPage();
  if (document.body.dataset.preview === 'true') document.querySelector('.poweroff-controls').hidden = true;

  el('stopAll').onclick = async () => {
    const ui = window.blueInspection;
    window.dispatchEvent(new Event('inspectionstop'));
    if (!el('tapStop').disabled) el('tapStop').click();
    const results = await Promise.allSettled([ui.stopX(), ui.stopY(), ui.winch('stop')]);
    const error = results.find(result => result.status === 'rejected');
    el('inspectionStatus').textContent = error
      ? 'Stop could not be fully confirmed. ' + error.reason.message : 'Stop sent to the motion controls.';
  };

  const manualStep = el('yDistance');
  const autoStep = el('autoStepDistance');
  manualStep.addEventListener('input', () => { autoStep.value = manualStep.value; });
  autoStep.addEventListener('input', () => { manualStep.value = autoStep.value; });
})();
