#!/usr/bin/env bash
# Launches two INDEPENDENT bitcoind regtest instances to stand in for
# "Bitcoin (sha256)" and "Bitcoin (blake2b)". These are ordinary
# bitcoind regtest nodes with separate datadirs/ports/credentials; the
# hash-function names are cosmetic labels only (see atomic_swap_cli/config.py
# docstring for the full disclaimer). Nothing here talks to any real
# alternate-PoW network.
set -euo pipefail

RUNTIME_DIR="${ATOMIC_SWAP_RUNTIME_DIR:-$HOME/.atomic-swap-cli}"
mkdir -p "$RUNTIME_DIR"

start_node() {
  local label="$1" datadir="$2" rpcport="$3" p2pport="$4" rpcuser="$5" rpcpass="$6"
  mkdir -p "$datadir"
  if [ -f "$datadir/bitcoind.pid" ] && kill -0 "$(cat "$datadir/bitcoind.pid")" 2>/dev/null; then
    echo "[$label] already running (pid $(cat "$datadir/bitcoind.pid"))"
    return
  fi
  echo "[$label] starting bitcoind regtest on rpc:$rpcport p2p:$p2pport datadir:$datadir"
  local extra_args=()
  # NOTE: some bitcoind builds in this sandbox (a custom Bitcoin Knots test
  # build) gate acceptance of modern low-R / deterministic-nonce ECDSA
  # signatures (the default produced by CLN and other current wallet
  # software) behind a regtest-only test deployment confusingly named
  # "blake2b". Until it is activated, such signatures are rejected from
  # the mempool as "Signature opts in to the hardfork, which is not
  # active here" (mempool-script-verify-flag-failed) -- even though no
  # actual alternate-PoW/consensus change is involved. This is unrelated
  # to our cosmetic "Bitcoin (blake2b)" chain label and unrelated to real
  # swap/atomicity logic; it's a quirk of this one local build. We
  # activate it at height 1 (not 0, which is incompatible with genesis)
  # so modern signatures validate normally. No-op on a stock Bitcoin Core
  # build that doesn't have this flag.
  if bitcoind -datadir="$datadir" -help-debug 2>&1 | grep -q -- "-testactivationheight"; then
    extra_args+=(-testactivationheight=blake2b@1)
  fi
  bitcoind \
    -regtest \
    -datadir="$datadir" \
    -rpcuser="$rpcuser" \
    -rpcpassword="$rpcpass" \
    -rpcport="$rpcport" \
    -port="$p2pport" \
    -fallbackfee=0.0002 \
    -txindex=1 \
    -server=1 \
    -listen=1 \
    -daemon \
    -pid="$datadir/bitcoind.pid" \
    "${extra_args[@]}"
}

start_node "chain-sha256"   "$RUNTIME_DIR/bitcoin-sha256"   18443 18444 swapuser_sha256   swappass_sha256
start_node "chain-blake2b"  "$RUNTIME_DIR/bitcoin-blake2b"  18453 18454 swapuser_blake2b  swappass_blake2b

echo "waiting for RPC to come up..."
wait_for_rpc() {
  local datadir="$1" rpcport="$2" rpcuser="$3" rpcpass="$4"
  for _ in $(seq 1 30); do
    if bitcoin-cli -regtest -rpcuser="$rpcuser" -rpcpassword="$rpcpass" -rpcport="$rpcport" getblockchaininfo >/dev/null 2>&1; then
      return 0
    fi
    sleep 0.5
  done
  echo "timed out waiting for bitcoind on port $rpcport" >&2
  return 1
}
wait_for_rpc "$RUNTIME_DIR/bitcoin-sha256" 18443 swapuser_sha256 swappass_sha256
wait_for_rpc "$RUNTIME_DIR/bitcoin-blake2b" 18453 swapuser_blake2b swappass_blake2b

echo "both simulated chains are up."
echo "  Bitcoin (sha256)  [simulated regtest] -> rpcport 18443"
echo "  Bitcoin (blake2b) [simulated regtest] -> rpcport 18453"
