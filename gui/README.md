# Findmatch native GUI

Rust/GPUI 0.2.2 frontend. `findmatch-gui --help` lists startup flags. Startup only
populates controls: search starts on Search or Enter, never automatically.
Empty query with one or more references performs reference-only search.

The backend is always the absolute executable in `FINDMATCH_GUI_PYTHON`, invoked
with `-m search_tool.cli` and literal argument arrays. It inherits PYTHONPATH and
native-library environment; it never invokes `--gui` or a shell. Backend stderr
prompts become separate processing/download consent overlays. Unknown prompts
are denied. Consent is never persisted or reused across searches. Cancel and
window close kill the process group, reap the backend and join pipe readers.
Results are limited, ranked, and constrained to existing files beneath the root.

Long libraries are supervised by progress, not by a total runtime budget:
`STALL_TIMEOUT` (15 minutes) is the longest the backend may stay *silent*, and
every stderr line — per-file and per-chunk indexing progress, download
percentages, backend selection — restarts it. Waiting for a consent decision
also suspends it, so leaving a dialog open cannot kill a healthy search. Only a
backend that stops reporting progress is terminated and reported as timed out.

Images use GPUI asynchronous path-backed images for cards and the selected
preview. The selected backend snippet, chunk location, score, kind and path are
shown. Audio/video/documents use `xdg-open` (no embedded decoder); PDF/Office
extracted snippets are still displayed. There is no GUI persistence or direct
index writing. Standard Latin/Unicode keyboard input, select-all, copy/cut/paste,
backspace/delete and cursor keys are implemented; complex IME composition and
mouse-positioned text selections are not implemented.

## Verification

From the repository root:

```sh
nix develop --command cargo test --manifest-path gui/Cargo.toml --locked
nix develop --command cargo clippy --manifest-path gui/Cargo.toml --all-targets --locked -- -D warnings
nix develop --command cargo build --manifest-path gui/Cargo.toml --locked
nix develop --command python3 gui/tests/native_smoke.py gui/target/debug/findmatch-gui
```

23 Rust tests cover launch flags, literal Python-module arguments/multiple references,
actual Unicode key events, actual rendered card/removal/decline clicks, separate
newline-free permission prompts, unknown-prompt denial, recursive ranked results
and metadata, cancellation/reaping, bounded progress, progress-based stall
detection (a reporting backend outlives the stall window, a silent one is still
killed, an open consent dialog is never a stall), and inherited-pipe timeout.
Tracer-bullet RED/GREEN cycles were exercised for flags, arguments, permission
protocol, keyboard input, reference/consent state, kill/reap, rendered selectors
and event backlog. Additional edge-case tests are regression coverage.

Native smoke runs only on a new isolated headless Sway runtime, captures a frame,
and closes its own processes. Native workflow QA also exercised processing
approval/decline, separate model-download decline, real EmbeddingGemma inference
with two references, ranked text previews, image selection/preview, and removal
of one attachment without removing the other. Evidence is retained under
`/home/barend/.hermes/cache/scratch/findmatch-gui-qa/`. Picker integration is covered
by GPUI's native platform API; an interactive portal picker was not exercised.
No model download was authorized during QA; real searches reused existing weights.

Cargo.lock is frozen for parent sandbox packaging. Vendored xattr preserves
upstream MIT/Apache licenses and the organizer's Linux ENODATA compatibility fix.
Build/test/clippy pass. Upstream proc-macro-error2 emits a future-incompatibility
notice; no application compiler/clippy warnings remain.
