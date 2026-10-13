# findmatch

Local Linux directory search powered by **EmbeddingGemma 2**, not a text-only substitute. Text prompts and reference images/audio/documents share the model's 768-dimensional embedding space. Processing and search are local; internet is needed only for installation and the initial model download.

## AI authorship and license

**This project is completely authored by AI**, including its application code, tests, packaging, and documentation, under human direction. It is experimental software; passing tests does not guarantee correctness or security. Review it before using it with sensitive files.

The project source is available under the **MIT License** (see [LICENSE](LICENSE)). Third-party dependencies and model weights retain their own licenses; the project's MIT license does not relicense them.

## Run

```sh
findmatch ~/Documents "antenna calibration procedures"
findmatch ~/Pictures "similar damage" --reference ./example.jpg
findmatch ~/Recordings --reference ./voice-query.wav --json --limit 5
```

New or changed files trigger **Process these files before searching? [y/N]**. Only `y` or `yes` proceeds. No, blank input, or EOF exits with code 2, without processing or searching. There is intentionally no automatic-yes option. On first use, a separate prompt authorizes downloading the model (484,622,336 bytes).

Unchanged files reuse SQLite embeddings. Deleted files are removed from the directory's index. `--rebuild` asks permission to replace the index. File change detection uses size, nanosecond modification time, and change time. Indexes are specific to a canonical directory path and model/extractor version.

Results contain absolute file paths, cosine scores, modality, best-matching chunk location, and snippets. One result per file, ranked by its highest-scoring chunk. Scores are similarities, **not probabilities**; top results may still be unrelated. JSON goes to stdout; progress, errors, and confirmations go to stderr.

## Native GUI

```sh
findmatch --gui
findmatch --gui ~/Pictures "similar damage" -r ./front.jpg -r ./side.jpg
```

The GPUI window uses the same local search engine and index as the CLI. Choose a directory, enter a query, and attach multiple reference files (or search with references alone). Remove attachments individually. Search and processing run off the UI thread; approval for indexing and model download is requested separately in the window. Cancel stops the running search.

Ranked results have image previews where supported. Select a result for its image or matching text snippet, path, location, modality, and similarity score. Audio/video and full documents can be opened with their default application; embedded media playback and full document rendering are not provided. Previewing a result does not modify it.

The Nix package includes the native frontend and its runtime libraries. Python-only installations keep the CLI but require a separately built frontend for `--gui`:

```sh
nix develop --command cargo build --manifest-path gui/Cargo.toml
./bin/findmatch --gui
# Rust tests (including GPUI event/render tests):
nix develop --command cargo test --manifest-path gui/Cargo.toml
```

`--limit`, `--cache-dir`, `--reference`, and `--rebuild` initialize the GUI. `--json` is CLI-only and cannot be combined with `--gui`. A graphical Wayland/X11 session with a working Vulkan driver is required; `--help` never starts a window or loads/downloads model weights.

## Supported content

- UTF-8/UTF-16 text, source code, CSV, JSON, configuration, and extensionless text: overlapping 1,800-character chunks.
- PDF: page text; pages without extractable text rendered as images using Poppler. Mixed text/graphic pages are currently indexed by text only.
- DOCX (paragraphs/tables), XLSX (sheet cells/formulas), PPTX (slide text/tables), ODT/ODS/ODP (XML text).
- Images supported by Pillow: normalized RGB, maximum 1,024px edge. Animated images and multi-page TIFF currently use the first frame/page only.
- Audio: FFmpeg-decoded mono 16kHz WAV segments every 20 seconds.
- Video: a frame every 5 seconds, plus audio segments when present. These are separate indexed chunks, not temporal video embeddings.
- Other binary formats: **filename/type/size metadata only**, explicitly labelled in results. This does not mean the tool understands arbitrary binaries. Legacy DOC/XLS/PPT and encrypted files are not content-supported.

