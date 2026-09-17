"""
The command line.

Useful for batch work and for scripting around the tool. Anything not
given on the command line comes from the saved settings, so the
interface and the command line always agree.

    facekit env                      check the install
    facekit settings                  print current settings as JSON
    facekit landmarks FOLDER [...] -o LANDMARKS.csv
    facekit mouth LANDMARKS.csv -o MOUTH.csv
    facekit events MOUTH.csv -o EVENTS.csv        (unreviewed)
    facekit review                                (the picker)
    facekit metrics EVENTS.csv -o PLOTS_DIR
    facekit serve --port 7332

Every setting is addressable by its upper-case name:

    facekit metrics ./events.csv -o ./plots \\
      --set CENTRAL_TENDENCIES=median --set BEST_QUARTILE=false
"""

from __future__ import annotations

import argparse
import json
import sys

from . import __version__, env_report
from . import settings as st


def _log(message: str) -> None:
    print(message, flush=True)


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--set", action="append", metavar="NAME=VALUE", dest="overrides",
        help="Override any setting for this run. Repeatable. Names are the "
             "upper-case ones listed by `facekit settings`.")
    parser.add_argument(
        "--preset", metavar="NAME",
        help="Load a saved preset before applying --set overrides.")
    parser.add_argument(
        "--save", action="store_true",
        help="Also write the resulting settings back as the new defaults.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="facekit",
        description="Facial movement analysis for clinical video.")
    parser.add_argument("--version", action="version",
                        version=f"facekit {__version__}")
    subparsers = parser.add_subparsers(dest="command")

    env = subparsers.add_parser(
        "env", help="Report the Python version and each stage's dependencies.")
    env.add_argument("--json", action="store_true",
                     help="Machine-readable output.")

    settings_cmd = subparsers.add_parser(
        "settings", help="Print, list or change saved settings.")
    settings_cmd.add_argument(
        "--describe", action="store_true",
        help="List every setting with its type, default and help text.")
    settings_cmd.add_argument(
        "--presets", action="store_true", help="List saved presets.")
    _add_common(settings_cmd)

    landmarks = subparsers.add_parser(
        "landmarks", help="Stage 1: extract the face mesh from video.")
    landmarks.add_argument("folders", nargs="*", metavar="FOLDER",
                           help="Input folders. Defaults to Video folders.")
    landmarks.add_argument("-o", "--out", metavar="CSV",
                           help="Output CSV. Defaults to Landmarks csv.")
    _add_common(landmarks)

    mouth = subparsers.add_parser(
        "mouth", help="Stage 2: mouth area, normalised and downsampled.")
    mouth.add_argument("input", nargs="?", metavar="CSV",
                       help="Stage 1 output. Defaults to Landmarks csv.")
    mouth.add_argument("-o", "--out", metavar="CSV",
                       help="Output CSV. Defaults to Mouth csv.")
    _add_common(mouth)

    events = subparsers.add_parser(
        "events",
        help="Stage 3 without review: detect peaks and valleys and export.")
    events.add_argument("input", nargs="?", metavar="CSV",
                        help="Stage 2 output. Defaults to Mouth csv.")
    events.add_argument("-o", "--out", metavar="CSV",
                        help="Output CSV. Defaults to Events csv.")
    _add_common(events)

    review = subparsers.add_parser(
        "review", help="Stage 3: open the picker to review peaks and valleys.")
    _add_common(review)

    metrics = subparsers.add_parser(
        "metrics", help="Stage 4: measure the reviewed frames and plot.")
    metrics.add_argument("input", nargs="?", metavar="CSV",
                         help="Stage 3 output. Defaults to Events csv.")
    metrics.add_argument("-o", "--out", metavar="DIR",
                         help="Output folder. Defaults to Plots dir.")
    _add_common(metrics)

    serve = subparsers.add_parser("serve", help="Start the interface.")
    serve.add_argument("--port", type=int, help="Override the saved port.")
    serve.add_argument("--host", help="Override the saved bind address.")
    serve.add_argument("--no-browser", action="store_true",
                       help="Do not open a browser window.")
    _add_common(serve)

    return parser


def _resolve(args) -> dict:
    """Saved settings, then a preset, then --set overrides."""
    overrides = {}
    if getattr(args, "preset", None):
        overrides.update(st.load_preset(args.preset))
    overrides.update(st.parse_set_flags(getattr(args, "overrides", None)))
    values = st.load(overrides)
    if getattr(args, "save", False):
        path = st.save(values)
        _log(f"Settings saved to {path}")
    return values


# ======================================================================
#  Commands
# ======================================================================

