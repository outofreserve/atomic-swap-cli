#!/usr/bin/env bash
# Clones and builds the BoltzExchange/hold CLN plugin
# (https://github.com/BoltzExchange/hold) from source.
#
# This plugin is a Rust binary; building it requires a Rust toolchain
# and protoc. If you don't have Rust installed, the quickest path is:
#
#   curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y --profile minimal
#   source "$HOME/.cargo/env"
#
# and protoc via your package manager, e.g.:
#   sudo apt-get install -y protobuf-compiler   # Debian/Ubuntu
#   brew install protobuf                       # macOS
#
# Usage:
#   ./infra/build_hold_plugin.sh [ref]
#
# `ref` defaults to `main`. The built binary ends up at
# infra/hold-plugin/target/release/hold, which is exactly where
# start_lightningd.sh looks for it by default.
set -euo pipefail

REF="${1:-main}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CLONE_DIR="$SCRIPT_DIR/hold-plugin"

if ! command -v cargo >/dev/null 2>&1; then
  echo "ERROR: cargo (Rust toolchain) not found on PATH." >&2
  echo "Install it with: curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y --profile minimal" >&2
  echo "then 'source \$HOME/.cargo/env' and re-run this script." >&2
  exit 1
fi

# protoc: the hold plugin's build.rs shells out to a protoc binary via
# tonic-prost-build. If it's not already on PATH, fall back to
# downloading a pinned release build (works without sudo/root).
if ! command -v protoc >/dev/null 2>&1 && [ -z "${PROTOC:-}" ]; then
  VENDORED_PROTOC_DIR="$SCRIPT_DIR/.protoc"
  if [ ! -x "$VENDORED_PROTOC_DIR/bin/protoc" ]; then
    echo "protoc not found on PATH; downloading a pinned release build into $VENDORED_PROTOC_DIR"
    mkdir -p "$VENDORED_PROTOC_DIR"
    ARCH="$(uname -m)"
    case "$ARCH" in
      x86_64) PROTOC_ARCH="linux-x86_64" ;;
      aarch64|arm64) PROTOC_ARCH="linux-aarch_64" ;;
      *) echo "ERROR: no pinned protoc for arch $ARCH; install protoc manually." >&2; exit 1 ;;
    esac
    curl -sSL "https://github.com/protocolbuffers/protobuf/releases/download/v27.3/protoc-27.3-${PROTOC_ARCH}.zip" \
      -o "$VENDORED_PROTOC_DIR/protoc.zip"
    (cd "$VENDORED_PROTOC_DIR" && unzip -q -o protoc.zip)
  fi
  export PROTOC="$VENDORED_PROTOC_DIR/bin/protoc"
fi

# diesel (postgres + sqlite features) links against -lpq and -lsqlite3.
# Debian/Ubuntu ship the runtime .so.N files (libpq5, libsqlite3-0) but
# the unversioned .so symlinks needed for linking only come from the
# -dev packages. If you have sudo, the clean fix is:
#   sudo apt-get install -y libpq-dev libsqlite3-dev
# Without sudo, this script creates user-writable symlinks pointing at
# whatever runtime libs are already present and adds them to the
# linker search path via RUSTFLAGS.
if ! command -v pg_config >/dev/null 2>&1 && [ -z "${RUSTFLAGS:-}" ]; then
  LIBDIR="$HOME/.local/lib"
  mkdir -p "$LIBDIR"
  PQ_SO="$(ldconfig -p 2>/dev/null | awk '/libpq\.so\.[0-9]/{print $NF; exit}')"
  SQLITE_SO="$(ldconfig -p 2>/dev/null | awk '/libsqlite3\.so\.[0-9]/{print $NF; exit}')"
  if [ -n "$PQ_SO" ] && [ ! -e "$LIBDIR/libpq.so" ]; then
    ln -sf "$PQ_SO" "$LIBDIR/libpq.so"
    echo "no libpq-dev found; symlinked $LIBDIR/libpq.so -> $PQ_SO"
  fi
  if [ -n "$SQLITE_SO" ] && [ ! -e "$LIBDIR/libsqlite3.so" ]; then
    ln -sf "$SQLITE_SO" "$LIBDIR/libsqlite3.so"
    echo "no libsqlite3-dev found; symlinked $LIBDIR/libsqlite3.so -> $SQLITE_SO"
  fi
  if [ -e "$LIBDIR/libpq.so" ] || [ -e "$LIBDIR/libsqlite3.so" ]; then
    export RUSTFLAGS="-L $LIBDIR"
  fi
fi

if [ ! -d "$CLONE_DIR" ]; then
  echo "cloning BoltzExchange/hold@$REF into $CLONE_DIR"
  git clone --depth 1 --branch "$REF" https://github.com/BoltzExchange/hold "$CLONE_DIR" \
    || git clone https://github.com/BoltzExchange/hold "$CLONE_DIR"
fi

cd "$CLONE_DIR"
if [ "$REF" != "main" ]; then
  git fetch --depth 1 origin "$REF" && git checkout "$REF"
fi

echo "building (release) -- this can take several minutes the first time..."
cargo build --release

BIN="$CLONE_DIR/target/release/hold"
if [ -x "$BIN" ]; then
  echo "built: $BIN"
  echo "start_lightningd.sh will pick this up automatically via HOLD_PLUGIN_PATH default."
else
  echo "ERROR: build finished but binary not found at $BIN" >&2
  exit 1
fi
