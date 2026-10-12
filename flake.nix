{
  description = "findmatch: local multimodal directory search with EmbeddingGemma 2";
  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
  outputs = { self, nixpkgs }:
    let
      system = "x86_64-linux";
      pkgs = import nixpkgs { inherit system; };
      findmatch = pkgs.callPackage ./nix/package.nix {};
    in {
      packages.${system} = { inherit findmatch; default = findmatch; };
      apps.${system}.default = { type = "app"; program = "${findmatch}/bin/findmatch"; meta.description = "Local multimodal directory search"; };
      checks.${system}.findmatch = findmatch;
      devShells.${system}.default = pkgs.mkShell {
        packages = [ pkgs.python312 pkgs.uv pkgs.ffmpeg pkgs.poppler-utils ];
        LD_LIBRARY_PATH = pkgs.lib.makeLibraryPath [ pkgs.stdenv.cc.cc.lib ];
      };
    };
}
