"""Fund-safety checks for the Bitcoin Knots BLAKE2b fork chain.

Per the privkeyio/lightning (blake2b-unified) README: a blake2b-chain
channel/swap-leg must only be funded with coins **received at or after**
that chain's split-activation height (961,640 mainnet / 150,308
testnet4). Coins received *before* activation share history with the
pre-split chain and funding a channel with them reopens the exact
cross-chain replay/signature exposure that the opt-in SIGHASH_UNIFIED
scheme exists to prevent.

This module provides an automated best-effort check of that rule by
querying bitcoind for the height at which a candidate funding UTXO was
confirmed. It is a safety net, not a substitute for reading the
upstream fork's README yourself -- there are edge cases (e.g. coins
moved through multiple hops since activation, wallet coin selection
across several inputs) this single-UTXO check does not fully cover.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .bitcoin_rpc import BitcoinRpcClient
from .config import BitcoinNodeConfig, ChainKind


class FundSafetyError(RuntimeError):
    """Raised when a candidate funding UTXO fails the post-activation check."""


@dataclass
class UtxoActivationCheck:
    txid: str
    vout: int
    confirmations: int
    height: Optional[int]  # None if unconfirmed (still in mempool)
    activation_height: Optional[int]  # None if not applicable (sha256 chain / regtest)
    is_safe: bool
    reason: str


def check_utxo_post_activation(
    rpc: BitcoinRpcClient, cfg: BitcoinNodeConfig, txid: str, vout: int
) -> UtxoActivationCheck:
    """Check whether funding a channel from (txid, vout) on `cfg`'s chain
    is safe per the post-activation funding rule. Always returns
    `is_safe=True` for the sha256 chain or regtest, where the rule does
    not apply."""
    activation_height = cfg.activation_height  # None unless blake2b + real network
    if cfg.chain_kind is not ChainKind.BLAKE2B or activation_height is None:
        return UtxoActivationCheck(
            txid=txid,
            vout=vout,
            confirmations=0,
            height=None,
            activation_height=None,
            is_safe=True,
            reason="not applicable: sha256 chain or regtest (no real chain split)",
        )

    utxo = rpc.gettxout(txid, vout, include_mempool=True)
    if utxo is None:
        raise FundSafetyError(f"UTXO {txid}:{vout} not found or already spent")

    confirmations = int(utxo.get("confirmations", 0))
    if confirmations <= 0:
        return UtxoActivationCheck(
            txid=txid,
            vout=vout,
            confirmations=confirmations,
            height=None,
            activation_height=activation_height,
            is_safe=False,
            reason=(
                "UTXO is unconfirmed; wait for it to confirm so its height "
                "relative to the activation height can be verified before funding"
            ),
        )

    tip = rpc.getblockcount()
    height = tip - confirmations + 1
    is_safe = height >= activation_height
    reason = (
        f"UTXO confirmed at height {height}, activation height is {activation_height} -> "
        + ("safe (post-activation)" if is_safe else "UNSAFE (pre-activation coin)")
    )
    return UtxoActivationCheck(
        txid=txid,
        vout=vout,
        confirmations=confirmations,
        height=height,
        activation_height=activation_height,
        is_safe=is_safe,
        reason=reason,
    )


def assert_safe_to_fund(rpc: BitcoinRpcClient, cfg: BitcoinNodeConfig, txid: str, vout: int) -> UtxoActivationCheck:
    """Like `check_utxo_post_activation`, but raises FundSafetyError if
    the UTXO is not safe to use for funding. Use this directly in code
    paths that are about to broadcast a funding transaction."""
    result = check_utxo_post_activation(rpc, cfg, txid, vout)
    if not result.is_safe:
        raise FundSafetyError(
            f"refusing to fund from {txid}:{vout} on {cfg.label}: {result.reason}"
        )
    return result