Corrupt supported files stop the search with an error and remain unprocessed for the next attempt. Previously completed files stay indexed. Empty files without searchable content currently stop processing. Documents' embedded pictures, headers, notes, and formatting are not comprehensively extracted.

Limits: text 32 MiB, Office ZIP uncompressed content 64 MiB, recordings 2 hours; excessive images are rejected. References combine into one query embedding and must fit the model context. Split long references or use shorter clips. Media extraction has subprocess timeouts and never executes file contents. Symlink files/directories, `.git`, `.venv`, `__pycache__`, `node_modules`, and `.findmatch` directories are excluded.

## NixOS installation

The flake provides a self-contained **x86_64-linux** package: Python, locked Python wheels, FFmpeg, Poppler, the GPUI frontend, and native runtime paths are supplied by Nix. It does not depend on the checkout's virtual environment or `~/.local/bin`. ARM Linux and other architectures are not packaged yet.

```sh
# Run without permanently installing:
nix run github:Vanish-prog/search-tool -- --help

# Install into your user profile:
nix profile install github:Vanish-prog/search-tool
findmatch --help

# Build a local checkout:
nix build .#findmatch
./result/bin/findmatch --help
```

Flakes and `nix-command` must be enabled. For reproducible deployment, pin a Git commit or use the flake from your NixOS/Home Manager lockfile. Downloads happen during Nix dependency fetches; the package's install phase uses only hash-verified wheels offline. Model weights are not in the Nix package and retain the separate first-run download prompt.

To add it to a NixOS flake:

```nix
# In flake.nix inputs:
search-tool.url = "github:Vanish-prog/search-tool";

# In a module receiving inputs through specialArgs:
environment.systemPackages = [
  inputs.search-tool.packages.${pkgs.stdenv.hostPlatform.system}.findmatch
];
```

With Home Manager, use the same package expression in `home.packages`. No service is enabled automatically.

### Development installation

```sh
cd /path/to/search-tool
./install-nixos.sh
findmatch --help
```

The development installer builds a Nix-rooted runtime, syncs `uv.lock`, and links the checkout's launcher into `~/.local/bin`; keep the checkout in place and ensure that directory is on PATH. Prefer the flake package for installation on other machines.

For other Linux distributions with Python 3.12+ and FFmpeg/Poppler installed:

```sh
uv sync --frozen --extra test
uv run findmatch ./files "search query"
```

Default storage: `${XDG_CACHE_HOME:-~/.cache}/findmatch/` (model, indexes, locks). `--cache-dir` changes it and must be outside the directory being searched. Index databases are mode 0600 and contain snippets as well as vectors: treat them as private data. No cloud API, telemetry, or server is used. Only one search may update a given directory at once.

## Tests

```sh
PATH="$PWD/.nix-runtime/bin:$PATH" \
LD_LIBRARY_PATH="$PWD/.nix-runtime/lib" \
.venv/bin/python -m pytest -q
```

`tests/test_model.py` performs actual text, image, audio, and combined text/image inference and requires the downloaded weights in the default cache. CLI orchestration tests use a tiny explicitly test-only engine; no synthetic embeddings are used by the installed tool.

Demo:
```sh
findmatch ./examples/demo "RF antenna measurement and calibration"
findmatch ./examples/demo "red circle" -r ./examples/demo/circle.png
```

## Model provenance

- Model: https://huggingface.co/litert-community/embeddinggemma-2-740m-litert-lm
- Revision: `24d962e906c7d332c6428e71c9676855024569e2`
- File: `embeddinggemma-2-740m.litertlm`
- SHA-256: `e7a8a2204b91e0f96e92960e84a09a89212e1633dcb7575a9bf3378b4df77f4c`
- Runtime: LiteRT-LM 0.18.0, CPU backend (4 threads).
- Model card / task prefixes: https://huggingface.co/google/embeddinggemma-2

The quantized model is Apache-2.0 per its published model card. Downloaded weights are not bundled with the source. Both CLI and optional GPUI modes are available; a NixOS-managed background service is deferred.
