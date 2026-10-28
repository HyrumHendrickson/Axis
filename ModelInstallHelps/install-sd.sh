#!/usr/bin/env bash
# Installs stable-diffusion.cpp (sd-cli and sd-server) for Axis on macOS or Linux.
#
#   bash install-sd.sh            pick the build for this computer
#   bash install-sd.sh vulkan     Linux: GPU build using Vulkan (NVIDIA, AMD, Intel)
#   bash install-sd.sh rocm       Linux: GPU build for AMD ROCm
#   bash install-sd.sh cpu        Linux: CPU only
#
# Installs to ~/sd (set SD_DIR to change it) and points Axis at it with AXIS_SD_CLI.
set -euo pipefail

REPO="leejet/stable-diffusion.cpp"
DEST="${SD_DIR:-$HOME/sd}"
VARIANT="${1:-auto}"
OS="$(uname -s)"
ARCH="$(uname -m)"

fail() { echo "Error: $*" >&2; exit 1; }
for tool in curl unzip; do
  command -v "$tool" >/dev/null || fail "$tool is not installed. Install it and run this again."
done

# --- choose the build -------------------------------------------------------
case "$OS" in
  Darwin)
    [ "$ARCH" = "arm64" ] || fail "There is no prebuilt release for Intel Macs. Build from source:
  git clone --recursive https://github.com/$REPO && cd stable-diffusion.cpp
  cmake -B build -DSD_METAL=OFF && cmake --build build --config Release -j
Then copy build/bin/sd-cli and build/bin/sd-server to $DEST."
    PATTERN="-bin-Darwin-.*-arm64\.zip$"
    LABEL="macOS (Apple Silicon, Metal)"
    ;;
  Linux)
    [ "$ARCH" = "x86_64" ] || fail "There is no prebuilt release for $ARCH Linux. Build from source (see the stable-diffusion.cpp README)."
    if [ "$VARIANT" = "auto" ]; then
      if command -v vulkaninfo >/dev/null && vulkaninfo --summary >/dev/null 2>&1; then
        VARIANT="vulkan"
      else
        VARIANT="cpu"
        if command -v nvidia-smi >/dev/null || lspci 2>/dev/null | grep -qiE "vga.*(amd|ati|nvidia)"; then
          echo "Note: a GPU was found, but Vulkan is not set up, so the CPU build is used."
          echo "      For the much faster GPU build: sudo apt install libvulkan1 vulkan-tools mesa-vulkan-drivers"
          echo "      (NVIDIA: the proprietary driver includes Vulkan), then run: bash install-sd.sh vulkan"
        fi
      fi
    fi
    case "$VARIANT" in
      cpu)    PATTERN="-bin-Linux-.*-x86_64\.zip$"; LABEL="Linux, CPU" ;;
      vulkan) PATTERN="-bin-Linux-.*-x86_64-vulkan\.zip$"; LABEL="Linux, Vulkan GPU" ;;
      rocm)   PATTERN="-bin-Linux-.*-x86_64-rocm-.*\.zip$"; LABEL="Linux, AMD ROCm GPU" ;;
      *)      fail "Unknown build '$VARIANT'. Use cpu, vulkan or rocm." ;;
    esac
    ;;
  *) fail "This script is for macOS and Linux. On Windows use install-sd.ps1." ;;
esac

# --- find the latest release -----------------------------------------------
echo "Looking up the latest stable-diffusion.cpp release..."
LATEST="$(curl -fsSLI -o /dev/null -w '%{url_effective}' "https://github.com/$REPO/releases/latest")" \
  || fail "Could not reach github.com. Check your internet connection."
TAG="${LATEST##*/}"
[ -n "$TAG" ] && [ "$TAG" != "latest" ] || fail "Could not find the latest release."
ASSETS="$(curl -fsSL "https://github.com/$REPO/releases/expanded_assets/$TAG" \
  | grep -o "/$REPO/releases/download/[^\"]*\.zip" | sort -u)" || fail "Could not list the release files."
ASSET="$(printf '%s\n' "$ASSETS" | grep -E -- "$PATTERN" | head -n 1 || true)"
[ -n "$ASSET" ] || fail "Release $TAG has no build for $LABEL. Files available:
$(printf '%s\n' "$ASSETS" | sed 's#.*/#  #')"
URL="https://github.com$ASSET"
echo "Release $TAG, build for $LABEL:"
echo "  ${ASSET##*/}"

# --- download and unpack ---------------------------------------------------
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
curl -fL --retry 3 --progress-bar -o "$TMP/sd.zip" "$URL" || fail "The download failed. Run this again."
unzip -tq "$TMP/sd.zip" >/dev/null || fail "The download is damaged. Run this again."
mkdir -p "$TMP/x"
unzip -q "$TMP/sd.zip" -d "$TMP/x"

# The archive is flat, but accept a top-level folder or bin/ too.
SRC="$(dirname "$(find "$TMP/x" -type f -name sd-cli | head -n 1)")"
[ -f "$SRC/sd-cli" ] || fail "sd-cli is not in the downloaded archive."
mkdir -p "$DEST"
cp -Rf "$SRC"/. "$DEST"/
chmod +x "$DEST/sd-cli" "$DEST"/sd-server 2>/dev/null || true
if [ "$OS" = "Darwin" ]; then
  xattr -dr com.apple.quarantine "$DEST" 2>/dev/null || true   # allow the unsigned programs to run
fi

# --- check it runs ---------------------------------------------------------
export LD_LIBRARY_PATH="$DEST${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
if ! VERSION="$("$DEST/sd-cli" --version 2>&1)"; then
  echo "$VERSION" >&2
  fail "sd-cli was installed in $DEST but does not run. The message above names the missing library or driver."
fi
echo "Installed: $VERSION"
[ -x "$DEST/sd-server" ] && echo "sd-server is included, so Axis can keep image models on." \
  || echo "Note: this release has no sd-server; Axis will use sd-cli for each picture."

# --- point Axis at it ------------------------------------------------------
LINE="export AXIS_SD_CLI=\"$DEST/sd-cli\""
case "${SHELL##*/}" in
  zsh)  PROFILES=("$HOME/.zshrc") ;;
  bash) PROFILES=("$HOME/.bashrc"); [ "$OS" = "Darwin" ] && PROFILES+=("$HOME/.bash_profile") ;;
  *)    PROFILES=("$HOME/.profile") ;;
esac
for p in "${PROFILES[@]}"; do
  touch "$p"
  grep -v '^export AXIS_SD_CLI=' "$p" > "$TMP/profile" || true
  printf '%s\n' "$LINE" >> "$TMP/profile"
  cat "$TMP/profile" > "$p"
done
echo
echo "Done. Open a new terminal and run axis."
echo "Then on the Models page, choose Image model under Install a model."
