{
  description = "Nvox - modular forensic and steganography analysis framework";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs = { self, nixpkgs }:
    let
      systems = [
        "x86_64-linux"
        "aarch64-linux"
        "x86_64-darwin"
        "aarch64-darwin"
      ];
      forAllSystems = nixpkgs.lib.genAttrs systems;
    in
    {
      devShells = forAllSystems (system:
        let
          pkgs = import nixpkgs { inherit system; };

          # Optional tools used by Nvox bridges and reported by `nvox doctor`.
          # `jphide`, `jpseek` and `stegdetect` are not packaged in nixpkgs;
          # StegSolve is a Java jar and is only included on Linux.
          optionalTools = with pkgs; [
            # Generic / archives
            file
            binutils
            binwalk
            foremost
            p7zip
            unzip

            # Metadata / images
            exiftool
            imagemagick
            pngcheck
            jdk

            # Steganography
            zsteg
            steghide
            stegseek
            outguess

            # Barcodes / OCR
            zbar
            tesseract

            # Audio / video
            ffmpeg
            sox
            mediainfo
          ] ++ pkgs.lib.optionals pkgs.stdenv.isLinux [ pkgs.stegsolve ];
        in
        {
          default = pkgs.mkShell {
            packages = [
              pkgs.uv
              pkgs.python3
            ] ++ optionalTools;

            shellHook = ''
              echo "Nvox development shell"
              echo "  uv sync --group dev"
              echo "  uv run nvox doctor"
              echo "  uv run nvox scan <file>"
              echo ""
              echo "Not packaged in nixpkgs: jphide, jpseek, stegdetect."
              echo "StegSolve is GUI-only; the bridge needs a headless command wrapper."
            '';
          };
        });
    };
}
