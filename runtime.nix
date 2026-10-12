{ pkgs ? import <nixpkgs> {} }:
pkgs.buildEnv {
  name = "findmatch-runtime";
  paths = [ pkgs.python312 pkgs.uv pkgs.ffmpeg pkgs.poppler-utils pkgs.stdenv.cc.cc.lib ];
  pathsToLink = [ "/bin" "/lib" ];
}
