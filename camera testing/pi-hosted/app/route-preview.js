'use strict';
(() => {
  const el = id => document.getElementById(id);
  const svg = el('routePreviewSvg');
  const play = el('routePreviewPlay');
  const status = el('routePreviewStatus');
  const ns = 'http://www.w3.org/2000/svg';
  let plan = null, signature = '', model = null, frame = null;
  let fraction = 0, playing = false, running = false, previousTime = 0, statusKey = '';

  function node(tag, attributes = {}, text) {
    const result = document.createElementNS(ns, tag);
    for (const [key, value] of Object.entries(attributes)) result.setAttribute(key, value);
    if (text !== undefined) result.textContent = text;
    return result;
  }

  function controls() {
    play.disabled = running || !model || model.length === 0;
    play.textContent = playing ? 'Pause Preview'
      : fraction >= 1 ? 'Replay Path' : fraction > 0 ? 'Continue Preview' : 'Preview Path';
  }

  function stopPreview() {
    if (frame !== null) cancelAnimationFrame(frame);
    frame = null;
    playing = false;
    controls();
  }

  function place(value) {
    if (!model) return;
    fraction = Math.max(0, Math.min(1, value));
    const distance = fraction * model.length;
    const segment = model.segments.find(item => distance <= item.start + item.length)
      || model.segments.at(-1);
    const amount = segment ? Math.max(0, Math.min(1, (distance - segment.start) / segment.length)) : 0;
    const point = segment ? {
      x: segment.a.x + (segment.b.x - segment.a.x) * amount,
      y: segment.a.y + (segment.b.y - segment.a.y) * amount
    } : model.points[0];
    const heading = segment ? Math.atan2(segment.b.y - segment.a.y, segment.b.x - segment.a.x) * 180 / Math.PI + 90 : 180;
    model.marker.setAttribute('transform', `translate(${point.x} ${point.y}) rotate(${heading})`);
    model.trace.setAttribute('stroke-dasharray', `${distance} ${model.length || 1}`);
    model.marker.style.visibility = running ? 'hidden' : 'visible';
    const key = running ? 'running' : fraction >= 1 ? 'finish' : segment?.label || 'single';
    if (key !== statusKey) {
      statusKey = key;
      status.textContent = running ? 'Planned path only · inspection progress is shown above.'
        : fraction >= 1 ? 'Preview complete · this did not move the pod.'
        : segment ? `${segment.label} · preview only, not a live position.`
        : 'One inspection stop · no travel between stops.';
    }
  }

  function animate(now) {
    if (!playing || !model) return;
    if (document.hidden || el('page-automatic').hidden) {
      stopPreview();
      return;
    }
    const duration = Math.max(6500, Math.min(16000, model.segments.length * 1200));
    place(fraction + (now - previousTime) / duration);
    previousTime = now;
    if (fraction >= 1) stopPreview();
    else frame = requestAnimationFrame(animate);
  }

  play.onclick = () => {
    if (play.disabled) return;
    if (playing) { stopPreview(); return; }
    if (fraction >= 1) place(0);
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
      place(1);
      controls();
      return;
    }
    playing = true;
    previousTime = performance.now();
    controls();
    frame = requestAnimationFrame(animate);
  };

  function draw() {
    stopPreview();
    fraction = 0;
    statusKey = '';
    svg.replaceChildren();
    model = null;
    if (!plan) { controls(); return; }
    const width = Math.max(220, Math.round(el('routePathPreview').clientWidth));
    const height = 270, left = 58, right = 18, top = 36, bottom = 40;
    const plotWidth = width - left - right, plotHeight = height - top - bottom;
    const spanX = (plan.columns - 1) * plan.xStep;
    const spanY = (plan.rows - 1) * plan.yStep;
    const scale = spanX && spanY ? Math.min(plotWidth / spanX, plotHeight / spanY)
      : spanX ? plotWidth / spanX : spanY ? plotHeight / spanY : 1;
    const offsetX = left + (plotWidth - spanX * scale) / 2;
    const offsetY = top + (plotHeight - spanY * scale) / 2;
    const leftward = plan.xDirection === 'left';
    const xy = (column, row) => ({x: offsetX + (leftward ? plan.columns - 1 - column : column) * plan.xStep * scale,
      y: offsetY + row * plan.yStep * scale, column, row});
    const points = plan.corners.map(step => xy(step.column, step.row));
    svg.setAttribute('viewBox', `0 0 ${width} ${height}`);
    svg.dataset.rows = plan.rows;
    svg.dataset.columns = plan.columns;
    svg.dataset.stops = plan.rows * plan.columns;
    svg.dataset.direction = leftward ? 'left' : 'right';
    svg.append(node('rect', {x: left - 10, y: top - 16, width: plotWidth + 20,
      height: plotHeight + 32, rx: 8, fill: 'var(--slot)'}));
    const rowTicks = [...new Set([0, Math.floor((plan.rows - 1) / 2), plan.rows - 1])];
    for (const row of rowTicks) {
      const point = xy(0, row);
      svg.append(node('line', {x1: left - 10, x2: width - right + 10,
        y1: point.y, y2: point.y, class: 'route-preview-grid'}));
      svg.append(node('text', {x: left - 15, y: point.y + 4,
        'text-anchor': 'end', class: 'route-preview-label'},
      new Intl.NumberFormat('en-US', {notation: Math.abs(plan.originY + row * plan.yStep) >= 1000 ? 'compact' : 'standard',
        maximumFractionDigits: 1}).format(plan.originY + row * plan.yStep)));
    }
    svg.append(node('text', {x: 4, y: 13, class: 'route-preview-label'}, 'Y (in)'));
    for (let column = 0; column < plan.columns; column++) {
      const point = xy(column, 0);
      svg.append(node('line', {x1: point.x, x2: point.x, y1: offsetY,
        y2: offsetY + spanY * scale, class: 'route-preview-grid'}));
      svg.append(node('text', {x: point.x, y: height - 4,
        'text-anchor': 'middle', class: 'route-preview-label'},
      plan.columns <= 6 ? `Col ${column + 1}` : String(column + 1)));
    }
    const defs = node('defs');
    const arrow = node('marker', {id: 'route-preview-direction-arrow', viewBox: '0 0 8 8',
      refX: 6, refY: 4, markerWidth: 6, markerHeight: 6, orient: 'auto'});
    arrow.append(node('path', {d: 'M1 1 L6 4 L1 7', class: 'route-preview-direction'}));
    defs.append(arrow);
    svg.append(defs);
    const path = points.map((point, index) => `${index ? 'L' : 'M'}${point.x} ${point.y}`).join(' ');
    svg.append(node('path', {d: path, class: 'route-preview-line', 'data-route-path': 'planned'}));
    const trace = node('path', {d: path, class: 'route-preview-trace'});
    svg.append(trace);
    const segments = [];
    let length = 0;
    for (let index = 1; index < points.length; index++) {
      const a = points[index - 1], b = points[index];
      const size = Math.hypot(b.x - a.x, b.y - a.y);
      if (!size) continue;
      const direction = a.column !== b.column ? (b.x < a.x ? 'left' : 'right') : b.y > a.y ? 'down' : 'up';
      const label = a.column !== b.column ? `Move ${direction} to column ${b.column + 1}`
        : `${direction === 'down' ? 'Down' : 'Up'} column ${a.column + 1}`;
      segments.push({a, b, length: size, start: length, label});
      length += size;
      if (size >= 25) svg.append(node('line', {
        x1: a.x + (b.x - a.x) * .42, y1: a.y + (b.y - a.y) * .42,
        x2: a.x + (b.x - a.x) * .58, y2: a.y + (b.y - a.y) * .58,
        class: 'route-preview-direction', 'marker-end': 'url(#route-preview-direction-arrow)',
        'data-direction': direction
      }));
    }
    const stride = Math.max(1, Math.ceil(plan.rows * plan.columns / 180));
    for (let column = 0; column < plan.columns; column++) {
      const rows = new Set([0, plan.rows - 1]);
      for (let row = 0; row < plan.rows; row += stride) rows.add(row);
      for (const row of rows) {
        const point = xy(column, row);
        const order = column * plan.rows + (column % 2 ? plan.rows - row : row + 1);
        const dot = node('circle', {cx: point.x, cy: point.y, r: 3,
          class: 'route-preview-dot', 'data-route-stop': order});
        dot.append(node('title', {}, `Stop ${order} · column ${column + 1}, brick ${row + 1}`));
        svg.append(dot);
      }
    }
    const first = points[0], last = points.at(-1);
    if (points.length === 1) {
      svg.append(node('text', {x: first.x, y: first.y - 18, 'text-anchor': 'middle',
        class: 'route-preview-endpoint'}, 'Start & finish'));
    } else {
      svg.append(node('text', {x: first.x, y: first.y - 16,
        'text-anchor': plan.columns === 1 ? 'middle' : leftward ? 'end' : 'start', class: 'route-preview-endpoint'}, 'Start'));
      svg.append(node('text', {x: last.x, y: last.y < top + 20 ? last.y - 16 : last.y + 20,
        'text-anchor': plan.columns === 1 ? 'middle' : leftward ? 'start' : 'end', class: 'route-preview-endpoint'}, 'Finish'));
    }
    const marker = node('g', {'data-route-marker': 'preview'});
    marker.append(node('path', {d: 'M0 -10 L8 9 L0 5 L-8 9 Z', class: 'route-preview-marker'}));
    svg.append(marker);
    model = {points, segments, length, trace, marker};
    el('routePreviewMeta').textContent = `${leftward ? 'Start right → move left' : 'Start left → move right'} · ${plan.columns} ${plan.columns === 1 ? 'column' : 'columns'} · ${plan.rows.toLocaleString('en-US')} ${plan.rows === 1 ? 'stop' : 'stops'} per column · ${(plan.rows * plan.columns).toLocaleString('en-US')} total`;
    place(0);
    controls();
  }

  function render(value, error) {
    const key = JSON.stringify(value ? {...value, originY: Number(value.originY.toFixed(1))} : {error});
    if (key === signature) return;
    signature = key;
    plan = value;
    el('routePreviewEmpty').hidden = !!plan;
    svg.toggleAttribute('hidden', !plan);
    if (!plan) {
      el('routePreviewMeta').textContent = '';
      el('routePreviewEmpty').textContent = error || 'Enter brick spacing and coverage to preview the path.';
      status.textContent = 'Preview only · not a live position.';
    }
    draw();
  }

  function setRunning(value) {
    if (running === value) return;
    running = value;
    if (running) stopPreview();
    place(fraction);
    controls();
  }

  let previousWidth = 0;
  new ResizeObserver(entries => {
    const width = Math.round(entries[0].contentRect.width);
    if (width > 0 && width !== previousWidth) {
      previousWidth = width;
      draw();
    }
  }).observe(el('routePathPreview'));
  document.addEventListener('visibilitychange', () => { if (document.hidden) stopPreview(); });
  window.addEventListener('hashchange', () => { if (el('page-automatic').hidden) stopPreview(); });
  window.brickRoutePreview = {render, setRunning};
})();
