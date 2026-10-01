# atomic-swap-cli

A Python CLI for cross-chain **atomic swaps over Lightning Network**
("submarine swaps") between:

- **Bitcoin (sha256)** — mainline Bitcoin, unmodified SHA256d proof-of-work:
  standard [Bitcoin Core](https://bitcoincore.org/) (`bitcoind`) + standard
  [Core Lightning](https://github.com/ElementsProject/lightning) (`lightningd`).
- **Bitcoin (blake2b)** — the **Bitcoin Knots BLAKE2b fork**, sometimes
  called **BTCB2**/**XBT**, which hard-forked away from SHA256d
  proof-of-work at block **961,640** (mainnet) / **150,308** (testnet4):
  the [bitcoinknots/bitcoin](https://github.com/bitcoinknots/bitcoin) fork
  (`bitcoind`) + the [privkeyio/lightning](https://github.com/privkeyio/lightning)
  `blake2b-unified` CLN fork (`lightningd`).

using real Core Lightning nodes on both sides and the real
[BoltzExchange/hold](https://github.com/BoltzExchange/hold) plugin for true
hold-invoice atomicity (the HTLC is accepted but **not** auto-settled until
the preimage is explicitly released through the plugin's gRPC/JSON-RPC API).

---

## ⚠️ REAL-FUNDS RISK DISCLAIMER — READ THIS FIRST ⚠️

**This project can move real money on Bitcoin mainnet.** It is personal,
unaudited software, written quickly, with no security review. Before using
it with funds you cannot afford to lose, understand:

1. **This code itself is unaudited.** It has not been reviewed by anyone
   other than its author. Bugs in the CLI, the state machine, or how it
   drives `lightningd`/the `hold` plugin could cause you to lose funds.
2. **The Bitcoin Knots BLAKE2b fork (BTCB2) is a new, contentious,
   minority chain.** It resulted from a hard fork at block 961,640
   (mainnet) that most of the Bitcoin ecosystem did not adopt. Its
   security model, hashrate, replay-protection correctness, and
   long-term viability are all materially different from — and less
   proven than — mainline Bitcoin.
3. **The `privkeyio/lightning` CLN fork is explicitly experimental,
   unaudited software per its own README.** It implements novel BLAKE2b
   header parsing and an opt-in `SIGHASH_UNIFIED` signature scheme that
   has not had the years of adversarial review that Bitcoin Core/CLN
   have had. Treat any funds routed through it as at meaningfully higher
   risk than funds on mainline Bitcoin + standard CLN.
4. **Pre-activation coins must never fund a blake2b-chain channel.**
   Coins received on the blake2b chain *before* its activation height
   share history with the pre-split chain; funding a channel with them
   reopens the exact cross-chain replay/signature exposure the
   unified-sig scheme exists to prevent. This CLI includes an automated
   best-effort check for this (`atomic_swap_cli/safety.py`) but **it is
   a safety net, not a substitute for reading the upstream
   `privkeyio/lightning` README yourself.**
5. **Strongly recommended: use `testnet4` first.** Both forks support a
   real, public `testnet4` network with worthless test coins (activation
   height 150,308). Validate your whole setup there — channel opens,
   swaps, refunds, the fund-safety check — before ever pointing this CLI
   at `mainnet`.
6. **Mainnet commands refuse to run without `--yes-mainnet`.** This is a
   deliberate speed bump, not a safety guarantee. Passing that flag means
   *you* take responsibility for understanding the risks above.

If any of this is unclear, **do not use this software on mainnet.**
`regtest` (fully local, zero real-world network, zero real-world funds) and
`testnet4` (real public network, worthless coins) remain available and
recommended for learning/validating the tool.

---

## What's simulated vs. what's real, by network profile

This project supports **three network profiles**, selected via node name
suffix (`atomic_swap_cli/config.py`):

| Profile | Node name examples | What it is |
|---|---|---|
| `regtest` (optional, local dev mode) | `alice-sha256`, `bob-blake2b` (no suffix) | **Fully simulated and local.** Two ordinary, independent `bitcoind` **regtest** instances with separate datadirs/ports/credentials; blocks are mined on demand, no real network, no real money. The `sha256`/`blake2b` labels here are **cosmetic only** — regtest has no real chain split. This mode exists purely to exercise this project's own CLI/state-machine logic quickly, without needing either real node fork installed. **Not** a stand-in for the real blake2b chain's consensus rules. |
| `testnet4` (recommended first real-network step) | `alice-sha256-testnet4`, `bob-blake2b-testnet4` | **Real, public networks with worthless test coins.** sha256 side is standard Bitcoin Core testnet4 + standard CLN; blake2b side is the real Bitcoin Knots BLAKE2b fork + `privkeyio/lightning` fork, both running against testnet4 (activation height 150,308). You must run/sync these nodes yourself — this project only connects to them. |
| `mainnet` (real funds — see disclaimer above) | `alice-sha256-mainnet`, `bob-blake2b-mainnet` | **Real Bitcoin mainnet and the real BTCB2/blake2b mainnet fork, real money.** sha256 side is standard Bitcoin Core mainnet + standard CLN (audited, mature software). blake2b side is the Bitcoin Knots BLAKE2b fork + `privkeyio/lightning` fork on mainnet — **experimental, unaudited** per their own disclaimers. You must run/sync these nodes yourself. |

Across all three profiles:

| Component | Status |
|---|---|
| Lightning nodes | **Real** `lightningd` (Core Lightning, or the `privkeyio/lightning` fork on the blake2b side for testnet4/mainnet) processes forming real Lightning channels. |
| Hold invoices / atomicity | **Real**, via the real [BoltzExchange/hold](https://github.com/BoltzExchange/hold) CLN plugin — confirmed compatible with both standard CLN and the `privkeyio/lightning` fork, since both expose the same plugin RPC/gRPC surface the fork does not modify. |
| Swap state machine / CLI | **Real**, this repo's Python code (`atomic_swap_cli/`), persisted to a local SQLite DB so it's resumable across process restarts, chain-agnostic (it only ever sees opaque chain/node name strings). |

**Local sandbox note (regtest dev-mode only):** the sandbox this project was
originally developed in ships a non-standard `lightningd` build that
unconditionally requires BOLT9 feature bit 512 (a non-upstream
`option_blake2b` feature) on every invoice it pays. Interestingly, this
turned out to foreshadow the real `privkeyio/lightning` fork, which
genuinely requires peer feature bits `option_blake2b` (512) and
`option_unified_sigs` (514) for real post-activation operation. The
regtest-only patches that work around the sandbox quirk
(`infra/hold-plugin/src/encoder.rs`, a vendored `lightning-invoice` crate)
are **not** needed when running against a stock Core Lightning build or the
real `privkeyio/lightning` fork binaries — they're regtest-sandbox-specific
and clearly commented as such in-place.

---

## Architecture

```
    "Bitcoin (sha256)"                      "Bitcoin (blake2b)" / BTCB2
    standard bitcoind                       Bitcoin Knots BLAKE2b fork bitcoind
    (regtest / testnet4 / mainnet)          (regtest / testnet4 / mainnet)
             ^            ^                            ^            ^
             |            |                            |            |
   lightningd(alice)  lightningd(bob)       lightningd(alice)   lightningd(bob)
   standard CLN        standard CLN         privkeyio/lightning  privkeyio/lightning
   + hold plugin        + hold plugin       blake2b-unified fork  blake2b-unified fork
                                              + hold plugin        + hold plugin
             \____________/                            \____________/
              LN channel                                LN channel

                    atomic_swap_cli (Python CLI)
        talks to lightningd via JSON-RPC (unix sockets)
        talks to hold plugin via its JSON-RPC commands
        talks to bitcoind via JSON-RPC (cookie- or user/pass-auth)
        persists swap state to ~/.atomic-swap-cli/state/swaps.sqlite3
        enforces fund-safety + real-money guardrails before broadcasting
```

A swap works like a standard submarine-swap / HTLC handshake, just across
two independent Lightning networks instead of on-chain vs. off-chain:

1. **Initiator** (e.g. Alice) picks a random secret `preimage`, computes
   `payment_hash = SHA256(preimage)`, and creates a **hold invoice** for
   that hash on her own chain (chain A). She sends `payment_hash` + that
   invoice to the counterparty.
2. **Responder** (e.g. Bob) creates a **mirrored hold invoice**, locked to
   the *same* `payment_hash`, on the other chain (chain B).
3. Each side **pays** the other's invoice. Because these are *hold*
   invoices, the receiving node's `hold` plugin accepts the HTLC but does
   **not** release it — both payments sit "in flight" (`ACCEPTED`) until
   settled.
4. Once both legs are `ACCEPTED` (**both-locked**), the initiator calls
   `settle`, which releases the preimage to her own hold invoice. That
   settles the HTLC on chain A and reveals the preimage to the *payer*
   (Bob), visible on the completed outgoing `pay` call.
5. The responder reads that revealed preimage and calls
   `settle-with-preimage` to settle his own hold invoice on chain B with
   it — completing the mirror payment and finishing the swap.
6. If either leg times out or something goes wrong before both sides are
   locked, either party can `refund` (cancel their own hold invoice), and
   no funds move — this is the atomicity guarantee: either both legs
   settle (using the one shared preimage) or neither does.

Swap state transitions through: `initiated` → `counterparty_locked` (one
side accepted) → *(both-locked, implicit once both invoices show
`ACCEPTED`)* → `settled` **or** `refunded`/`failed`.

---

## Prerequisites

- Linux (or macOS) with `bash`.
- Python 3.10+.
- **For `regtest` dev-mode (optional, recommended for first-time setup):**
  stock [Bitcoin Core](https://bitcoincore.org/) + stock
  [Core Lightning](https://github.com/ElementsProject/lightning) on `PATH`,
  plus a Rust toolchain + `protoc` to build the `hold` plugin (see below).
- **For `testnet4`/`mainnet` on the sha256 side:** your own synced,
  running standard `bitcoind` + standard `lightningd` (with the `hold`
  plugin loaded).
- **For `testnet4`/`mainnet` on the blake2b side:** your own synced,
  running Bitcoin Knots BLAKE2b fork `bitcoind` + the `privkeyio/lightning`
  `blake2b-unified` fork `lightningd` (with the same `hold` plugin loaded).
  See [Building/installing the BLAKE2b-fork node software](#buildinginstalling-the-blake2b-fork-node-software)
  below.

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
`infra/hold-plugin/target/release/hold`. The same plugin binary works
against **both** standard CLN and the `privkeyio/lightning` fork — the fork
only changes block-header parsing and adds an opt-in signature scheme, it
does not change the plugin RPC/gRPC surface `hold` relies on.

> The `encoder.rs` patch and vendored `lightning-invoice` crate under
> `infra/hold-plugin/` are **regtest-sandbox-specific workarounds** (see
> the note above) — not required against a stock CLN build or the real
> `privkeyio/lightning` fork. If you hit build issues unrelated to that
> quirk, build the unmodified upstream plugin directly instead:
> `git clone https://github.com/BoltzExchange/hold && cargo build --release`.

### 3a. Regtest dev-mode (optional, fully local, no real funds)

```bash
./infra/start_bitcoind.sh     # launches chain-sha256 (RPC 18443) and chain-blake2b (RPC 18453)
./infra/fund_wallets.sh       # mines initial blocks + funds each bitcoind wallet
./infra/start_lightningd.sh   # launches alice-sha256, bob-sha256, alice-blake2b, bob-blake2b
                               # each with the hold plugin loaded, pointed at its own bitcoind
```

Runtime data (datadirs, logs, pidfiles, the swap DB) lives under
`~/.atomic-swap-cli/` by default (override with `ATOMIC_SWAP_RUNTIME_DIR`).
To stop everything: `./infra/stop_lightningd.sh && ./infra/stop_bitcoind.sh`
(prefer these over killing the shell — the nodes daemonize).

### 3b. Testnet4 / mainnet: bring your own nodes

This project does **not** launch or manage real testnet4/mainnet nodes —
you run your own, and point this CLI's environment variables at them.

**sha256 side** (standard Bitcoin Core + standard CLN): install normally
per their own docs. By default this CLI assumes the standard datadir
(`~/.bitcoin`) and cookie-file auth; override via
`ATOMIC_SWAP_{TESTNET4,MAINNET}_SHA256_{DATADIR,RPC_HOST,RPC_PORT,RPC_USER,RPC_PASSWORD,COOKIE,BITCOIND_BINARY}`.

**blake2b side:** see the next section.

### Building/installing the BLAKE2b-fork node software

**Bitcoin Knots BLAKE2b fork `bitcoind`** —
[bitcoinknots/bitcoin](https://github.com/bitcoinknots/bitcoin), tag
`v29.4.2.knots20260508`. No prebuilt binary was available for this tag at
the time this project was written, so build from source:

```bash
./infra/install_bitcoinknots.sh [install-path]   # defaults to ~/.atomic-swap-cli/bin/bitcoind-knots-blake2b
```

Check the [releases page](https://github.com/bitcoinknots/bitcoin/releases)
yourself first — if an official binary release now exists for this tag,
prefer that (with its own signature verification) over building from
source.

**`privkeyio/lightning` `blake2b-unified` fork `lightningd`** —
[privkeyio/lightning](https://github.com/privkeyio/lightning), branch
`blake2b-unified`, release `v26.06.8-blake2b.5`. This release ships
**reproducible prebuilt binaries** for Ubuntu 22.04/24.04/26.04 with a
signed `SHA256SUMS`, which is strongly preferred over building from
source. The following script downloads the right asset for your Ubuntu
release, **verifies its GPG signature** (signing key
`A47D99B6DB0D715D40C59A2023AE8A8EA7E24E38`) and checksum, then installs it:

```bash
./infra/install_lightningd_blake2b_fork.sh
```

**Read the script before running it.** It fetches the signing key from
`keys.openpgp.org` if not already in your keyring and runs `gpg --verify`
against `SHA256SUMS.asc` before trusting the downloaded binary — do not
skip or bypass that check. If you'd rather build from source (branch
`blake2b-unified`), clone the repo and follow Core Lightning's own
`doc/BUILDING.md`.

Once installed, point the project's env vars at the binaries, e.g.:

```bash
export ATOMIC_SWAP_MAINNET_BLAKE2B_BITCOIND_BINARY=~/.atomic-swap-cli/bin/bitcoind-knots-blake2b
export ATOMIC_SWAP_MAINNET_BLAKE2B_LIGHTNINGD_BINARY=~/.atomic-swap-cli/bin/lightningd-blake2b
export ATOMIC_SWAP_TESTNET4_BLAKE2B_BITCOIND_BINARY=~/.atomic-swap-cli/bin/bitcoind-knots-blake2b
export ATOMIC_SWAP_TESTNET4_BLAKE2B_LIGHTNINGD_BINARY=~/.atomic-swap-cli/bin/lightningd-blake2b
```

Launching/configuring these real nodes (datadir layout, `bitcoin.conf`,
`lightningd.conf`, `--plugin=.../hold` flag, peer connections) is up to
you — see each project's own docs. `atomic_swap_cli/config.py` documents
every env var this project reads to find them.

---

## The fund-safety rule (blake2b chain, testnet4/mainnet only)

Per the `privkeyio/lightning` README: **only fund a blake2b-chain channel
using coins received at or after that chain's activation height**
(961,640 mainnet / 150,308 testnet4). Pre-activation coins share history
with the pre-split chain, and funding with them reopens the exact
cross-chain replay/signature exposure the opt-in `SIGHASH_UNIFIED` scheme
exists to prevent.

`atomic_swap_cli/safety.py` implements an automated best-effort check: the
`open-channel` command inspects the funding node's `listfunds` outputs on
the blake2b chain and, for each confirmed one, compares its confirmation
height against the activation height via `gettxout`/`getblockcount`. If any
candidate output predates activation, the command refuses to proceed:

```
REFUSING to fund: wallet holds pre-activation coin(s) on the blake2b chain:
  - <txid>:<vout> -- UTXO confirmed at height ..., activation height is ... -> UNSAFE (pre-activation coin)
```

This is **a safety net, not a guarantee** — it doesn't trace coins through
multiple hops, and CLN's own coin selection could combine inputs in ways a
single-UTXO check can't fully model. If you've manually verified your
funding coins are safe some other way, you can override it with
`--accept-fund-safety-risk` (regtest and the sha256 chain are never
affected by this check — it only applies to real blake2b-chain networks).
**Read the upstream `privkeyio/lightning` README yourself before funding
real channels.**

---

## Real-money guardrails

Any command touching a `mainnet`-profile node (`is_real_money` in
`config.py`) refuses to run unless you pass `--yes-mainnet`, and prints a
`⚠️ MAINNET (REAL FUNDS)` banner when it does. `testnet4`-profile commands
print an informational `(testnet4, worthless coins)` note but don't
require the flag. In addition:

- `open-channel` on a real network (`testnet4`/`mainnet`) prints an
  **estimated feerate** (via `estimatesmartfee`) before broadcasting, runs
  the blake2b fund-safety check described above, and supports `--dry-run`
  to preview without broadcasting anything.
- `pay` supports `--dry-run` to decode and display an invoice's amount and
  payment hash without actually paying it.

---

## CLI commands

```bash
python -m atomic_swap_cli.cli --help
```

| Command | Purpose |
|---|---|
| `open-channel` | Connect + fund a Lightning channel between two nodes on the same chain. Flags: `--yes-mainnet`, `--dry-run`, `--accept-fund-safety-risk`. |
| `initiate` | As the initiator: pick a secret, create the first hold invoice (SHA-256 payment hash) on your chain. Flag: `--yes-mainnet`. |
| `accept` | As the responder: create the mirrored hold invoice, locked to the same payment hash, on the other chain. Flag: `--yes-mainnet`. |
| `pay` | Pay a bolt11 invoice from a given node (used by both sides to lock their HTLC). Blocks until the payment resolves. Flags: `--yes-mainnet`, `--dry-run`. |
| `monitor` | Poll both hold invoices for a swap and print their state (`UNPAID`/`ACCEPTED`/etc.), including a `BOTH_LOCKED` summary once ready. |
| `settle` | Initiator only: release the preimage, settling your own hold invoice (this reveals the preimage to the other side's completed `pay`). Flag: `--yes-mainnet`. |
| `settle-with-preimage` | Responder only: settle your own hold invoice using a preimage learned from your completed outgoing `pay`. Flag: `--yes-mainnet`. |
| `refund` | Cancel a swap's hold invoice (timeout/refund path) — use if the counterparty never locks their side. Flag: `--yes-mainnet`. |
| `check-timeout` | Check a swap's recorded CLTV-expiry height against each chain's current height and, if passed, auto-refund (cancel your own hold invoice). Manually-triggered — see [Known limitations](#known-limitations). Flag: `--yes-mainnet`. |
| `status` | Show full persisted details for one swap by `swap_id`. |
| `list` | List all known swaps and their current state. |

Node names encode the network profile by suffix: no suffix = `regtest`
(e.g. `alice-sha256`), `-testnet4` (e.g. `bob-blake2b-testnet4`), or
`-mainnet` (e.g. `alice-sha256-mainnet`) — see `atomic_swap_cli/config.py`
for the full registry.

---

## Full demo walkthrough (regtest dev-mode)

This walks through one complete swap in the fully local, no-real-funds
regtest dev-mode — the recommended way to first learn this tool before
ever touching testnet4 or mainnet. Alice gives up 50,000 msat on the
`sha256` chain in exchange for 50,000 msat from Bob on the `blake2b`
chain. (The same commands work against `testnet4`/`mainnet` node names,
just add `--yes-mainnet` for mainnet ones and budget real confirmation
times.)

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

## Moving to testnet4, then mainnet

1. Install and sync both node pairs per
   [Building/installing the BLAKE2b-fork node software](#buildinginstalling-the-blake2b-fork-node-software)
   above, for `testnet4` first.
2. Repeat the walkthrough above using `-testnet4`-suffixed node names
   (`alice-sha256-testnet4`, `bob-blake2b-testnet4`, etc.) and real
   confirmation wait times instead of `-generate`.
3. **Specifically verify the fund-safety check** works as expected on
   testnet4 before trusting it on mainnet: try funding from a coin you
   know predates block 150,308 on the blake2b testnet4 chain and confirm
   `open-channel` refuses it.
4. Only once you're fully comfortable, repeat with `-mainnet`-suffixed
   node names, `--yes-mainnet` on every real-money command, and real
   funds you are prepared to lose. Re-read the
   [risk disclaimer](#️-real-funds-risk-disclaimer--read-this-first-️)
   above first.

---

## Running the tests

The full unit test suite mocks all CLN/hold/bitcoind RPC calls and
**does not** require live `bitcoind`/`lightningd` processes or any network
access (no live testnet4/mainnet calls are ever made in tests):

```bash
.venv/bin/pip install -e ".[test]"   # or just pytest, if already installed
.venv/bin/pytest tests/
```

---

## Project layout

```
atomic_swap_cli/        Python package: CLI, state machine, RPC clients
  cli.py                 click-based CLI commands + real-money guardrails
  swap_service.py         swap state machine / orchestration logic
  safety.py                 blake2b-chain post-activation fund-safety check
  store.py                   SQLite-backed persistence for swap records
  models.py                   SwapRecord / SwapState dataclasses
  cln_rpc.py                    thin JSON-RPC client for lightningd (unix socket)
  hold_client.py                  client for the hold plugin's RPC commands
  bitcoin_rpc.py                    bitcoind JSON-RPC client (cookie- or user/pass-auth)
  config.py                           network profiles (regtest/testnet4/mainnet)
                                       x chain kinds (sha256/blake2b) node registry
infra/                   Bash scripts to launch/stop the regtest dev-mode,
                          and to build/install the blake2b-fork node software
  start_bitcoind.sh / stop_bitcoind.sh     regtest dev-mode only
  fund_wallets.sh                           regtest dev-mode only
  start_lightningd.sh / stop_lightningd.sh  regtest dev-mode only
  build_hold_plugin.sh     clones + builds BoltzExchange/hold from source
  install_bitcoinknots.sh    builds the Bitcoin Knots BLAKE2b fork bitcoind
  install_lightningd_blake2b_fork.sh   downloads+GPG-verifies the privkeyio/lightning
                                       fork's prebuilt lightningd binary
  hold-plugin/             (generated) cloned+patched hold plugin source/build output
tests/                   pytest suite (fully mocked, no live daemons/network required)
```

---

## Known limitations

- This is a **two-node-per-chain** demo topology (Alice/Bob only); it's not
  meant to demonstrate multi-hop routing, fee estimation, or liquidity
  management at scale.
- The regtest-sandbox feature-bit workaround described above
  (`encoder.rs` patch + vendored `lightning-invoice` crate) is specific to
  the development environment's non-standard `lightningd` build and is
  clearly commented as such in both files; it's irrelevant against a
  stock CLN build or the real `privkeyio/lightning` fork binaries.
- CLTV-expiry tracking exists (`initiate`/`accept` record an absolute
  expiry block height per leg, computed from the chain height at
  creation time plus the CLTV delta) and `check-timeout --swap-id <id>`
  will auto-refund (cancel your own hold invoice) once that height has
  passed. This is still a **manually-triggered check**, not a background
  daemon — you (or a cron job calling `check-timeout` periodically) must
  invoke it; nothing runs automatically in the background on its own.
  Swaps created without a reachable bitcoind at `initiate`/`accept` time
  have no recorded expiry height and are reported as "unknown" rather
  than guessed at.
- The fund-safety check in `safety.py` is best-effort (see
  [above](#the-fund-safety-rule-blake2b-chain-testnet4mainnet-only)) — it
  does not replace reading the upstream `privkeyio/lightning` README.
- This project does not launch, configure, or manage real testnet4/mainnet
  nodes for you (unlike the regtest dev-mode scripts) — you are
  responsible for running, syncing, securing, and backing up your own
  node software and wallets.
- `bitcoinknots/bitcoin` tag `v29.4.2.knots20260508` had no official
  prebuilt binary release at the time this project was written, so
  `infra/install_bitcoinknots.sh` builds from source; re-check the
  project's releases page for an official signed binary before relying
  on this script long-term.
