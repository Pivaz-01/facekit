/* The picker.
   Marks live here; the numbers do not. Every slope and every rise comes
   back from the server, so what this page draws and what the CSV
   contains are computed by the same code. */

(function () {
  const message = document.getElementById('message');
  const heading = document.getElementById('heading');
  const list = document.getElementById('recordings');
  const framesOut = document.getElementById('frames');

  const windowSlider = document.getElementById('window');
  const windowOut = document.getElementById('window-out');
  const sensSlider = document.getElementById('sensitivity');
  const sensOut = document.getElementById('sensitivity-out');

  let recordings = [];
  let current = null; // {idx, frames, areas, smoothed, fitted, peaks, valleys, manual, rises}
  let dirty = false;

  /* ---------- marks ---------- */

  const marks = { peak: new Set(), valley: new Set(), manual: new Set() };

  function clearMarks() {
    marks.peak.clear();
    marks.valley.clear();
    marks.manual.clear();
  }

  function markedFrames() {
    return [...marks.peak, ...marks.valley, ...marks.manual];
  }

  function typeOf(frame) {
    if (marks.peak.has(frame)) return 'peak';
    if (marks.valley.has(frame)) return 'valley';
    if (marks.manual.has(frame)) return 'manual';
    return null;
  }

  function classify(frame) {
    /* A newly marked point is a peak or valley if the smoothed curve
       turns there, and manual if it does not. */
    const i = current.frames.indexOf(frame);
    const smoothed = current.smoothed || [];
    if (i < 0 || !smoothed.length) {
      marks.manual.add(frame);
      return;
    }
    const here = smoothed[i];
    const before = i > 0 ? smoothed[i - 1] : here;
    const after = i < smoothed.length - 1 ? smoothed[i + 1] : here;
    if (here === null || before === null || after === null) {
      marks.manual.add(frame);
    } else if (here >= before && here >= after) {
      marks.peak.add(frame);
    } else if (here <= before && here <= after) {
      marks.valley.add(frame);
    } else {
      marks.manual.add(frame);
    }
  }

  /* ---------- server round trips ---------- */

  async function recompute() {
    try {
      const result = await fk.post('/api/review/recompute', {
        idx: current.idx,
        peaks: [...marks.peak],
        valleys: [...marks.valley],
      });
      current.rises = result.rises;
      current.fitted = result.fitted;
      current.mean_angle_deg = result.mean_angle_deg;
    } catch (err) {
      fk.say(message, err.message, 'error');
    }
  }

  async function saveCurrent() {
    if (!current || !dirty) return;
    const points = markedFrames()
      .map((frame) => ({ frame, type: typeOf(frame) }))
      .sort((a, b) => a.frame - b.frame);
    try {
      await fk.post('/api/review/save', {
        idx: current.idx,
        points,
        rises: current.rises || [],
      });
      dirty = false;
      const entry = recordings.find((r) => r.idx === current.idx);
      if (entry) {
        entry.reviewed = points.length > 0;
        entry.points = points.length;
        renderList();
      }
    } catch (err) {
      fk.say(message, err.message, 'error');
    }
  }

  /* ---------- drawing ---------- */

  function render() {
    const frames = current.frames;
    const areas = current.areas;

    const traces = [
      {
        x: frames, y: areas, mode: 'lines', hoverinfo: 'skip',
        showlegend: false, line: { color: '#cbd5e0', width: 1 },
      },
      {
        x: frames, y: current.smoothed, mode: 'lines', hoverinfo: 'skip',
        showlegend: false, line: { color: '#2b6cb0', width: 2, dash: 'dot' },
      },
      {
        x: frames, y: current.fitted || [], mode: 'lines', hoverinfo: 'skip',
        showlegend: false, line: { color: '#2f855a', width: 2.5 },
      },
    ];

    const colors = [];
    const sizes = [];
    const symbols = [];
    const texts = [];

    frames.forEach((frame, i) => {
      const area = areas[i];
      const shown = area === null ? 'no measurement' : area.toFixed(4);
      const kind = typeOf(frame);
      if (kind === 'peak') {
        colors.push('#e53e3e'); sizes.push(13); symbols.push('triangle-up');
        texts.push(`peak\nframe ${frame}\narea ${shown}`);
      } else if (kind === 'valley') {
        colors.push('#38a169'); sizes.push(13); symbols.push('triangle-down');
        texts.push(`valley\nframe ${frame}\narea ${shown}`);
      } else if (kind === 'manual') {
        colors.push('#805ad5'); sizes.push(10); symbols.push('circle');
        texts.push(`manual\nframe ${frame}\narea ${shown}`);
      } else {
        colors.push('#a0aec0'); sizes.push(4); symbols.push('circle');
        texts.push(`frame ${frame}\narea ${shown}`);
      }
    });

    traces.push({
      x: frames, y: areas, mode: 'markers', showlegend: false,
      marker: {
        color: colors, size: sizes, symbol: symbols,
        line: { color: '#fff', width: sizes.map((s) => (s > 8 ? 1 : 0)) },
      },
      text: texts, hoverinfo: 'text',
    });

    const starX = [];
    const starY = [];
    const starText = [];
    (current.rises || []).forEach((rise) => {
      if (rise.slope_max_frame === null) return;
      starX.push(rise.slope_max_frame);
      const i = frames.indexOf(Math.round(rise.slope_max_frame));
      starY.push(i >= 0 && current.fitted ? current.fitted[i] : rise.peak_area);
      starText.push(
        `steepest point\nframe ${rise.slope_max_frame.toFixed(1)}\n` +
        `slope ${rise.slope_max.toFixed(5)}\nangle ${rise.angle_deg.toFixed(2)}\u00b0`
      );
    });

    traces.push({
      x: starX, y: starY, mode: 'markers', showlegend: false,
      marker: {
        color: '#d69e2e', size: 14, symbol: 'star',
        line: { color: '#7c5e15', width: 1 },
      },
      text: starText, hoverinfo: 'text',
    });

    Plotly.react('chart', traces, {
      xaxis: { title: 'Frame' },
      yaxis: { title: 'Mouth area / IPD\u00b2' },
      margin: { t: 10, b: 50, l: 70, r: 20 },
      hovermode: 'closest',
      paper_bgcolor: 'rgba(0,0,0,0)',
      plot_bgcolor: 'rgba(0,0,0,0)',
    }, { responsive: true, displaylogo: false });

    const chart = document.getElementById('chart');
    chart.removeAllListeners?.('plotly_click');
    chart.on('plotly_click', async (event) => {
      const point = event.points[0];
      if (!point || point.curveNumber !== 3) return;
      const frame = frames[point.pointIndex];
      if (typeOf(frame)) {
        marks.peak.delete(frame);
        marks.valley.delete(frame);
        marks.manual.delete(frame);
      } else {
        classify(frame);
      }
      dirty = true;
      await recompute();
      render();
      await saveCurrent();
    });

    updateReadout();
  }

  function updateReadout() {
    const rises = current.rises || [];
    document.getElementById('n-rises').textContent = rises.length;

    const angles = rises.map((r) => r.angle_deg).filter((a) => a !== null);
    document.getElementById('mean-angle').textContent = angles.length
      ? `${(angles.reduce((a, b) => a + b, 0) / angles.length).toFixed(2)}\u00b0`
      : '—';

    const slopes = rises.map((r) => r.slope_max).filter((s) => s !== null);
    document.getElementById('max-slope').textContent = slopes.length
      ? Math.max(...slopes).toFixed(5)
      : '—';

    const parts = [];
    ['peak', 'valley', 'manual'].forEach((kind) => {
      const frames = [...marks[kind]].sort((a, b) => a - b);
      if (!frames.length) return;
      const tags = frames
        .map((f) => `<span class="tag ${kind}">${f}</span>`)
        .join('');
      parts.push(`<b>${kind} (${frames.length})</b> ${tags}`);
    });
    framesOut.innerHTML = parts.length ? parts.join('<br>') : 'No marks yet.';
  }

  /* ---------- loading ---------- */

  function renderList() {
    list.innerHTML = '';
    recordings.forEach((entry) => {
      const item = document.createElement('li');
      const button = document.createElement('button');
      button.type = 'button';
      if (current && entry.idx === current.idx) {
        button.setAttribute('aria-current', 'true');
      }
      const tick = entry.reviewed ? '<span class="tick">reviewed</span> ' : '';
      button.innerHTML = `${tick}${entry.video}<br><span class="stages">${entry.frames} frames</span>`;
      button.addEventListener('click', () => load(entry.idx));
      item.appendChild(button);
      list.appendChild(item);
    });
  }

  async function load(idx, fresh) {
    await saveCurrent();
    fk.say(message, '', 'info');
    heading.textContent = 'Loading';

    const params = new URLSearchParams({ idx });
    if (fresh) {
      params.set('fresh', '1');
      params.set('window', windowSlider.value);
      params.set('sensitivity', sensSlider.value);
    }

    let data;
    try {
      data = await fk.api(`/api/review/recording?${params}`);
    } catch (err) {
      heading.textContent = 'Nothing to review';
      fk.say(message, err.message, 'error');
      return;
    }

    current = data;
    clearMarks();
    (data.peaks || []).forEach((f) => marks.peak.add(f));
    (data.valleys || []).forEach((f) => marks.valley.add(f));
    (data.manual || []).forEach((f) => marks.manual.add(f));
    dirty = Boolean(fresh);

    heading.textContent = `${data.video} (${data.idx + 1} of ${data.total})`;
    if (data.window) {
      windowSlider.value = data.window;
      windowOut.textContent = data.window;
    }
    renderList();
    render();
    if (fresh) await saveCurrent();
  }

  async function loadSeries(reload) {
    try {
      const data = await fk.api(`/api/review/series${reload ? '?reload=1' : ''}`);
      recordings = data.recordings;
      document.getElementById('source-path').textContent = data.source;
      renderList();
      if (!recordings.length) {
        heading.textContent = 'No recordings in that file';
        fk.say(message, 'The mouth-area CSV has no recordings in it.', 'error');
        return;
      }
      const firstUnreviewed = recordings.find((r) => !r.reviewed);
      await load(firstUnreviewed ? firstUnreviewed.idx : 0);
    } catch (err) {
      heading.textContent = 'Nothing to review';
      fk.say(message, err.message, 'error');
    }
  }

  /* ---------- controls ---------- */

  windowSlider.addEventListener('input', () => {
    windowOut.textContent = windowSlider.value;
  });
  sensSlider.addEventListener('input', () => {
    sensOut.textContent = `${sensSlider.value}%`;
  });

  document.getElementById('redetect').addEventListener('click', () => {
    if (current) load(current.idx, true);
  });

  document.getElementById('clear').addEventListener('click', async () => {
    if (!current) return;
    clearMarks();
    dirty = true;
    await recompute();
    render();
    await saveCurrent();
  });

  document.getElementById('prev').addEventListener('click', () => {
    if (current && current.idx > 0) load(current.idx - 1);
  });

  document.getElementById('next').addEventListener('click', () => {
    if (current && current.idx < current.total - 1) load(current.idx + 1);
  });

  document.getElementById('reload').addEventListener('click', () => loadSeries(true));

  document.getElementById('export').addEventListener('click', async () => {
    await saveCurrent();
    fk.say(message, 'Exporting.', 'info');
    try {
      const result = await fk.post('/api/review/export', {});
      fk.say(message, result.message, 'ok');
    } catch (err) {
      fk.say(message, err.message, 'error');
    }
  });

  document.addEventListener('keydown', (event) => {
    if (event.target.matches('input, textarea, select')) return;
    if (event.key === 'ArrowRight') document.getElementById('next').click();
    if (event.key === 'ArrowLeft') document.getElementById('prev').click();
  });

  window.addEventListener('beforeunload', () => {
    if (dirty) saveCurrent();
  });

  loadSeries(false);
})();
