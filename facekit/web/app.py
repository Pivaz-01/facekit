"""
The local interface.

Every field on the settings page is generated from facekit.settings, so
adding an option there makes it appear here with its help text and no
HTML change. The review page is a thin view over facekit.events: it asks
the server what it would detect and draws the answer, which is how the
curve on screen and the numbers in the CSV stay the same thing.

Binds to the loopback address. There is no authentication and the file
browser can see the whole filesystem, which is fine on your own machine
and unacceptable on a shared network interface.
"""

from __future__ import annotations

import json
import threading
import webbrowser
from pathlib import Path

from flask import Flask, jsonify, render_template, request

from .. import __version__, env_report
from .. import settings as st
from ..jobs import Runner

app = Flask(__name__)
app.config["JSON_SORT_KEYS"] = False

runner = Runner()

# In-memory review session, rebuilt from the CSV whenever it is asked for.
_review = {"source": None, "series": [], "selections": {}}
_review_lock = threading.Lock()

REVIEW_DIR = st.CONFIG_DIR / "review"


# ======================================================================
#  Errors
# ======================================================================

@app.errorhandler(Exception)
def _as_json(exc):
    """Any unhandled error in an API route comes back as JSON.

    Flask's default is an HTML traceback, which the interface cannot
    parse, so a clear server-side message used to reach the user as
    "Unexpected token '<'". Page routes keep the default behaviour,
    because a traceback in the browser is useful there.
    """
    from werkzeug.exceptions import HTTPException

    if isinstance(exc, HTTPException):
        return exc
    if not request.path.startswith("/api/"):
        raise exc

    app.logger.exception("unhandled error in %s", request.path)
    hint = ""
    if isinstance(exc, PermissionError):
        hint = (" A path field may be naming a folder where a file is "
                "needed, or the file may be open in Excel.")
    return jsonify({
        "ok": False,
        "error": f"{type(exc).__name__}: {exc}{hint}",
    }), 500


# ======================================================================
#  Settings page
# ======================================================================

def _serialise_settings(values: dict) -> list[dict]:
    """The registry plus current values, as the template wants it."""
    out = []
    for group in st.GROUP_ORDER:
        fields = []
        for spec in st.SETTINGS:
            if spec.group != group:
                continue
            value = values.get(spec.name, spec.default)
            if spec.type in ("intlist", "strlist", "multichoice"):
                shown = ", ".join(str(v) for v in value)
            else:
                shown = value
            fields.append({
                "name": spec.name,
                "label": spec.label,
                "type": spec.type,
                "value": value,
                "shown": shown,
                "choices": list(spec.choices),
                "help": spec.help,
                "advanced": spec.advanced,
                "stages": list(spec.stages),
                "min": spec.min,
                "max": spec.max,
                "unit": spec.unit,
                "default_name": st.DEFAULT_FILENAMES.get(spec.name, ""),
            })
        if fields:
            out.append({"group": group, "fields": fields})
    return out


@app.route("/")
def index():
    values = st.load()
    return render_template(
        "index.html",
        version=__version__,
        groups=_serialise_settings(values),
        stages=[{"key": k, "title": st.STAGE_TITLES[k]} for k in st.STAGES],
        presets=st.list_presets(),
        env=env_report(),
    )


@app.route("/review")
def review_page():
    return render_template("review.html", version=__version__)


# ======================================================================
#  Settings API
# ======================================================================

@app.route("/api/settings", methods=["GET", "POST"])
def api_settings():
    if request.method == "GET":
        return jsonify(st.load())

    payload = request.get_json(silent=True) or {}
    try:
        cleaned = {k: st.coerce(k, v) for k, v in payload.items()
                   if k in st.BY_NAME}
    except st.SettingError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400

    values = st.load()
    values.update(cleaned)
    path = st.save(values)
    return jsonify({"ok": True, "saved_to": str(path), "values": values})


@app.route("/api/env")
def api_env():
    return jsonify(env_report())


@app.route("/api/presets", methods=["GET", "POST", "DELETE"])
def api_presets():
    if request.method == "GET":
        return jsonify({"presets": st.list_presets()})

    payload = request.get_json(silent=True) or {}
    name = (payload.get("name") or "").strip()
    if not name:
        return jsonify({"ok": False, "error": "A preset needs a name."}), 400

    if request.method == "DELETE":
        removed = st.delete_preset(name)
        return jsonify({"ok": removed, "presets": st.list_presets()})

    if payload.get("load"):
        try:
            loaded = st.load_preset(name)
        except st.SettingError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 404
        values = st.load()
        values.update(loaded)
        st.save(values)
        return jsonify({"ok": True, "values": values})

    st.save_preset(name, st.load())
    return jsonify({"ok": True, "presets": st.list_presets()})


# ======================================================================
#  File browser
# ======================================================================

