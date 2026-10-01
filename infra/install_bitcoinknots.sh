#!/usr/bin/env bash
# Builds/installs the Bitcoin Knots BLAKE2b fork's bitcoind, which is
# required to run the "Bitcoin (blake2b)" / BTCB2 side of this project
# against testnet4 or mainnet (NOT needed for the regtest dev-mode, which
# uses stock bitcoind for both simulated chains).
#
# Source: https://github.com/bitcoinknots/bitcoin
# Tag:    v29.4.2.knots20260508
#
# This builds from source because (unlike the privkeyio/lightning CLN
# fork) Bitcoin Knots does not ship prebuilt binaries for this tag as of
# writing -- verify https://github.com/bitcoinknots/bitcoin/releases for
# an updated binary release before running this script, since using an
# official signed binary is always preferable to building from source
# yourself.
#
# This script only builds+installs the binary; it does NOT start or
# configure a node. See README.md for datadir/config and how to point
# this project's env vars (ATOMIC_SWAP_*_BLAKE2B_*) at it.
set -euo pipefail

REPO_URL="https://github.com/bitcoinknots/bitcoin.git"
TAG="v29.4.2.knots20260508"
BUILD_DIR="${ATOMIC_SWAP_RUNTIME_DIR:-$HOME/.atomic-swap-cli}/build/bitcoinknots"
INSTALL_BIN="${1:-$HOME/.atomic-swap-cli/bin/bitcoind-knots-blake2b}"

echo "== Building Bitcoin Knots BLAKE2b fork bitcoind =="
echo "   repo: $REPO_URL (tag $TAG)"
echo "   this binary is for the 'Bitcoin (blake2b)' / BTCB2 chain only;"
echo "   it is EXPERIMENTAL, UNAUDITED software per its own README -- do"
echo "   not point real mainnet funds at it without reading that README."
echo

mkdir -p "$(dirname "$INSTALL_BIN")"
mkdir -p "$BUILD_DIR"

if [ ! -d "$BUILD_DIR/.git" ]; then
  git clone --branch "$TAG" --depth 1 "$REPO_URL" "$BUILD_DIR"
else
  (cd "$BUILD_DIR" && git fetch --depth 1 origin "$TAG" && git checkout "FETCH_HEAD")
fi

cd "$BUILD_DIR"
echo "== Configuring (depends: autotools, libtool, boost, libevent, berkeley-db;"
echo "   see bitcoinknots/bitcoin's doc/build-unix.md for your distro) =="
./autogen.sh
./configure --without-gui --disable-tests --disable-bench
echo "== Building (this can take 20-60+ minutes) =="
make -j"$(nproc)"

cp src/bitcoind "$INSTALL_BIN"
chmod +x "$INSTALL_BIN"
echo
echo "Installed: $INSTALL_BIN"
echo "Point ATOMIC_SWAP_<PROFILE>_BLAKE2B_BITCOIND_BINARY at this path, e.g.:"
echo "  export ATOMIC_SWAP_MAINNET_BLAKE2B_BITCOIND_BINARY=$INSTALL_BIN"
echo "  export ATOMIC_SWAP_TESTNET4_BLAKE2B_BITCOIND_BINARY=$INSTALL_BIN"
