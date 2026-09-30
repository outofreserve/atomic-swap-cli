#!/usr/bin/env bash
# Launches the 4 CLN (lightningd) nodes used by the demo: one Alice and
# one Bob per simulated chain, each pointed at its own bitcoind
# regtest instance and loading the BoltzExchange/hold plugin for real
# hold-invoice support.
#
# Requires the hold plugin binary to already be built -- see
# build_hold_plugin.sh / README.md. Set HOLD_PLUGIN_PATH to override
# its location (defaults to infra/hold-plugin/target/release/hold).
set -euo pipefail

RUNTIME_DIR="${ATOMIC_SWAP_RUNTIME_DIR:-$HOME/.atomic-swap-cli}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOLD_PLUGIN_PATH="${HOLD_PLUGIN_PATH:-$SCRIPT_DIR/hold-plugin/target/release/hold}"

if [ ! -x "$HOLD_PLUGIN_PATH" ]; then
  echo "WARNING: hold plugin not found/executable at $HOLD_PLUGIN_PATH" >&2
  echo "  lightningd will start WITHOUT hold-invoice support until you build it." >&2
  echo "  See infra/build_hold_plugin.sh and README.md." >&2
  PLUGIN_ARGS=()
else
  PLUGIN_ARGS=(--plugin="$HOLD_PLUGIN_PATH")
fi

# --- local-environment quirk workaround (no-op on stock CLN) --------------
# This sandbox's lightningd is a custom build (`lightningd --version` reports
# something like "v26.06.8-blake2b.5") that ships an extra developer-only
# flag, `--dev-blake2b-activation-height` (default: 0), which is unrelated to
# this project's cosmetic "Bitcoin (blake2b)" chain label -- it's a
# coincidental naming collision with a joke/test feature baked into this one
# build. With the default of 0 it behaves as if a fictitious "blake2b"
# feature is active from genesis, which makes lightningd require every
# invoice to advertise a feature bit named `option_blake2b`; since our hold
# invoices (created via the hold plugin) don't set that bit, `pay` on a
# *counterparty's* node rejects them with "invoice does not set
# option_blake2b". We work around this by pushing the activation height far
# into the future so the fictitious feature never activates. On stock Core
# Lightning this flag simply doesn't exist, so we detect support for it
# first and only pass it when present.
EXTRA_ARGS=()
if lightningd --help 2>/dev/null | grep -q -- "--dev-blake2b-activation-height"; then
  # dev-* options require developer mode to be enabled.
  EXTRA_ARGS+=(--developer --dev-blake2b-activation-height=1)
fi

start_node() {
  local label="$1" lightning_dir="$2" addr_port="$3" bitcoin_rpcport="$4" \
        bitcoin_rpcuser="$5" bitcoin_rpcpass="$6" grpc_port="$7"
  mkdir -p "$lightning_dir"
  if [ -f "$lightning_dir/regtest/lightningd-regtest.pid" ] && \
     kill -0 "$(cat "$lightning_dir/regtest/lightningd-regtest.pid")" 2>/dev/null; then
    echo "[$label] already running"
    return
  fi
  echo "[$label] starting lightningd on port $addr_port (hold grpc port $grpc_port)"
  lightningd \
    --lightning-dir="$lightning_dir" \
    --network=regtest \
    --bitcoin-rpcuser="$bitcoin_rpcuser" \
    --bitcoin-rpcpassword="$bitcoin_rpcpass" \
    --bitcoin-rpcport="$bitcoin_rpcport" \
    --addr="127.0.0.1:$addr_port" \
    --daemon \
    --log-file="$lightning_dir/log" \
    --disable-plugin=cln-grpc \
    --hold-grpc-port="$grpc_port" \
    "${PLUGIN_ARGS[@]}" \
    "${EXTRA_ARGS[@]}"
}

start_node "alice-sha256"  "$RUNTIME_DIR/lightning-alice-sha256"  19735 18443 swapuser_sha256  swappass_sha256  20001
start_node "bob-sha256"    "$RUNTIME_DIR/lightning-bob-sha256"    19736 18443 swapuser_sha256  swappass_sha256  20002
start_node "alice-blake2b" "$RUNTIME_DIR/lightning-alice-blake2b" 19745 18453 swapuser_blake2b swappass_blake2b 20003
start_node "bob-blake2b"   "$RUNTIME_DIR/lightning-bob-blake2b"   19746 18453 swapuser_blake2b swappass_blake2b 20004

echo "all 4 CLN nodes started (or already running). Check logs under \$lightning-dir/log."
