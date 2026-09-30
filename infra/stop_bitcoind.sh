#!/usr/bin/env bash
# Stops the two simulated regtest bitcoind instances started by
# start_bitcoind.sh.
set -euo pipefail

RUNTIME_DIR="${ATOMIC_SWAP_RUNTIME_DIR:-$HOME/.atomic-swap-cli}"

stop_node() {
  local label="$1" datadir="$2" rpcport="$3" rpcuser="$4" rpcpass="$5"
  if [ -f "$datadir/bitcoind.pid" ]; then
    local pid
    pid="$(cat "$datadir/bitcoind.pid")"
    if kill -0 "$pid" 2>/dev/null; then
      echo "[$label] stopping bitcoind (pid $pid)"
      bitcoin-cli -regtest -rpcuser="$rpcuser" -rpcpassword="$rpcpass" -rpcport="$rpcport" stop >/dev/null 2>&1 || kill "$pid"
      for _ in $(seq 1 20); do
        kill -0 "$pid" 2>/dev/null || break
        sleep 0.5
      done
    else
      echo "[$label] not running"
    fi
  else
    echo "[$label] no pidfile found, nothing to stop"
  fi
}

stop_node "chain-sha256"  "$RUNTIME_DIR/bitcoin-sha256"  18443 swapuser_sha256  swappass_sha256
stop_node "chain-blake2b" "$RUNTIME_DIR/bitcoin-blake2b" 18453 swapuser_blake2b swappass_blake2b
