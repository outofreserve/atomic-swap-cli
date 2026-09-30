"""Static configuration for the two simulated regtest networks and the
four Core Lightning nodes used in demos.

IMPORTANT — simulation disclaimer
----------------------------------
"Bitcoin (blake2b)" and "Bitcoin (sha256)" are **not** real alternate
proof-of-work forks of Bitcoin. They are two independent, ordinary
``bitcoind`` regtest instances running on this machine with separate
datadirs, ports and RPC credentials. In regtest mode there is no real
proof-of-work difficulty to speak of (blocks are generated on demand via
``generatetoaddress``), so the hash-function label is purely cosmetic —
it exists only so the CLI output and docs clearly distinguish "chain A"
from "chain B" for a human reading the demo. Swap logic never depends on
which hash function either chain's PoW nominally uses.

Everything below is local, regtest-only configuration. No mainnet or
real-money code paths exist in this project.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

# Root directory where all generated runtime data (datadirs, lightning
# dirs, logs, pidfiles) is written. Kept out of the repo via .gitignore.
RUNTIME_DIR = Path(
    __import__("os").environ.get("ATOMIC_SWAP_RUNTIME_DIR", str(Path.home() / ".atomic-swap-cli"))
)

# Local state (swap DB, CLI config) lives alongside runtime data by default.
STATE_DIR = RUNTIME_DIR / "state"
DB_PATH = STATE_DIR / "swaps.sqlite3"


@dataclass(frozen=True)
class BitcoinNodeConfig:
    """Connection details for one simulated bitcoind regtest instance."""

    label: str  # e.g. "chain-sha256" / "chain-blake2b" (display only)
    datadir: Path
    rpc_host: str = "127.0.0.1"
    rpc_port: int = 18443
    p2p_port: int = 18444
    rpc_user: str = "swapuser"
    rpc_password: str = "swappass"

    @property
    def rpc_url(self) -> str:
        return f"http://{self.rpc_user}:{self.rpc_password}@{self.rpc_host}:{self.rpc_port}"


@dataclass(frozen=True)
class LightningNodeConfig:
    """Connection details for one lightningd + hold-plugin instance."""

    label: str  # e.g. "alice-chain-sha256"
    lightning_dir: Path
    network: str  # matches the underlying bitcoind network name passed via -regtest
    rpc_port: int  # CLN's own `lightning-rpc` is a unix socket; kept for docs
    hold_grpc_host: str = "127.0.0.1"
    hold_grpc_port: int = 0  # filled in per-node below
    bitcoin: BitcoinNodeConfig = None  # type: ignore[assignment]


# --- Two simulated chains -------------------------------------------------

CHAIN_SHA256 = BitcoinNodeConfig(
    label="Bitcoin (sha256) [simulated regtest]",
    datadir=RUNTIME_DIR / "bitcoin-sha256",
    rpc_port=18443,
    p2p_port=18444,
    rpc_user="swapuser_sha256",
    rpc_password="swappass_sha256",
)

CHAIN_BLAKE2B = BitcoinNodeConfig(
    label="Bitcoin (blake2b) [simulated regtest]",
    datadir=RUNTIME_DIR / "bitcoin-blake2b",
    rpc_port=18453,
    p2p_port=18454,
    rpc_user="swapuser_blake2b",
    rpc_password="swappass_blake2b",
)

CHAINS = {"sha256": CHAIN_SHA256, "blake2b": CHAIN_BLAKE2B}

# --- Four CLN nodes: {alice,bob} x {sha256,blake2b} -----------------------

LIGHTNING_NODES: dict[str, LightningNodeConfig] = {
    "alice-sha256": LightningNodeConfig(
        label="alice-sha256",
        lightning_dir=RUNTIME_DIR / "lightning-alice-sha256",
        network="regtest",
        rpc_port=19735,
        hold_grpc_port=20001,
        bitcoin=CHAIN_SHA256,
    ),
    "bob-sha256": LightningNodeConfig(
        label="bob-sha256",
        lightning_dir=RUNTIME_DIR / "lightning-bob-sha256",
        network="regtest",
        rpc_port=19736,
        hold_grpc_port=20002,
        bitcoin=CHAIN_SHA256,
    ),
    "alice-blake2b": LightningNodeConfig(
        label="alice-blake2b",
        lightning_dir=RUNTIME_DIR / "lightning-alice-blake2b",
        network="regtest",
        rpc_port=19745,
        hold_grpc_port=20003,
        bitcoin=CHAIN_BLAKE2B,
    ),
    "bob-blake2b": LightningNodeConfig(
        label="bob-blake2b",
        lightning_dir=RUNTIME_DIR / "lightning-bob-blake2b",
        network="regtest",
        rpc_port=19746,
        hold_grpc_port=20004,
        bitcoin=CHAIN_BLAKE2B,
    ),
}


def node(name: str) -> LightningNodeConfig:
    try:
        return LIGHTNING_NODES[name]
    except KeyError as exc:
        raise ValueError(
            f"Unknown lightning node {name!r}; expected one of {sorted(LIGHTNING_NODES)}"
        ) from exc
