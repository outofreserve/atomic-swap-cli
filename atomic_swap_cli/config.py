"""Network/node configuration.

=========================== REAL-FUNDS WARNING =============================
This project now supports REAL networks, including Bitcoin **mainnet**
(real money) alongside a real, contentious chain split:

  * "Bitcoin (sha256)" = mainline Bitcoin, unmodified SHA256d
    proof-of-work, standard Bitcoin Core (``bitcoind``) + standard
    Core Lightning (``lightningd`` from ElementsProject/lightning).

  * "Bitcoin (blake2b)" = the Bitcoin Knots BLAKE2b fork (sometimes
    called BTCB2/XBT) that hard-forked away from SHA256d proof-of-work
    at block 961,640 (mainnet) / 150,308 (testnet4). It requires:
      - a Bitcoin Core fork: https://github.com/bitcoinknots/bitcoin
        (tag v29.4.2.knots20260508)
      - a Core Lightning fork: https://github.com/privkeyio/lightning
        (branch blake2b-unified, release v26.06.8-blake2b.5) which
        understands BLAKE2b block headers and the opt-in
        SIGHASH_UNIFIED signature scheme, and advertises/requires
        peer feature bits option_blake2b (512) / option_unified_sigs
        (514).

Both the Bitcoin Knots BLAKE2b fork and the privkeyio/lightning fork
are **experimental, unaudited software** per their own README
disclaimers. Using this CLI against mainnet means real funds are at
risk from bugs in this project, in those forks, or in your
understanding of the fund-safety rules below. See README.md for the
full risk disclosure. ``regtest`` profiles remain available and are
recommended for exercising the CLI/state-machine logic itself without
touching any real network.

=========================== FUND-SAFETY RULE ===============================
Per the privkeyio/lightning README: only fund a blake2b-chain channel
(or a swap leg on that chain) using coins **received at or after** the
chain-split activation height (961,640 mainnet / 150,308 testnet4).
Channels funded from pre-activation coins reopen exactly the
cross-chain replay/signature-malleability exposure that the unified-sig
scheme exists to prevent. ``atomic_swap_cli.safety`` implements an
automated check for this that the CLI runs before funding a channel on
a blake2b-chain network profile; see that module and the README for
details. This check cannot protect you from every mistake -- read the
upstream privkeyio/lightning README yourself before using mainnet.
==============================================================================
"""
from __future__ import annotations

import enum
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

# Root directory where all generated runtime data (datadirs, lightning
# dirs, logs, pidfiles) is written. Kept out of the repo via .gitignore.
RUNTIME_DIR = Path(os.environ.get("ATOMIC_SWAP_RUNTIME_DIR", str(Path.home() / ".atomic-swap-cli")))

# Local state (swap DB, CLI config) lives alongside runtime data by default.
STATE_DIR = RUNTIME_DIR / "state"
DB_PATH = STATE_DIR / "swaps.sqlite3"


class NetworkProfile(str, enum.Enum):
    """Which real/simulated network a node pair is deployed on."""

    REGTEST = "regtest"  # local-only simulation; recommended for CLI/dev work
    TESTNET4 = "testnet4"  # real public testnet4 network, worthless test coins
    MAINNET = "mainnet"  # real Bitcoin mainnet / real BTCB2 mainnet -- REAL FUNDS


class ChainKind(str, enum.Enum):
    SHA256 = "sha256"  # mainline Bitcoin, unmodified SHA256d PoW
    BLAKE2B = "blake2b"  # Bitcoin Knots BLAKE2b fork (BTCB2), privkeyio CLN fork


# Chain-split activation heights for the BLAKE2b fork. Irrelevant for the
# SHA256 (mainline) chain and for REGTEST (no real split exists there).
BLAKE2B_ACTIVATION_HEIGHT: dict[NetworkProfile, int] = {
    NetworkProfile.MAINNET: 961_640,
    NetworkProfile.TESTNET4: 150_308,
}

# Networks where funds have real-world value and the strongest guardrails
# (explicit confirmation flags, fee display, fund-safety checks) apply.
REAL_MONEY_PROFILES = {NetworkProfile.MAINNET}
# Networks that talk to a real, public peer-to-peer network (so e.g.
# "wait for confirmations" is a real multi-minute wait, unlike regtest)
# even though the profile's coins are worthless.
REAL_NETWORK_PROFILES = {NetworkProfile.MAINNET, NetworkProfile.TESTNET4}


def _env(key: str, default: Optional[str] = None) -> Optional[str]:
    return os.environ.get(key, default)