@app.route("/api/browse")
def api_browse():
    """List one directory, so paths can be picked rather than typed."""
    raw = request.args.get("path", "").strip()
    want_files = request.args.get("files", "0") == "1"

    if not raw:
        target = Path.home()
    else:
        target = Path(raw).expanduser()
        if not target.is_dir():
            target = target.parent if target.parent.is_dir() else Path.home()

    try:
        entries = sorted(target.iterdir(), key=lambda p: p.name.lower())
    except (PermissionError, OSError) as exc:
        return jsonify({"ok": False, "error": str(exc),
                        "path": str(target)}), 200

    folders, files = [], []
    for entry in entries:
        if entry.name.startswith("."):
            continue
        try:
            if entry.is_dir():
                folders.append({"name": entry.name, "path": str(entry)})
            elif want_files:
                files.append({"name": entry.name, "path": str(entry),
                              "size": entry.stat().st_size})
        except OSError:
            continue

    parent = str(target.parent) if target.parent != target else None
    return jsonify({"ok": True, "path": str(target), "parent": parent,
                    "folders": folders, "files": files})


# ======================================================================
#  Jobs
# ======================================================================

_STAGE_RUNNERS = {}


def _stage_runner(stage: str):
    if stage not in _STAGE_RUNNERS:
        if stage == "landmarks":
            from .. import landmarks

            _STAGE_RUNNERS[stage] = landmarks.run
        elif stage == "mouth":
            from .. import mouth

            _STAGE_RUNNERS[stage] = mouth.run
        elif stage == "events":
            from .. import events

            _STAGE_RUNNERS[stage] = events.run
        elif stage == "metrics":
            from .. import metrics

            _STAGE_RUNNERS[stage] = metrics.run
        else:
            raise KeyError(stage)
    return _STAGE_RUNNERS[stage]


@app.route("/api/run/<stage>", methods=["POST"])
def api_run(stage: str):
    if stage not in st.STAGES:
        return jsonify({"ok": False, "error": f"No stage {stage!r}"}), 404

    payload = request.get_json(silent=True) or {}
    try:
        overrides = {k: st.coerce(k, v) for k, v in
                     (payload.get("settings") or {}).items()
                     if k in st.BY_NAME}
        values = st.load(overrides)
    except st.SettingError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400

    kwargs = {"values": values}
    if stage == "metrics":
        kwargs["out_dir"] = None

    try:
        function = _stage_runner(stage)
    except (ImportError, KeyError) as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500

    try:
        runner.start(st.STAGE_TITLES[stage], function, **kwargs)
    except RuntimeError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 409

    return jsonify({"ok": True, "stage": stage})


@app.route("/api/job")
def api_job():
    cursor = request.args.get("cursor", "0")
    try:
        cursor = int(cursor)
    except ValueError:
        cursor = 0
    return jsonify(runner.state(cursor))


@app.route("/api/job/stop", methods=["POST"])
def api_job_stop():
    return jsonify({"ok": runner.stop()})


# ======================================================================
#  Review session
# ======================================================================

def _selection_store(source: Path) -> Path:
    """Where review progress for one input file is kept."""
    import hashlib

    digest = hashlib.sha1(str(source.resolve()).encode()).hexdigest()[:16]
    return REVIEW_DIR / f"{digest}.json"


