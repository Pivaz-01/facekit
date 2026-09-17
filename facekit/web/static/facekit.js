/* Shared helpers for both pages. */

const fk = {};

fk.api = async function (url, options) {
  const response = await fetch(url, options);
  let payload = null;
  try {
    payload = await response.json();
  } catch (err) {
    throw new Error(`${response.status} ${response.statusText}`);
  }
  if (!response.ok || payload.ok === false) {
    throw new Error(payload.error || `${response.status} ${response.statusText}`);
  }
  return payload;
};

fk.post = function (url, body) {
  return fk.api(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body || {}),
  });
};

fk.say = function (element, text, kind) {
  if (!element) return;
  element.textContent = text;
  element.dataset.kind = kind || 'info';
  element.hidden = !text;
};

/* Job log. Polls only while something is running, then once more so the
   final lines and the summary always land. */
fk.jobWatcher = function (options) {
  const log = document.getElementById('job-log');
  const status = document.getElementById('job-status');
  const stopButton = document.getElementById('job-stop');
  let cursor = 0;
  let timer = null;
  let wasRunning = false;

  function append(lines) {
    if (!lines.length) return;
    const atBottom = log.scrollHeight - log.scrollTop - log.clientHeight < 40;
    log.textContent += lines.join('\n') + '\n';
    if (atBottom) log.scrollTop = log.scrollHeight;
  }

  async function poll() {
    let state;
    try {
      state = await fk.api(`/api/job?cursor=${cursor}`);
    } catch (err) {
      return;
    }
    cursor = state.cursor;
    append(state.lines);

    const label = {
      idle: 'Idle',
      running: `${state.name} — running, ${state.elapsed_s}s`,
      done: `${state.name} — finished in ${state.elapsed_s}s`,
      stopped: `${state.name} — stopped after ${state.elapsed_s}s`,
      failed: `${state.name} — failed`,
    }[state.status] || state.status;

    status.textContent = label;
    status.dataset.state = state.status;
    stopButton.disabled = !state.running;

    if (state.running) {
      wasRunning = true;
      timer = setTimeout(poll, 900);
    } else if (wasRunning) {
      wasRunning = false;
      if (options && options.onFinish) options.onFinish(state);
    }
  }

  return {
    start() {
      if (timer) clearTimeout(timer);
      cursor = 0;
      log.textContent = '';
      wasRunning = true;
      poll();
    },
    resume() {
      if (timer) clearTimeout(timer);
      poll();
    },
    stopButton,
  };
};