@dataclass(frozen=True)
class BitcoinNodeConfig:
    """Connection details for one bitcoind-family node (standard Bitcoin
    Core for the sha256 chain, or the Bitcoin Knots BLAKE2b fork build
    for the blake2b chain)."""

    label: str
    network_profile: NetworkProfile
    chain_kind: ChainKind
    datadir: Path
    rpc_host: str = "127.0.0.1"
    rpc_port: int = 18443
    p2p_port: int = 18444
    rpc_user: Optional[str] = None
    rpc_password: Optional[str] = None
    # Standard bitcoind cookie-auth file, used instead of user/pass when
    # set (the normal way to authenticate against your own real node).
    rpc_cookie_file: Optional[Path] = None
    # Path/name of the bitcoind binary to launch for this node (only used
    # by the infra/*.sh dev launcher scripts; for mainnet/testnet4 you are
    # expected to already be running your own node and only the RPC
    # connection details above are used).
    binary_path: str = "bitcoind"

    @property
    def is_real_money(self) -> bool:
        return self.network_profile in REAL_MONEY_PROFILES

    @property
    def activation_height(self) -> Optional[int]:
        """Height at which this chain's BLAKE2b fork activated, or None
        if this node is on the sha256 chain or on regtest (no real split)."""
        if self.chain_kind is ChainKind.BLAKE2B:
            return BLAKE2B_ACTIVATION_HEIGHT.get(self.network_profile)
        return None

    @property
    def rpc_url(self) -> str:
        if self.rpc_user and self.rpc_password:
            return f"http://{self.rpc_user}:{self.rpc_password}@{self.rpc_host}:{self.rpc_port}"
        return f"http://{self.rpc_host}:{self.rpc_port}"


@dataclass(frozen=True)
class LightningNodeConfig:
    """Connection details for one lightningd + hold-plugin instance."""

    label: str
    network_profile: NetworkProfile
    lightning_dir: Path
    network: str  # lightningd's own --network value: "regtest" | "testnet4" | "bitcoin"
    rpc_port: int
    hold_grpc_host: str = "127.0.0.1"
    hold_grpc_port: int = 0
    bitcoin: BitcoinNodeConfig = None  # type: ignore[assignment]
    # Path/name of the lightningd binary for this node. Standard CLN for
    # the sha256 chain; must be the privkeyio/lightning blake2b-unified
    # fork for the blake2b chain on testnet4/mainnet (see README for the
    # prebuilt-binary + gpg-verification instructions). Overridable via
    # ATOMIC_SWAP_<PROFILE>_BLAKE2B_LIGHTNINGD_BINARY.
    binary_path: str = "lightningd"

    @property
    def is_real_money(self) -> bool:
        return self.bitcoin.is_real_money if self.bitcoin else False


def _lightning_network_name(profile: NetworkProfile) -> str:
    return {
        NetworkProfile.REGTEST: "regtest",
        NetworkProfile.TESTNET4: "testnet4",
        NetworkProfile.MAINNET: "bitcoin",
    }[profile]


# --- REGTEST profile: local simulation, recommended for CLI/state-machine --
# These two chains are ordinary bitcoind regtest instances with separate
# datadirs/ports/credentials; the sha256/blake2b labels are purely
# cosmetic here (no real chain split exists on regtest). Good for
# iterating on this project's own code without touching any real network.

CHAIN_SHA256_REGTEST = BitcoinNodeConfig(
    label="Bitcoin (sha256) [regtest simulation]",
    network_profile=NetworkProfile.REGTEST,
    chain_kind=ChainKind.SHA256,
    datadir=RUNTIME_DIR / "bitcoin-sha256",
    rpc_port=18443,
    p2p_port=18444,
    rpc_user="swapuser_sha256",
    rpc_password="swappass_sha256",
)

CHAIN_BLAKE2B_REGTEST = BitcoinNodeConfig(
    label="Bitcoin (blake2b) [regtest simulation]",
    network_profile=NetworkProfile.REGTEST,
    chain_kind=ChainKind.BLAKE2B,
    datadir=RUNTIME_DIR / "bitcoin-blake2b",
    rpc_port=18453,
    p2p_port=18454,
    rpc_user="swapuser_blake2b",
    rpc_password="swappass_blake2b",
)

# --- TESTNET4 profile: real public testnet, worthless coins, recommended --
# as the first stop before mainnet. Point these at your own synced nodes
# via the env vars below (defaults assume standard ports / your default
# datadir + cookie auth).