def _load_review(values: dict, force: bool = False) -> dict:
    """The review session, loaded from the configured stage 2 output.

    Reading the series from the CSV rather than from whatever a previous
    run left in memory means the picker still works after a restart, and
    after a stage 2 run started from the command line.
    """
    from .. import events

    source = values["MOUTH_CSV"]
    if not source:
        raise RuntimeError(
            "No input. Set Mouth csv under Paths, then run the mouth-area "
            "stage.")
    source_path = st.resolve_input_path(source, "MOUTH_CSV")

    with _review_lock:
        if not force and _review["source"] == str(source_path):
            return _review

        series = events.load_series(source_path)
        selections = {}
        store = _selection_store(source_path)
        if store.exists():
            try:
                selections = json.loads(store.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                selections = {}

        _review["source"] = str(source_path)
        _review["series"] = series
        _review["selections"] = selections
        return _review


def _save_selections() -> None:
    if not _review["source"]:
        return
    REVIEW_DIR.mkdir(parents=True, exist_ok=True)
    store = _selection_store(Path(_review["source"]))
    store.write_text(json.dumps(_review["selections"]), encoding="utf-8")


def _key_for(entry: dict) -> str:
    return f"{entry['folder']}\x00{entry['video']}"


@app.route("/api/review/series")
def api_review_series():
    values = st.load()
    try:
        session = _load_review(values, force=request.args.get("reload") == "1")
    except (RuntimeError, st.SettingError, OSError) as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400

    listing = []
    for index, entry in enumerate(session["series"]):
        saved = session["selections"].get(_key_for(entry))
        listing.append({
            "idx": index,
            "video": entry["video"],
            "folder": entry["folder"],
            "frames": len(entry["frames"]),
            "reviewed": bool(saved),
            "points": len(saved.get("points", [])) if saved else 0,
        })
    return jsonify({"ok": True, "source": session["source"],
                    "recordings": listing})


@app.route("/api/review/recording")
def api_review_recording():
    values = st.load()
    try:
        session = _load_review(values)
    except (RuntimeError, st.SettingError, OSError) as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400

    try:
        index = int(request.args.get("idx", "0"))
    except ValueError:
        index = 0
    if not (0 <= index < len(session["series"])):
        return jsonify({"ok": False, "error": "No recording at that index."}), 404

    entry = session["series"][index]
    window = request.args.get("window")
    sensitivity = request.args.get("sensitivity")
    window = int(window) if window not in (None, "", "auto") else None
    sensitivity = int(sensitivity) if sensitivity not in (None, "") else None

    from .. import events

    saved = session["selections"].get(_key_for(entry))
    if saved and request.args.get("fresh") != "1":
        peaks = [p["frame"] for p in saved["points"] if p["type"] == "peak"]
        valleys = [p["frame"] for p in saved["points"] if p["type"] == "valley"]
        manual = [p["frame"] for p in saved["points"] if p["type"] == "manual"]
        found = events.analyse(entry, values, window, sensitivity)
        analysis = {**found, "peaks": peaks, "valleys": valleys,
                    "manual": manual, "rises": saved.get("rises", [])}
    else:
        analysis = {**events.analyse(entry, values, window, sensitivity),
                    "manual": []}

    return jsonify({
        "ok": True,
        "idx": index,
        "total": len(session["series"]),
        "video": entry["video"],
        "folder": entry["folder"],
        "frames": entry["frames"],
        "areas": entry["areas"],
        **analysis,
    })


@app.route("/api/review/recompute", methods=["POST"])
def api_review_recompute():
    """Rises for a hand-edited set of points, computed server-side."""
    values = st.load()
    try:
        session = _load_review(values)
    except (RuntimeError, st.SettingError, OSError) as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400

    payload = request.get_json(silent=True) or {}
    index = int(payload.get("idx", 0))
    if not (0 <= index < len(session["series"])):
        return jsonify({"ok": False, "error": "No recording at that index."}), 404

    entry = session["series"][index]
    frame_to_index = {f: i for i, f in enumerate(entry["frames"])}
    peaks = [frame_to_index[f] for f in payload.get("peaks", [])
             if f in frame_to_index]
    valleys = [frame_to_index[f] for f in payload.get("valleys", [])
               if f in frame_to_index]

    from .. import events

    rises, fitted = events.compute_rises(
        entry["frames"], entry["areas"], sorted(peaks), sorted(valleys),
        values)
    angles = [r["angle_deg"] for r in rises if r["angle_deg"] is not None]
    return jsonify({"ok": True, "rises": rises, "fitted": fitted,
                    "mean_angle_deg": (sum(angles) / len(angles))
                    if angles else None})


@app.route("/api/review/save", methods=["POST"])
def api_review_save():
    values = st.load()
    try:
        session = _load_review(values)
    except (RuntimeError, st.SettingError, OSError) as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400

    payload = request.get_json(silent=True) or {}
    index = int(payload.get("idx", 0))
    if not (0 <= index < len(session["series"])):
        return jsonify({"ok": False, "error": "No recording at that index."}), 404

    entry = session["series"][index]
    with _review_lock:
        session["selections"][_key_for(entry)] = {
            "points": payload.get("points", []),
            "rises": payload.get("rises", []),
        }
        _save_selections()
    return jsonify({"ok": True, "saved": len(payload.get("points", []))})


@app.route("/api/review/export", methods=["POST"])
def api_review_export():
    values = st.load()
    try:
        session = _load_review(values)
    except (RuntimeError, st.SettingError, OSError) as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400

    from .. import events

    try:
        result = events.export(session["selections"], values,
                               in_csv=session["source"])
    except (RuntimeError, st.SettingError, OSError) as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({"ok": True, **result})


# ======================================================================
#  Serving
# ======================================================================

def serve(values: dict | None = None, landing: str = "/") -> None:
    values = values or st.load()
    host = values["HOST"]
    port = values["PORT"]
    url = f"http://{host}:{port}{landing}"

    print(f"facekit {__version__}")
    print(f"  interface at {url}")
    if host not in ("127.0.0.1", "localhost"):
        print("  WARNING bound to a non-loopback address. The interface has "
              "no authentication and its file browser can see the whole "
              "filesystem.")
    print("  Ctrl+C to stop")

    if values["OPEN_BROWSER"]:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()

    app.run(host=host, port=port, debug=False, threaded=True,
            use_reloader=False)
