{
  description = "findmatch: local multimodal directory search with EmbeddingGemma 2";
  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
  outputs = { self, nixpkgs }:
    let
      system = "x86_64-linux";
      pkgs = import nixpkgs { inherit system; };
      libs = with pkgs; [ libxkbcommon wayland vulkan-loader libGL fontconfig freetype openssl zlib libx11 libxcb libxcursor libxi libxrandr ];
      findmatch-gui = pkgs.callPackage ./nix/gui.nix { runtimeLibraries = libs; };
      findmatch = pkgs.callPackage ./nix/package.nix { gui = findmatch-gui; };
    in {
      packages.${system} = { inherit findmatch findmatch-gui; default = findmatch; };
      apps.${system}.default = { type = "app"; program = "${findmatch}/bin/findmatch"; meta.description = "Local multimodal directory search"; };
      checks.${system}.findmatch = findmatch;
      devShells.${system}.default = pkgs.mkShell {
        packages = (with pkgs; [ python312 uv ffmpeg poppler-utils cargo rustc rustfmt clippy pkg-config clang cmake makeWrapper ]) ++ libs;
        LIBCLANG_PATH = "${pkgs.libclang.lib}/lib";
        LD_LIBRARY_PATH = pkgs.lib.makeLibraryPath (libs ++ [ pkgs.stdenv.cc.cc.lib ]);
      };
    };
}