CHAIN_SHA256_TESTNET4 = BitcoinNodeConfig(
    label="Bitcoin (sha256) [testnet4]",
    network_profile=NetworkProfile.TESTNET4,
    chain_kind=ChainKind.SHA256,
    datadir=Path(_env("ATOMIC_SWAP_TESTNET4_SHA256_DATADIR", str(Path.home() / ".bitcoin"))),
    rpc_host=_env("ATOMIC_SWAP_TESTNET4_SHA256_RPC_HOST", "127.0.0.1"),
    rpc_port=int(_env("ATOMIC_SWAP_TESTNET4_SHA256_RPC_PORT", "48332")),
    p2p_port=int(_env("ATOMIC_SWAP_TESTNET4_SHA256_P2P_PORT", "48333")),
    rpc_user=_env("ATOMIC_SWAP_TESTNET4_SHA256_RPC_USER"),
    rpc_password=_env("ATOMIC_SWAP_TESTNET4_SHA256_RPC_PASSWORD"),
    rpc_cookie_file=Path(_env("ATOMIC_SWAP_TESTNET4_SHA256_COOKIE", str(Path.home() / ".bitcoin/testnet4/.cookie"))),
    binary_path=_env("ATOMIC_SWAP_TESTNET4_SHA256_BITCOIND_BINARY", "bitcoind"),
)

CHAIN_BLAKE2B_TESTNET4 = BitcoinNodeConfig(
    label="Bitcoin Knots BLAKE2b fork / BTCB2 [testnet4]",
    network_profile=NetworkProfile.TESTNET4,
    chain_kind=ChainKind.BLAKE2B,
    datadir=Path(_env("ATOMIC_SWAP_TESTNET4_BLAKE2B_DATADIR", str(Path.home() / ".bitcoin-knots-blake2b"))),
    rpc_host=_env("ATOMIC_SWAP_TESTNET4_BLAKE2B_RPC_HOST", "127.0.0.1"),
    rpc_port=int(_env("ATOMIC_SWAP_TESTNET4_BLAKE2B_RPC_PORT", "48432")),
    p2p_port=int(_env("ATOMIC_SWAP_TESTNET4_BLAKE2B_P2P_PORT", "48433")),
    rpc_user=_env("ATOMIC_SWAP_TESTNET4_BLAKE2B_RPC_USER"),
    rpc_password=_env("ATOMIC_SWAP_TESTNET4_BLAKE2B_RPC_PASSWORD"),
    rpc_cookie_file=Path(
        _env("ATOMIC_SWAP_TESTNET4_BLAKE2B_COOKIE", str(Path.home() / ".bitcoin-knots-blake2b/testnet4/.cookie"))
    ),
    binary_path=_env("ATOMIC_SWAP_TESTNET4_BLAKE2B_BITCOIND_BINARY", "bitcoind-knots-blake2b"),
)

# --- MAINNET profile: REAL Bitcoin, REAL money. --------------------------

CHAIN_SHA256_MAINNET = BitcoinNodeConfig(
    label="Bitcoin (sha256) [MAINNET -- real funds]",
    network_profile=NetworkProfile.MAINNET,
    chain_kind=ChainKind.SHA256,
    datadir=Path(_env("ATOMIC_SWAP_MAINNET_SHA256_DATADIR", str(Path.home() / ".bitcoin"))),
    rpc_host=_env("ATOMIC_SWAP_MAINNET_SHA256_RPC_HOST", "127.0.0.1"),
    rpc_port=int(_env("ATOMIC_SWAP_MAINNET_SHA256_RPC_PORT", "8332")),
    p2p_port=int(_env("ATOMIC_SWAP_MAINNET_SHA256_P2P_PORT", "8333")),
    rpc_user=_env("ATOMIC_SWAP_MAINNET_SHA256_RPC_USER"),
    rpc_password=_env("ATOMIC_SWAP_MAINNET_SHA256_RPC_PASSWORD"),
    rpc_cookie_file=Path(_env("ATOMIC_SWAP_MAINNET_SHA256_COOKIE", str(Path.home() / ".bitcoin/.cookie"))),
    binary_path=_env("ATOMIC_SWAP_MAINNET_SHA256_BITCOIND_BINARY", "bitcoind"),
)

CHAIN_BLAKE2B_MAINNET = BitcoinNodeConfig(
    label="Bitcoin Knots BLAKE2b fork / BTCB2 [MAINNET -- real funds, experimental/unaudited fork]",
    network_profile=NetworkProfile.MAINNET,
    chain_kind=ChainKind.BLAKE2B,
    datadir=Path(_env("ATOMIC_SWAP_MAINNET_BLAKE2B_DATADIR", str(Path.home() / ".bitcoin-knots-blake2b"))),
    rpc_host=_env("ATOMIC_SWAP_MAINNET_BLAKE2B_RPC_HOST", "127.0.0.1"),
    rpc_port=int(_env("ATOMIC_SWAP_MAINNET_BLAKE2B_RPC_PORT", "8432")),
    p2p_port=int(_env("ATOMIC_SWAP_MAINNET_BLAKE2B_P2P_PORT", "8433")),
    rpc_user=_env("ATOMIC_SWAP_MAINNET_BLAKE2B_RPC_USER"),
    rpc_password=_env("ATOMIC_SWAP_MAINNET_BLAKE2B_RPC_PASSWORD"),
    rpc_cookie_file=Path(
        _env("ATOMIC_SWAP_MAINNET_BLAKE2B_COOKIE", str(Path.home() / ".bitcoin-knots-blake2b/.cookie"))
    ),
    binary_path=_env("ATOMIC_SWAP_MAINNET_BLAKE2B_BITCOIND_BINARY", "bitcoind-knots-blake2b"),
)