def cmd_env(args) -> int:
    report = env_report()
    if args.json:
        print(json.dumps(report, indent=2))
        return 0 if report["all_ok"] else 1

    _log(f"facekit {report['version']} on Python {report['python']} "
         f"({report['platform']})")
    if not report["python_ok"]:
        _log("  Python 3.10 to 3.12 is the supported range. MediaPipe "
             "publishes no wheels above 3.12.")
    _log("")
    for stage in report["stages"]:
        title = st.STAGE_TITLES[stage["stage"]]
        if stage["ok"]:
            _log(f"  ready    {stage['stage']:<10} {title}")
        else:
            _log(f"  MISSING  {stage['stage']:<10} {title}")
            if stage["missing"]:
                _log(f"           needs {', '.join(stage['missing'])}")
                _log(f"           {stage['install']}")
            if stage.get("note"):
                _log(f"           {stage['note']}")
        if stage.get("backends") is not None:
            available = ", ".join(stage["backends"]) or "none"
            _log(f"           mediapipe {stage.get('mediapipe')}, "
                 f"face-mesh API available: {available}")
    _log("")
    _log(f"  settings {report['settings_file']}")
    if report["dll_dirs"]:
        _log("  native library directories registered:")
        for path in report["dll_dirs"]:
            _log(f"    {path}")
    return 0 if report["all_ok"] else 1


def cmd_settings(args) -> int:
    if args.presets:
        names = st.list_presets()
        _log("\n".join(names) if names else "No presets saved.")
        return 0

    if args.describe:
        for group in st.GROUP_ORDER:
            _log(f"\n{group}")
            _log("-" * len(group))
            for spec in st.SETTINGS:
                if spec.group != group:
                    continue
                flag = " (advanced)" if spec.advanced else ""
                _log(f"  {spec.name}  [{spec.type}]{flag}")
                _log(f"    default: {spec.default!r}")
                if spec.choices:
                    _log(f"    one of: {', '.join(spec.choices)}")
                _log(f"    stages: {', '.join(spec.stages)}")
                for line in _wrap(spec.help, 68):
                    _log(f"    {line}")
        return 0

    print(json.dumps(_resolve(args), indent=2, sort_keys=True))
    return 0


def _wrap(text: str, width: int) -> list[str]:
    import textwrap

    return textwrap.wrap(text, width) or [""]


def _run_stage(name: str, function, args, **kwargs) -> int:
    values = _resolve(args)
    try:
        result = function(values=values, log=_log, **kwargs)
    except (RuntimeError, st.SettingError) as exc:
        _log(f"\n{name} failed: {exc}")
        return 1
    if result and result.get("csv"):
        _log(f"\n{name}: {result['csv']}")
    return 0


def cmd_landmarks(args) -> int:
    from . import landmarks

    return _run_stage("landmarks", landmarks.run, args,
                      folders=args.folders or None, out_csv=args.out)


def cmd_mouth(args) -> int:
    from . import mouth

    return _run_stage("mouth", mouth.run, args,
                      in_csv=args.input, out_csv=args.out)


def cmd_events(args) -> int:
    from . import events

    _log("Detecting without review. Automatic detection marks swallows and "
         "speech as readily as the movement you asked for; open the picker "
         "before reporting from this.\n")
    return _run_stage("events", events.run, args,
                      in_csv=args.input, out_csv=args.out)


def cmd_metrics(args) -> int:
    from . import metrics

    values = _resolve(args)
    try:
        result = metrics.run(values=values, in_csv=args.input,
                             out_dir=args.out, log=_log)
    except (RuntimeError, st.SettingError) as exc:
        _log(f"\nmetrics failed: {exc}")
        return 1
    _log(f"\nmetrics: {result['out_dir']}")
    return 0


def cmd_serve(args) -> int:
    from .web.app import serve

    overrides = {}
    if getattr(args, "port", None):
        overrides["PORT"] = args.port
    if getattr(args, "host", None):
        overrides["HOST"] = args.host
    if getattr(args, "no_browser", False):
        overrides["OPEN_BROWSER"] = False

    args.overrides = (args.overrides or []) + [
        f"{k}={v}" for k, v in overrides.items()]
    values = _resolve(args)
    serve(values)
    return 0


def cmd_review(args) -> int:
    from .web.app import serve

    values = _resolve(args)
    serve(values, landing="/review")
    return 0


COMMANDS = {
    "env": cmd_env,
    "settings": cmd_settings,
    "landmarks": cmd_landmarks,
    "mouth": cmd_mouth,
    "events": cmd_events,
    "review": cmd_review,
    "metrics": cmd_metrics,
    "serve": cmd_serve,
}


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if not args.command:
        return cmd_serve(parser.parse_args(["serve"]))

    try:
        return COMMANDS[args.command](args)
    except st.SettingError as exc:
        _log(f"Setting error: {exc}")
        return 2
    except KeyboardInterrupt:
        _log("\nInterrupted.")
        return 130


if __name__ == "__main__":
    sys.exit(main())
