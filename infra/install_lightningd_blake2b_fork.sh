#!/usr/bin/env bash
# Downloads and GPG-verifies the privkeyio/lightning (blake2b-unified)
# prebuilt lightningd binary release, required to run CLN against the
# "Bitcoin (blake2b)" / BTCB2 chain on testnet4 or mainnet (NOT needed
# for the regtest dev-mode, which uses stock lightningd for both
# simulated chains).
#
# Source:  https://github.com/privkeyio/lightning  (branch blake2b-unified)
# Release: v26.06.8-blake2b.5
# Signing key: A47D99B6DB0D715D40C59A2023AE8A8EA7E24E38
#
# The privkeyio/lightning README states this release ships reproducible
# prebuilt binaries for Ubuntu 22.04/24.04/26.04 with a signed
# SHA256SUMS file -- use the prebuilt binary rather than building from
# source where possible, but ALWAYS verify the GPG signature yourself
# before trusting the download; this script automates that check, it
# does not replace your own judgement.
#
# EXPERIMENTAL, UNAUDITED SOFTWARE per the privkeyio/lightning README.
# Do not point real mainnet funds at it until you have read that README
# and this project's own README.md risk disclosure in full.
set -euo pipefail

REPO="privkeyio/lightning"
TAG="v26.06.8-blake2b.5"
SIGNING_KEY="A47D99B6DB0D715D40C59A2023AE8A8EA7E24E38"
UBUNTU_CODENAME="${1:-$(lsb_release -cs 2>/dev/null || echo noble)}"

# Map codename -> the release-asset naming convention documented in the
# privkeyio/lightning release notes. Adjust if the actual asset names on
# the release page differ.
case "$UBUNTU_CODENAME" in
  jammy)   UBUNTU_TAG="22.04" ;;
  noble)   UBUNTU_TAG="24.04" ;;
  *)       UBUNTU_TAG="26.04" ;;
esac

WORKDIR="$(mktemp -d)"
trap 'rm -rf "$WORKDIR"' EXIT
cd "$WORKDIR"

ASSET="lightningd-blake2b-unified-${TAG}-ubuntu-${UBUNTU_TAG}.tar.gz"
BASE_URL="https://github.com/${REPO}/releases/download/${TAG}"

echo "== Downloading privkeyio/lightning blake2b-unified release $TAG (Ubuntu $UBUNTU_TAG) =="
curl -fL -o "$ASSET" "$BASE_URL/$ASSET"
curl -fL -o SHA256SUMS "$BASE_URL/SHA256SUMS"
curl -fL -o SHA256SUMS.asc "$BASE_URL/SHA256SUMS.asc"

echo "== Verifying GPG signature of SHA256SUMS (signing key $SIGNING_KEY) =="
if ! gpg --list-keys "$SIGNING_KEY" >/dev/null 2>&1; then
  echo "fetching signing key $SIGNING_KEY from keys.openpgp.org ..."
  gpg --keyserver hkps://keys.openpgp.org --recv-keys "$SIGNING_KEY"
fi
gpg --verify SHA256SUMS.asc SHA256SUMS
echo "GPG signature OK."

echo "== Verifying SHA256SUMS matches the downloaded asset =="
sha256sum --ignore-missing --check SHA256SUMS
echo "Checksum OK."

INSTALL_DIR="${ATOMIC_SWAP_RUNTIME_DIR:-$HOME/.atomic-swap-cli}/bin"
mkdir -p "$INSTALL_DIR"
tar -xzf "$ASSET"
INSTALLED_BIN="$INSTALL_DIR/lightningd-blake2b"
cp "$(find . -maxdepth 2 -type f -name lightningd | head -n1)" "$INSTALLED_BIN"
chmod +x "$INSTALLED_BIN"

echo
echo "Installed: $INSTALLED_BIN"
echo "Point ATOMIC_SWAP_<PROFILE>_BLAKE2B_LIGHTNINGD_BINARY at this path, e.g.:"
echo "  export ATOMIC_SWAP_MAINNET_BLAKE2B_LIGHTNINGD_BINARY=$INSTALLED_BIN"
echo "  export ATOMIC_SWAP_TESTNET4_BLAKE2B_LIGHTNINGD_BINARY=$INSTALLED_BIN"
echo
echo "Remember: this CLN fork requires/advertises peer feature bits"
echo "option_blake2b (512) and option_unified_sigs (514), and only"
echo "recognizes post-activation coins (block 961,640 mainnet / 150,308"
echo "testnet4) as safe to fund a channel with -- see safety.py / README.md."
