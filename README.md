# atomic-swap-cli

A Python CLI for cross-chain **atomic swaps over Lightning Network**
("submarine swaps") between two simulated Bitcoin-like networks, using
real [Core Lightning](https://github.com/ElementsProject/lightning) (CLN)
nodes and the real [BoltzExchange/hold](https://github.com/BoltzExchange/hold)
plugin for true hold-invoice atomicity.

This is a **personal dev/demo project**. No real funds, no mainnet, no
production deployment. Everything runs on `regtest`.

---

## What's simulated vs. what's real

| Component | Status |
|---|---|
| "Bitcoin (sha256)" / "Bitcoin (blake2b)" networks | **Simulated.** Two ordinary, independent `bitcoind` **regtest** instances with separate datadirs, ports, and RPC credentials. Regtest has no real proof-of-work difficulty — blocks are mined on demand. The `sha256`/`blake2b` labels are **cosmetic only**; nothing about swap logic depends on either chain's nominal hash function, and neither chain is a real alternate-PoW fork of Bitcoin. They exist purely so CLI output/docs can clearly distinguish "chain A" from "chain B" for a human running the demo. |
| Lightning nodes | **Real.** Four genuine `lightningd` (Core Lightning) processes — Alice and Bob, one each per simulated chain — each connected to its own `bitcoind` instance and forming a real Lightning channel with its counterparty. |
| Hold invoices / atomicity | **Real**, via the real [BoltzExchange/hold](https://github.com/BoltzExchange/hold) CLN plugin, which accepts an HTLC without auto-settling it until the preimage is explicitly released through the plugin's gRPC/JSON-RPC API. This is what makes the swap genuinely atomic rather than a simplified/unsafe demo that trusts timing. |
| Swap state machine / CLI | **Real**, this repo's Python code (`atomic_swap_cli/`), persisted to a local SQLite DB so it's resumable across process restarts. |

**⚠️ Local sandbox workaround note:** the development sandbox this project
was built/tested in ships a non-standard `lightningd` build that
unconditionally requires BOLT9 feature bit 512 (a custom, non-upstream
`option_blake2b` feature) on every invoice it pays — completely unrelated to
this project's cosmetic chain-naming scheme, and not something you'll hit on
a stock Core Lightning install. To work around it in this environment we:

1. Patched the hold plugin's Rust invoice encoder (`infra/hold-plugin/src/encoder.rs`)
   to set that bit manually before signing.
2. Vendored a locally-patched copy of the `lightning-invoice` crate
   (`infra/hold-plugin/vendor/lightning-invoice-0.34.0-patched/`) so the
   plugin's own BOLT11 re-validation of its stored invoices tolerates that
   one specific bit instead of correctly (per spec) rejecting an unknown
   required bit.

Both patches are heavily commented in-place as **local-sandbox-only**
workarounds. **If you're running this on a stock Core Lightning build (the
normal case), you almost certainly don't need either patch** — you can build
the plugin straight from the unmodified upstream `hold` repo. They're kept
here only so the demo works out-of-the-box in the environment it was
developed in.

---

## Architecture

```
          "Bitcoin (sha256)" regtest         "Bitcoin (blake2b)" regtest
          (bitcoind, port 18443)             (bitcoind, port 18453)
             ^            ^                      ^            ^
             |            |                      |            |
   lightningd(alice)  lightningd(bob)    lightningd(alice)  lightningd(bob)
   + hold plugin      + hold plugin      + hold plugin      + hold plugin
   alice-sha256       bob-sha256         alice-blake2b      bob-blake2b
             \____________/                      \____________/
              LN channel                          LN channel

                    atomic_swap_cli (Python CLI)
        talks to lightningd via JSON-RPC (unix sockets)
        talks to hold plugin via its JSON-RPC commands
        (holdinvoice / settleholdinvoice / cancelholdinvoice / listholdinvoices)
        persists swap state to ~/.atomic-swap-cli/state/swaps.sqlite3
```

A swap works like a standard submarine-swap / HTLC handshake, just across
two independently-running Lightning networks instead of on-chain vs.
off-chain:

1. **Initiator** (e.g. Alice) picks a random secret `preimage`, computes
   `payment_hash = SHA256(preimage)`, and creates a **hold invoice** for that
   hash on her own chain (chain A). She sends `payment_hash` + that invoice
   to the counterparty.
2. **Responder** (e.g. Bob) creates a **mirrored hold invoice**, locked to
   the *same* `payment_hash`, on the other chain (chain B).
3. Each side **pays** the other's invoice. Because these are *hold*
   invoices, the receiving node's `hold` plugin accepts the HTLC but does
   **not** release it — both payments sit "in flight" (`ACCEPTED`) until
   settled.
4. Once both legs are `ACCEPTED` (**both-locked**), the initiator calls
   `settle`, which releases the preimage to her own hold invoice. That
   settles the HTLC on chain A and reveals the preimage to the *payer*
   (Bob), which is visible on the completed outgoing `pay` call.
5. The responder reads that revealed preimage and calls
   `settle-with-preimage` to settle his own hold invoice on chain B with it
   — completing the mirror payment and finishing the swap.
6. If either leg times out or something goes wrong before both sides are
   locked, either party can `refund` (cancel their own hold invoice), and
   no funds move — this is the atomicity guarantee: either both legs settle
   (using the one shared preimage) or neither does.

Swap state transitions through: `initiated` → `counterparty_locked` (one
side accepted) → *(both-locked, implicit once both invoices show
`ACCEPTED`)* → `settled` **or** `refunded`/`failed`.

---

## Prerequisites

- Linux (or macOS) with `bash`.
- [Bitcoin Core](https://bitcoincore.org/) (`bitcoind`/`bitcoin-cli`) on `PATH`.
- [Core Lightning](https://github.com/ElementsProject/lightning) (`lightningd`/`lightning-cli`) on `PATH`.
- Python 3.10+.
- A Rust toolchain + `protoc`, **only** if you need to build the `hold`
  plugin from source (see below). `infra/build_hold_plugin.sh` will install
  a pinned `protoc` release for you if none is found on `PATH`, and will
  print install instructions for `rustup` if `cargo` is missing.

---

## Setup

### 1. Install the Python package

```bash
cd atomic-swap-cli
python3 -m venv .venv
.venv/bin/pip install -e .
```

### 2. Build the `hold` CLN plugin

```bash
./infra/build_hold_plugin.sh        # clones BoltzExchange/hold and builds it (release mode)
```

This clones the plugin into `infra/hold-plugin/` and builds
`infra/hold-plugin/target/release/hold`, which `infra/start_lightningd.sh`
loads automatically via `--plugin=...`.

> If you are **not** running into the sandbox's non-standard
> `option_blake2b` feature-bit requirement described above (i.e. you're on
> a normal Core Lightning build), you can instead build the genuine,
> unmodified upstream plugin — e.g. `git clone
> https://github.com/BoltzExchange/hold` and `cargo build --release`
> directly — and point `start_lightningd.sh` at that binary. The
> `encoder.rs` patch and vendored `lightning-invoice` crate under
> `infra/hold-plugin/` in this repo are **only** needed to satisfy this
> particular sandbox's non-standard requirement; they are not part of, and
> are not required by, the real upstream `hold` plugin.

### 3. Start both simulated chains

```bash
./infra/start_bitcoind.sh     # launches chain-sha256 (RPC 18443) and chain-blake2b (RPC 18453)
./infra/fund_wallets.sh       # mines initial blocks + funds each bitcoind wallet
```

### 4. Start the four Lightning nodes

```bash
./infra/start_lightningd.sh   # launches alice-sha256, bob-sha256, alice-blake2b, bob-blake2b
                               # each with the hold plugin loaded, pointed at its own bitcoind
```

Runtime data (datadirs, logs, pidfiles, the swap DB) lives under
`~/.atomic-swap-cli/` by default (override with `ATOMIC_SWAP_RUNTIME_DIR`).

To stop everything later:

```bash
./infra/stop_lightningd.sh
./infra/stop_bitcoind.sh
```

(Prefer these scripts, or `lightning-cli --lightning-dir=... stop`, over
killing the shell that launched them — the nodes daemonize.)

---

## CLI commands

```bash
python -m atomic_swap_cli.cli --help
```

| Command | Purpose |
|---|---|
| `open-channel` | Connect + fund a Lightning channel between two nodes on the same chain. |
| `initiate` | As the initiator: pick a secret, create the first hold invoice (SHA-256 payment hash) on your chain. |
| `accept` | As the responder: create the mirrored hold invoice, locked to the same payment hash, on the other chain. |
| `pay` | Pay a bolt11 invoice from a given node (used by both sides to lock their HTLC). Blocks until the payment resolves — hold invoices keep it pending until settled/cancelled. |
| `monitor` | Poll both hold invoices for a swap and print their state (`UNPAID`/`ACCEPTED`/etc.), including a `BOTH_LOCKED` summary once ready. |
| `settle` | Initiator only: release the preimage, settling your own hold invoice (this is what reveals the preimage to the other side's completed `pay`). |
| `settle-with-preimage` | Responder only: settle your own hold invoice using a preimage learned from your completed outgoing `pay`. |
| `refund` | Cancel a swap's hold invoice (timeout/refund path) — use if the counterparty never locks their side. |
| `status` | Show full persisted details for one swap by `swap_id`. |
| `list` | List all known swaps and their current state. |

All node names refer to the four preconfigured CLN nodes:
`alice-sha256`, `bob-sha256`, `alice-blake2b`, `bob-blake2b` (see
`atomic_swap_cli/config.py`).

---

## Full demo walkthrough

This walks through one complete swap: Alice gives up 50,000 msat on the
`sha256` chain in exchange for 50,000 msat from Bob on the `blake2b` chain.

### 1. Open channels (once per chain)

```bash
python -m atomic_swap_cli.cli open-channel --from-node alice-sha256   --to-node bob-sha256   --amount-sat 1000000
python -m atomic_swap_cli.cli open-channel --from-node alice-blake2b  --to-node bob-blake2b  --amount-sat 1000000
```

Mine a few blocks on each chain so the channels reach `CHANNELD_NORMAL`
(`bitcoin-cli -regtest -rpcuser=... -rpcpassword=... -rpcport=18443
-generate 6`, and likewise on port 18453 for blake2b). You can check with:

```bash
lightning-cli --lightning-dir=~/.atomic-swap-cli/lightning-alice-sha256 --network=regtest listpeerchannels
```

> **Note on channel direction:** a channel funded entirely by Alice starts
> with all the balance on Alice's side. For a swap to succeed in both
> directions you need spendable balance on *both* sides of *both* channels
> — e.g. also open (or push funds into) a small channel/balance the other
> way (`bob-sha256` → `alice-sha256`, `bob-blake2b` → `alice-blake2b`) if
> you want Bob to be able to pay Alice's invoice too. In a real deployment
> this is normal Lightning channel-management/rebalancing, not something
> specific to swaps.

### 2. Initiate (Alice)

```bash
python -m atomic_swap_cli.cli initiate \
  --initiator-node alice-sha256 --responder-node bob-blake2b \
  --initiator-chain sha256 --responder-chain blake2b \
  --amount-initiator-msat 50000 --amount-responder-msat 50000
```

This prints a `swap_id`, the `payment_hash`, and Alice's bolt11 invoice.
Send the `payment_hash` and invoice to Bob out-of-band (copy/paste for this
local demo).

### 3. Accept (Bob)

```bash
python -m atomic_swap_cli.cli accept \
  --payment-hash <payment_hash from step 2> \
  --responder-node bob-blake2b --initiator-node alice-sha256 \
  --initiator-chain sha256 --responder-chain blake2b \
  --amount-initiator-msat 50000 --amount-responder-msat 50000
```

This prints Bob's own bolt11 invoice, locked to the same `payment_hash`.
Send it back to Alice.

### 4. Pay both legs

Each `pay` blocks until resolved, so run each in the background (or a
separate terminal):

```bash
# Bob pays Alice's invoice on the sha256 chain
python -m atomic_swap_cli.cli pay --from-node bob-sha256 --bolt11 <alice's invoice> &

# Alice pays Bob's invoice on the blake2b chain
python -m atomic_swap_cli.cli pay --from-node alice-blake2b --bolt11 <bob's invoice> &
```

### 5. Monitor until both-locked

```bash
python -m atomic_swap_cli.cli monitor --swap-id <alice's swap_id>
python -m atomic_swap_cli.cli monitor --swap-id <bob's swap_id>
```

Once both invoice states show `ACCEPTED`, you'll see:
`both hold invoices ACCEPTED -> swap is BOTH_LOCKED, ready to settle`.

### 6. Settle (Alice releases the preimage)

```bash
python -m atomic_swap_cli.cli settle --swap-id <alice's swap_id>
```

This settles Alice's hold invoice, which completes Bob's backgrounded
`pay` from step 4 and reveals the preimage in its output
(`preimage revealed: ...`).

### 7. Settle (Bob completes his leg with the revealed preimage)

```bash
python -m atomic_swap_cli.cli settle-with-preimage \
  --swap-id <bob's swap_id> --preimage <preimage revealed in step 6>
```

This settles Bob's hold invoice, which completes Alice's backgrounded `pay`
from step 4 with the same preimage.

### 8. Verify

```bash
python -m atomic_swap_cli.cli list
```

Both swap records should show `settled`.

### Refund / failure path

If the counterparty never accepts/pays their leg (e.g. times out), cancel
your own hold invoice instead of settling:

```bash
python -m atomic_swap_cli.cli refund --swap-id <swap_id> --reason "counterparty timed out"
```

No preimage is ever revealed, so neither leg settles — no funds move.

---

## Running the tests

The full unit test suite mocks all CLN/hold RPC calls and **does not**
require live `bitcoind`/`lightningd` processes:

```bash
.venv/bin/pip install -e ".[test]"   # or just pytest, if already installed
.venv/bin/pytest tests/
```

---

## Project layout

```
atomic_swap_cli/        Python package: CLI, state machine, RPC clients
  cli.py                 click-based CLI commands
  swap_service.py         swap state machine / orchestration logic
  store.py                 SQLite-backed persistence for swap records
  models.py                 SwapRecord / SwapState dataclasses
  cln_rpc.py                 thin JSON-RPC client for lightningd (unix socket)
  hold_client.py               client for the hold plugin's RPC commands
  bitcoin_rpc.py                minimal bitcoind JSON-RPC client (used by infra helpers)
  config.py                       static config for the 2 chains + 4 CLN nodes
infra/                   Bash scripts to launch/stop the simulated environment
  start_bitcoind.sh / stop_bitcoind.sh
  fund_wallets.sh
  start_lightningd.sh / stop_lightningd.sh
  build_hold_plugin.sh     clones + builds BoltzExchange/hold from source
  hold-plugin/             (generated) cloned+patched hold plugin source/build output
tests/                   pytest suite (fully mocked, no live daemons required)
```

---

## Known limitations

- This is a **two-node-per-chain** demo topology (Alice/Bob only); it's not
  meant to demonstrate multi-hop routing, fee estimation, or liquidity
  management at scale.
- The local-sandbox feature-bit workaround described above
  (`encoder.rs` patch + vendored `lightning-invoice` crate) is specific to
  this development environment's non-standard `lightningd` build and is
  clearly commented as such in both files; remove/ignore it on a stock CLN
  install.
- No real refund-timeout automation is included (`refund` is a manual CLI
  action) — a production-grade implementation would want automatic
  CLTV-expiry-driven refunds.
