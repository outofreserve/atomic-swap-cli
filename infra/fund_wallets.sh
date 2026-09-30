#!/usr/bin/env bash
# Creates wallets and mines initial blocks on both simulated chains so
# there are spendable funds for opening lightning channels. Idempotent.
set -euo pipefail

RUNTIME_DIR="${ATOMIC_SWAP_RUNTIME_DIR:-$HOME/.atomic-swap-cli}"

fund_chain() {
  local label="$1" rpcport="$2" rpcuser="$3" rpcpass="$4"
  local cli=(bitcoin-cli -regtest -rpcuser="$rpcuser" -rpcpassword="$rpcpass" -rpcport="$rpcport")

  "${cli[@]}" createwallet "default" >/dev/null 2>&1 || "${cli[@]}" loadwallet "default" >/dev/null 2>&1 || true

  local addr
  addr="$("${cli[@]}" getnewaddress "mining" "bech32")"
  echo "[$label] mining 110 blocks to $addr (100 to mature coinbase + buffer)"
  "${cli[@]}" generatetoaddress 110 "$addr" >/dev/null
  local balance
  balance="$("${cli[@]}" getbalance)"
  echo "[$label] wallet balance: $balance BTC"
}

fund_chain "chain-sha256"  18443 swapuser_sha256  swappass_sha256
fund_chain "chain-blake2b" 18453 swapuser_blake2b swappass_blake2b
