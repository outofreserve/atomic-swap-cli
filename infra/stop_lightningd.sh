#!/usr/bin/env bash
# Stops the 4 CLN nodes started by start_lightningd.sh.
set -euo pipefail

RUNTIME_DIR="${ATOMIC_SWAP_RUNTIME_DIR:-$HOME/.atomic-swap-cli}"

stop_node() {
  local label="$1" lightning_dir="$2"
  local socket="$lightning_dir/regtest/lightning-rpc"
  if [ -S "$socket" ]; then
    echo "[$label] stopping via lightning-cli"
    lightning-cli --lightning-dir="$lightning_dir" --network=regtest stop >/dev/null 2>&1 || true
  else
    echo "[$label] not running (no rpc socket)"
  fi
}

stop_node "alice-sha256"  "$RUNTIME_DIR/lightning-alice-sha256"
stop_node "bob-sha256"    "$RUNTIME_DIR/lightning-bob-sha256"
stop_node "alice-blake2b" "$RUNTIME_DIR/lightning-alice-blake2b"
stop_node "bob-blake2b"   "$RUNTIME_DIR/lightning-bob-blake2b"
