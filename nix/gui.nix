{ lib, rustPlatform, pkg-config, clang, cmake, libclang, makeWrapper, python312, xdg-utils, runtimeLibraries }:

rustPlatform.buildRustPackage {
  pname = "findmatch-gui";
  version = "0.1.0";
  src = lib.fileset.toSource {
    root = ../gui;
    fileset = lib.fileset.unions [ ../gui/Cargo.toml ../gui/Cargo.lock ../gui/src ../gui/vendor ];
  };
  cargoLock.lockFile = ../gui/Cargo.lock;
  nativeBuildInputs = [ pkg-config clang cmake makeWrapper ];
  nativeCheckInputs = [ python312 ];
  buildInputs = runtimeLibraries;
  LIBCLANG_PATH = "${libclang.lib}/lib";
  LD_LIBRARY_PATH = lib.makeLibraryPath runtimeLibraries;
  postInstall = ''
    wrapProgram "$out/bin/findmatch-gui" \
      --prefix PATH : "${lib.makeBinPath [ xdg-utils ]}" \
      --prefix LD_LIBRARY_PATH : "${lib.makeLibraryPath runtimeLibraries}"
  '';
  meta = {
    description = "Native GPUI frontend for findmatch";
    homepage = "https://github.com/Vanish-prog/search-tool";
    license = lib.licenses.mit;
    platforms = [ "x86_64-linux" ];
    mainProgram = "findmatch-gui";
  };
}
