/* Settings page.
   Reads values back out of the generated form using each field's
   data-type, so nothing here has to know which settings exist. */

(function () {
  const form = document.getElementById('settings-form');
  const message = document.getElementById('settings-message');
  const watcher = fk.jobWatcher();

  /* ---------- reading the form ---------- */

  function collect() {
    const values = {};

    form.querySelectorAll('[data-type]').forEach((element) => {
      const type = element.dataset.type;

      if (type === 'multichoice') {
        const picked = [];
        element.querySelectorAll('input[type=checkbox]').forEach((box) => {
          if (box.checked) picked.push(box.value);
        });
        values[element.dataset.name] = picked;
        return;
      }

      const name = element.name;
      if (!name) return;

      if (type === 'bool') {
        values[name] = element.checked;
      } else if (type === 'int') {
        values[name] = element.value === '' ? 0 : parseInt(element.value, 10);
      } else if (type === 'float') {
        values[name] = element.value === '' ? 0 : parseFloat(element.value);
      } else if (type === 'intlist' || type === 'strlist') {
        values[name] = element.value
          .split(/[\n,;]+/)
          .map((part) => part.trim())
          .filter(Boolean);
      } else {
        values[name] = element.value;
      }
    });

    return values;
  }

  /* ---------- saving ---------- */

  /* Unsaved changes are tracked and shown on the Save button. Editing a
     path and then navigating away used to discard the edit silently,
     which surfaced much later as an error naming the old value. */
  const saveButton = document.getElementById('save-settings');
  let unsaved = false;

  function markUnsaved() {
    unsaved = true;
    saveButton.textContent = 'Save settings *';
    saveButton.title = 'You have unsaved changes';
  }

  function markSaved() {
    unsaved = false;
    saveButton.textContent = 'Save settings';
    saveButton.title = '';
  }

  form.addEventListener('input', markUnsaved);
  form.addEventListener('change', markUnsaved);

  async function save() {
    try {
      await fk.post('/api/settings', collect());
      markSaved();
      fk.say(message, 'Settings saved.', 'ok');
    } catch (err) {
      fk.say(message, err.message, 'error');
      throw err;
    }
  }

  saveButton.addEventListener('click', save);

  /* Anything that leaves this page saves first, so what the next page
     reads is what is on screen. */
  document.querySelectorAll('[data-save-then]').forEach((button) => {
    button.addEventListener('click', async () => {
      fk.say(message, '', 'info');
      try {
        await save();
      } catch (err) {
        return; // the error is already on screen; do not navigate
      }
      window.location.href = button.dataset.saveThen;
    });
  });

  const navReview = document.getElementById('nav-review');
  if (navReview) {
    navReview.addEventListener('click', async (event) => {
      if (!unsaved) return;
      event.preventDefault();
      try {
        await save();
      } catch (err) {
        return;
      }
      window.location.href = navReview.href;
    });
  }

  window.addEventListener('beforeunload', (event) => {
    if (!unsaved) return;
    event.preventDefault();
    event.returnValue = '';
  });

  /* ---------- running a stage ---------- */

  document.querySelectorAll('[data-run]').forEach((button) => {
    button.addEventListener('click', async () => {
      const stage = button.dataset.run;
      fk.say(message, '', 'info');
      try {
        await save();
        await fk.post(`/api/run/${stage}`, {});
        watcher.start();
      } catch (err) {
        fk.say(message, err.message, 'error');
      }
    });
  });

  watcher.stopButton.addEventListener('click', () => {
    fk.post('/api/job/stop', {}).catch(() => {});
  });

  /* Pick up a run already in progress, so reloading the page does not
     lose sight of it. */
  watcher.resume();

  /* ---------- advanced fields ---------- */

  const advancedToggle = document.getElementById('show-advanced');

  function applyAdvanced() {
    const show = advancedToggle.checked;
    document
      .querySelectorAll('.field[data-advanced="true"]')
      .forEach((field) => field.classList.toggle('hidden', !show));
  }

  advancedToggle.addEventListener('change', applyAdvanced);
  applyAdvanced();

  /* ---------- file browser ---------- */

  const dialog = document.getElementById('browser');
  const here = document.getElementById('browse-here');
  const body = document.getElementById('browse-body');
  let target = null;
  let mode = 'set';
  let current = '';
  let wantFiles = false;
  let defaultName = '';

  async function show(path) {
    let data;
    try {
      data = await fk.api(
        `/api/browse?path=${encodeURIComponent(path || '')}&files=${wantFiles ? 1 : 0}`
      );
    } catch (err) {
      body.textContent = err.message;
      return;
    }
    current = data.path;
    here.textContent = data.path;
    body.innerHTML = '';

    data.folders.forEach((folder) => {
      const button = document.createElement('button');
      button.className = 'entry';
      button.type = 'button';
      button.textContent = `${folder.name}/`;
      button.addEventListener('click', () => show(folder.path));
      body.appendChild(button);
    });

    data.files.forEach((file) => {
      const button = document.createElement('button');
      button.className = 'entry file';
      button.type = 'button';
      button.textContent = file.name;
      button.addEventListener('click', () => {
        apply(file.path);
        dialog.close();
      });
      body.appendChild(button);
    });

    if (!data.folders.length && !data.files.length) {
      const empty = document.createElement('p');
      empty.className = 'entry file';
      empty.textContent = 'Nothing here. Use Up to go back.';
      body.appendChild(empty);
    }
  }

  function apply(path) {
    if (!target) return;
    if (mode === 'append') {
      const existing = target.value.trim();
      target.value = existing ? `${existing}\n${path}` : path;
    } else {
      target.value = path;
    }
  }

  document.querySelectorAll('[data-browse]').forEach((button) => {
    button.addEventListener('click', () => {
      target = document.getElementById(button.dataset.browse);
      mode = 'set';
      wantFiles = button.dataset.files === '1';
      defaultName = button.dataset.defaultName || '';
      dialog.showModal();
      show(target.value || '');
    });
  });

  document.querySelectorAll('[data-browse-append]').forEach((button) => {
    button.addEventListener('click', () => {
      target = document.getElementById(button.dataset.browseAppend);
      mode = 'append';
      wantFiles = false;
      defaultName = '';
      dialog.showModal();
      const lines = target.value.trim().split('\n').filter(Boolean);
      show(lines.length ? lines[lines.length - 1] : '');
    });
  });

  document.getElementById('browse-up').addEventListener('click', async () => {
    const data = await fk.api(
      `/api/browse?path=${encodeURIComponent(current)}&files=${wantFiles ? 1 : 0}`
    );
    if (data.parent) show(data.parent);
  });

  /* A field that needs a file gets one. Choosing a folder here used to
     leave a bare directory in the field, which fails at run time as a
     permission error on Windows rather than anything legible. */
  document.getElementById('browse-choose').addEventListener('click', () => {
    const separator = current.includes('\\') ? '\\' : '/';
    const complete = wantFiles && defaultName
      ? `${current.replace(/[\\/]+$/, '')}${separator}${defaultName}`
      : current;
    apply(complete);
    dialog.close();
  });

  document.getElementById('browse-cancel').addEventListener('click', () => dialog.close());
})();
