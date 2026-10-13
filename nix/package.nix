{ lib, stdenv, python312, uv, fetchurl, makeWrapper, ffmpeg, poppler-utils, gui }:
let
  wheels = map (wheel: fetchurl { inherit (wheel) url sha256; name = wheel.name; })
    (builtins.fromJSON (builtins.readFile ./wheels-x86_64-linux.json));
  source = lib.fileset.toSource {
    root = ../.;
    fileset = lib.fileset.unions [ ../search_tool ../tests ../pyproject.toml ../README.md ../LICENSE ];
  };
in stdenv.mkDerivation {
  pname = "findmatch";
  version = "0.1.0";
  src = source;
  nativeBuildInputs = [ uv makeWrapper ];
  buildInputs = [ stdenv.cc.cc.lib ];
  dontConfigure = true;
  dontBuild = true;
  installPhase = ''
    runHook preInstall
    export UV_CACHE_DIR="$TMPDIR/uv-cache"
    mkdir -p "$TMPDIR/wheels"
    ${lib.concatMapStringsSep "\n" (wheel: "cp ${wheel} \"$TMPDIR/wheels/${wheel.name}\"") wheels}
    uv venv --python ${python312}/bin/python3.12 "$out/lib/findmatch/venv"
    uv pip install --python "$out/lib/findmatch/venv/bin/python" --offline --no-index \
      --find-links "$TMPDIR/wheels" ${lib.concatMapStringsSep " " (wheel: "\"$TMPDIR/wheels/${wheel.name}\"") wheels}
    mkdir -p "$out/lib/findmatch/source" "$out/share/doc/findmatch" "$out/bin"
    cp -r search_tool "$out/lib/findmatch/source/"
    cp README.md LICENSE "$out/share/doc/findmatch/"
    makeWrapper "$out/lib/findmatch/venv/bin/python" "$out/bin/findmatch" \
      --set FINDMATCH_GUI_BINARY "${gui}/bin/findmatch-gui" \
      --prefix PYTHONPATH : "$out/lib/findmatch/source" \
      --prefix PATH : ${lib.makeBinPath [ ffmpeg poppler-utils ]} \
      --prefix LD_LIBRARY_PATH : ${lib.makeLibraryPath [ stdenv.cc.cc.lib ]} \
      --add-flags "-m search_tool.cli"
    runHook postInstall
  '';
  doInstallCheck = true;
  installCheckPhase = ''
    runHook preInstallCheck
    export PYTHONPATH="$out/lib/findmatch/source"
    export LD_LIBRARY_PATH=${lib.makeLibraryPath [ stdenv.cc.cc.lib ]}
    export PATH=${lib.makeBinPath [ ffmpeg poppler-utils ]}:$PATH
    "$out/lib/findmatch/venv/bin/python" -m pytest tests/test_core.py tests/test_cli.py tests/test_gui.py tests/test_extract.py -q
    "$out/bin/findmatch" --help > "$TMPDIR/help"
    runHook postInstallCheck
  '';
  meta = {
    description = "Local multimodal file search using EmbeddingGemma 2";
    homepage = "https://github.com/Vanish-prog/search-tool";
    license = lib.licenses.mit;
    platforms = [ "x86_64-linux" ];
    mainProgram = "findmatch";
  };
}
