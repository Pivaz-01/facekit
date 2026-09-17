# Changelog

All notable changes to facekit are recorded here. Versions follow
[semantic versioning](https://semver.org/).

## 1.0.0 - 2026-09-17

First release. facekit replaces four standalone scripts, each of which
had its configuration as constants at the top of the file and its own
copy of the shared geometry.

### Added

- One local interface for all four stages, at `http://127.0.0.1:7332`.
  Every value that used to be edited in the source is a field there,
  with the original explanation as help text.
- A settings registry in `facekit/settings.py` that generates the
  interface and the command line. Adding an option is one entry.
- Named presets, so one configuration per study arm or participant
  group can be kept and reloaded.
- A command line covering every stage, with `--set NAME=VALUE`
  overrides drawn from the same registry.
- `facekit env`, reporting the Python version and each stage's
  dependencies with the exact install command for whatever is missing.
- Per-stage optional dependencies: the mouth-area and event stages
  install without mediapipe or matplotlib.
- Review progress is saved per input file under `~/.facekit/review`, so
  the picker can be closed and reopened mid-dataset.
- Support for both MediaPipe face-mesh APIs, behind `facekit/mesh`. The
  scripts facekit replaces called `mp.solutions.face_mesh`, which is
  deprecated and absent from some mediapipe 0.10.x builds, so they fail
  on a fresh install with `module 'mediapipe' has no attribute
  'solutions'`. facekit detects what is available, prefers `solutions`
  where it exists, and falls back to the `tasks` FaceLandmarker,
  downloading its model once.
- `rise_oversmoothed`, set when a rise's spline slope falls below its
  own diagonal. That is impossible for a curve following the data, so
  the flag identifies exactly the rises whose slope cannot be trusted.

### Changed

- The two landmark scripts became one stage with a render mode. Drawing
  the mesh over the recording, drawing it on black, and not rendering
  at all were previously three-hundred-odd duplicated lines apart.
- Detection and slope measurement moved to Python. They previously
  existed twice, once in the browser for the figure and once in Python
  for the export, which is why the exported angle could differ from the
  one displayed.
- Head-roll correction orders the two irises by image position instead
  of by which landmark id was labelled left. The two scripts disagreed
  about which id was which eye, and with the labels swapped the
  rotation was out by half a turn.
- The left and right half-areas are assigned from where the labelled
  mouth corners actually fall, so a mirrored recording no longer swaps
  them silently. `MIRROR_MODE` can override the detection.
- Landmark rows stream to disk as they are produced. Accumulating a
  folder of recordings in a list before writing was the memory ceiling
  on long sessions.
- Stage 2 reads and writes in chunks, and by default carries only the
  landmarks the later stages read, which makes its output roughly
  fifteen times smaller.
- Downsampling reports how many frames in each window were usable, so
  a window that averaged two frames out of five is visible rather than
  implied.
- Rises record both the spline slope and the diagonal slope. The
  diagonal was previously kept "for reference" under a name that did
  not say it underestimates.
- The stage-1 dependency check verifies that a face-mesh API is
  actually reachable rather than that `import mediapipe` succeeds. The
  latter reported the stage ready and then failed on the first frame.

### Fixed

- The automatic spline smoothing factor. It was estimated from the
  variance of first differences, but a real opening has large first
  differences, so the movement was counted as noise and the spline was
  flattened until its derivative no longer described the rising edge.
  Measured against a synthetic signal with a known answer, every rise
  came out below its own diagonal slope, and the reported slopes decayed
  across a recording whose cycles were identical. The estimate now comes
  from the median absolute deviation of second differences, which is
  small for a smooth curve of any slope. On the same signal the
  recovered slope is within 3% of the true value given five or more
  samples per rise.
- The Review button navigated to the picker without saving the settings
  form, so a path typed and not saved was ignored and the picker read
  the previously saved value instead. Every route off the settings page
  now saves first, the Save button marks unsaved changes, and leaving
  the page with changes outstanding prompts.
- `_load_review` in the web app bypassed the path validation the stages
  use, so a folder in Mouth csv reached `pd.read_csv` and raised a bare
  `PermissionError` from inside pandas.
- Unhandled errors in an API route returned Flask's HTML traceback,
  which the interface could not parse, turning a clear server-side
  message into a JSON parse error. API routes now return JSON; page
  routes keep the traceback.
- Path settings holding a folder where a file was needed. On Windows
  this surfaced as `PermissionError: [Errno 13] Permission denied`
  naming only the folder, because that is how Windows reports opening a
  directory for writing. Output paths are now completed with the
  setting's natural filename and the substitution is logged; input
  paths are rejected with the folder's CSV files listed. The
  interface's folder picker also completes file fields itself, which is
  where the bare folder came from.
- A missing or zero inter-pupil distance no longer divides by zero; the
  frame is reported unusable instead.
- An incomplete lip contour is reported by name rather than producing a
  shoelace area over whichever points happened to be present.
- Stage 2 no longer silently writes nothing when no frame number is a
  multiple of the downsampling interval.
- The event export fails loudly when none of the selected frames are
  found in the input, which happened when the picker had been loaded
  from a different file than the one configured.