CHAINS: dict[tuple[str, str], BitcoinNodeConfig] = {
    ("regtest", "sha256"): CHAIN_SHA256_REGTEST,
    ("regtest", "blake2b"): CHAIN_BLAKE2B_REGTEST,
    ("testnet4", "sha256"): CHAIN_SHA256_TESTNET4,
    ("testnet4", "blake2b"): CHAIN_BLAKE2B_TESTNET4,
    ("mainnet", "sha256"): CHAIN_SHA256_MAINNET,
    ("mainnet", "blake2b"): CHAIN_BLAKE2B_MAINNET,
}


def _lightningd_binary(profile: NetworkProfile, chain: ChainKind) -> str:
    if chain is ChainKind.BLAKE2B and profile is not NetworkProfile.REGTEST:
        env_key = f"ATOMIC_SWAP_{profile.value.upper()}_BLAKE2B_LIGHTNINGD_BINARY"
        # Standard install location documented in README for the
        # privkeyio/lightning blake2b-unified fork's prebuilt binary.
        return _env(env_key, str(RUNTIME_DIR / "bin" / "lightningd-blake2b"))
    return "lightningd"


def _make_node(person: str, chain: str, profile: NetworkProfile, rpc_port: int, hold_grpc_port: int) -> LightningNodeConfig:
    bitcoin = CHAINS[(profile.value, chain)]
    suffix = "" if profile is NetworkProfile.REGTEST else f"-{profile.value}"
    name = f"{person}-{chain}{suffix}"
    return LightningNodeConfig(
        label=name,
        network_profile=profile,
        lightning_dir=RUNTIME_DIR / f"lightning-{name}",
        network=_lightning_network_name(profile),
        rpc_port=rpc_port,
        hold_grpc_port=hold_grpc_port,
        bitcoin=bitcoin,
        binary_path=_lightningd_binary(profile, ChainKind(chain)),
    )


# --- Node registry: {alice,bob} x {sha256,blake2b} x {regtest,testnet4,mainnet} --
# Regtest names are unchanged from the original simulation-only version
# of this project for backwards compatibility (e.g. "alice-sha256").
# Real-network names carry an explicit profile suffix so they can never
# be confused with the local simulation (e.g. "alice-sha256-mainnet",
# "alice-blake2b-testnet4").

LIGHTNING_NODES: dict[str, LightningNodeConfig] = {
    # regtest (local simulation / fast dev loop)
    "alice-sha256": _make_node("alice", "sha256", NetworkProfile.REGTEST, 19735, 20001),
    "bob-sha256": _make_node("bob", "sha256", NetworkProfile.REGTEST, 19736, 20002),
    "alice-blake2b": _make_node("alice", "blake2b", NetworkProfile.REGTEST, 19745, 20003),
    "bob-blake2b": _make_node("bob", "blake2b", NetworkProfile.REGTEST, 19746, 20004),
    # testnet4 (real public network, worthless coins -- recommended pre-mainnet step)
    "alice-sha256-testnet4": _make_node("alice", "sha256", NetworkProfile.TESTNET4, 19835, 21001),
    "bob-sha256-testnet4": _make_node("bob", "sha256", NetworkProfile.TESTNET4, 19836, 21002),
    "alice-blake2b-testnet4": _make_node("alice", "blake2b", NetworkProfile.TESTNET4, 19845, 21003),
    "bob-blake2b-testnet4": _make_node("bob", "blake2b", NetworkProfile.TESTNET4, 19846, 21004),
    # mainnet (REAL FUNDS)
    "alice-sha256-mainnet": _make_node("alice", "sha256", NetworkProfile.MAINNET, 19935, 22001),
    "bob-sha256-mainnet": _make_node("bob", "sha256", NetworkProfile.MAINNET, 19936, 22002),
    "alice-blake2b-mainnet": _make_node("alice", "blake2b", NetworkProfile.MAINNET, 19945, 22003),
    "bob-blake2b-mainnet": _make_node("bob", "blake2b", NetworkProfile.MAINNET, 19946, 22004),
}


def node(name: str) -> LightningNodeConfig:
    try:
        return LIGHTNING_NODES[name]
    except KeyError as exc:
        raise ValueError(
            f"Unknown lightning node {name!r}; expected one of {sorted(LIGHTNING_NODES)}"
        ) from exc
