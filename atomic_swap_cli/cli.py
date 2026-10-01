"""Command-line interface for the atomic-swap demo.

All commands operate against the local swap database (see `store.py`)
and, when not given `--dry-run`/mock flags, talk to real CLN + hold
gRPC endpoints configured in `config.py`. Run `atomic-swap-cli --help`
or see the README for the full walkthrough.
"""
from __future__ import annotations

import sys

import click

from . import config
from .bitcoin_rpc import BitcoinRpcClient
from .cln_rpc import ClnRpcClient
from .hold_client import HoldClient
from .models import Swap, SwapState
from .safety import FundSafetyError, check_utxo_post_activation
from .store import SwapStore
from .swap_service import SwapService, sha256_hex


def _store() -> SwapStore:
    return SwapStore(config.DB_PATH)


def _hold_client_for(node_name: str) -> HoldClient:
    node = config.node(node_name)
    cert_dir = node.lightning_dir / node.network / "hold"
    return HoldClient.from_cert_dir(node.hold_grpc_host, node.hold_grpc_port, cert_dir)


def _cln_client_for(node_name: str) -> ClnRpcClient:
    node = config.node(node_name)
    socket_path = node.lightning_dir / node.network / "lightning-rpc"
    return ClnRpcClient(socket_path)


def _bitcoin_rpc_for(node_name: str) -> BitcoinRpcClient:
    return BitcoinRpcClient(config.node(node_name).bitcoin)


def _confirm_real_money(*node_names: str, yes_mainnet: bool) -> None:
    """Guardrail: refuse to touch any mainnet (real funds) node unless the
    caller passed --yes-mainnet. Always prints a warning banner for any
    real-network (mainnet or testnet4) node involved so it's never
    ambiguous whether a command is touching a real network."""
    nodes = [config.node(n) for n in node_names]
    mainnet_nodes = [n for n in nodes if n.network_profile is config.NetworkProfile.MAINNET]
    testnet_nodes = [n for n in nodes if n.network_profile is config.NetworkProfile.TESTNET4]

    if mainnet_nodes and not yes_mainnet:
        click.echo("REFUSING: this command touches MAINNET node(s) with REAL FUNDS:", err=True)
        for n in mainnet_nodes:
            click.echo(f"  - {n.label}  (chain: {n.bitcoin.label})", err=True)
        click.echo(
            "\nThis project drives Bitcoin mainnet alongside the Bitcoin Knots "
            "BLAKE2b fork (BTCB2) and the privkeyio/lightning fork -- both "
            "EXPERIMENTAL, UNAUDITED software per their own READMEs. Bugs here, "
            "in those forks, or in how you use this CLI can lose real money "
            "irreversibly.\n\nRe-run with --yes-mainnet only once you have read "
            "README.md's risk disclosure and understand/accept this risk.",
            err=True,
        )
        sys.exit(2)

    for n in mainnet_nodes:
        click.echo(f"\u26a0\ufe0f  MAINNET (REAL FUNDS): {n.label} on {n.bitcoin.label}")
    for n in testnet_nodes:
        click.echo(f"(testnet4, worthless coins: {n.label} on {n.bitcoin.label})")


def _print_swap(swap: Swap) -> None:
    click.echo(f"swap_id:          {swap.swap_id}")
    click.echo(f"role:             {swap.role.value}")
    click.echo(f"state:            {swap.state.value}")
    click.echo(f"payment_hash:     {swap.payment_hash}")
    click.echo(f"initiator chain:  {swap.initiator_chain} ({swap.initiator_node})")
    click.echo(f"responder chain:  {swap.responder_chain} ({swap.responder_node})")
    click.echo(f"amount initiator: {swap.amount_msat_initiator} msat")
    click.echo(f"amount responder: {swap.amount_msat_responder} msat")
    if swap.initiator_invoice:
        click.echo(f"initiator invoice: {swap.initiator_invoice}")
    if swap.responder_invoice:
        click.echo(f"responder invoice: {swap.responder_invoice}")
    if swap.error:
        click.echo(f"error:            {swap.error}")


@click.group()
def cli() -> None:
    """Cross-chain atomic swap CLI: Bitcoin (blake2b/BTCB2) <-> Bitcoin (sha256) over CLN hold invoices.

    Supports regtest (local dev-mode, no real funds), testnet4 (real
    public network, worthless coins), and mainnet (REAL FUNDS -- see
    README.md's risk disclaimer; mainnet commands require --yes-mainnet).
    """


# --- channel management ----------------------------------------------------


@cli.command("open-channel")
@click.option("--from-node", required=True, help="Funding node name, e.g. alice-sha256")
@click.option("--to-node", required=True, help="Peer node name, e.g. bob-sha256")
@click.option("--amount-sat", default=1_000_000, show_default=True, type=int)
@click.option("--yes-mainnet", is_flag=True, help="Required to proceed if either node is on mainnet.")
@click.option("--dry-run", is_flag=True, help="Show what would happen (incl. estimated fee) without broadcasting.")
@click.option(
    "--accept-fund-safety-risk",
    is_flag=True,
    help=(
        "Override the blake2b-chain post-activation funding-UTXO check. "
        "Only use this if you have manually verified your funding coins "
        "per the privkeyio/lightning README -- see safety.py/README.md."
    ),
)
def open_channel(
    from_node: str, to_node: str, amount_sat: int, yes_mainnet: bool, dry_run: bool, accept_fund_safety_risk: bool
) -> None:
    """Connect + fund a channel between two lightning nodes on the same chain."""
    _confirm_real_money(from_node, to_node, yes_mainnet=yes_mainnet)
    from_cfg = config.node(from_node)

    if from_cfg.network_profile in config.REAL_NETWORK_PROFILES:
        # Fee estimate before broadcasting anything on a real network.
        try:
            fee_est = _bitcoin_rpc_for(from_node).estimatesmartfee(6)
            if "feerate" in fee_est:
                click.echo(f"estimated feerate (~6 blocks): {fee_est['feerate']} BTC/kvB")
            else:
                click.echo(f"fee estimate unavailable: {fee_est}")
        except Exception as exc:  # pragma: no cover - live-network only
            click.echo(f"fee estimate unavailable: {exc}")

        # Fund-safety check: on the blake2b chain, refuse to fund from
        # pre-activation coins unless explicitly overridden. CLN auto-
        # selects UTXOs, so we check every confirmed, unreserved output
        # currently in the funding node's wallet.
        if from_cfg.bitcoin.chain_kind is config.ChainKind.BLAKE2B and not accept_fund_safety_risk:
            funder = _cln_client_for(from_node)
            rpc = _bitcoin_rpc_for(from_node)
            outputs = funder.listfunds().get("outputs", [])
            unsafe = []
            for out in outputs:
                if out.get("reserved") or out.get("status") != "confirmed":
                    continue
                try:
                    result = check_utxo_post_activation(rpc, from_cfg.bitcoin, out["txid"], out["output"])
                except FundSafetyError as exc:
                    unsafe.append(str(exc))
                    continue
                if not result.is_safe:
                    unsafe.append(f"{out['txid']}:{out['output']} -- {result.reason}")
            if unsafe:
                click.echo("REFUSING to fund: wallet holds pre-activation coin(s) on the blake2b chain:", err=True)
                for line in unsafe:
                    click.echo(f"  - {line}", err=True)
                click.echo(
                    "\nFunding a blake2b-chain channel with pre-activation coins reopens "
                    "the replay/signature exposure the unified-sig scheme prevents. "
                    "Re-run with --accept-fund-safety-risk only if you have manually "
                    "verified this is safe (see README.md).",
                    err=True,
                )
                sys.exit(2)

    if dry_run:
        click.echo(f"[dry-run] would connect {from_node} -> {to_node} and fund a {amount_sat} sat channel")
        return

    funder = _cln_client_for(from_node)
    peer = _cln_client_for(to_node)
    peer_info = peer.getinfo()
    peer_id = peer_info["id"]
    host, port = "127.0.0.1", peer_info.get("binding", [{}])[0].get("port", 9735)
    click.echo(f"connecting {from_node} -> {to_node} ({peer_id}@{host}:{port})")
    funder.connect(peer_id, host, port)
    result = funder.fundchannel(peer_id, amount_sat)
    click.echo(f"channel funded: txid={result.get('txid')}")


# --- swap lifecycle ----------------------------------------------------------


@cli.command()
@click.option("--initiator-node", required=True, help="e.g. alice-sha256")
@click.option("--responder-node", required=True, help="e.g. bob-blake2b")
@click.option("--initiator-chain", required=True, help="e.g. sha256")
@click.option("--responder-chain", required=True, help="e.g. blake2b")
@click.option("--amount-initiator-msat", required=True, type=int, help="amount initiator receives on their chain")
@click.option("--amount-responder-msat", required=True, type=int, help="amount responder receives on their chain")
@click.option("--yes-mainnet", is_flag=True, help="Required to proceed if either node is on mainnet.")
def initiate(
    initiator_node: str,
    responder_node: str,
    initiator_chain: str,
    responder_chain: str,
    amount_initiator_msat: int,
    amount_responder_msat: int,
    yes_mainnet: bool,
) -> None:
    """Initiate a swap: generate the secret and create the first hold invoice."""
    _confirm_real_money(initiator_node, responder_node, yes_mainnet=yes_mainnet)
    with _store() as store:
        service = SwapService(store)
        hold_client = _hold_client_for(initiator_node)
        swap = service.initiate(
            initiator_chain=initiator_chain,
            responder_chain=responder_chain,
            initiator_node=initiator_node,
            responder_node=responder_node,
            amount_msat_initiator=amount_initiator_msat,
            amount_msat_responder=amount_responder_msat,
            initiator_hold_client=hold_client,
        )
        _print_swap(swap)
        click.echo("\nGive the payment_hash and initiator invoice above to the counterparty.")


@cli.command()
@click.option("--payment-hash", required=True)
@click.option("--responder-node", required=True)
@click.option("--initiator-node", required=True)
@click.option("--initiator-chain", required=True)
@click.option("--responder-chain", required=True)
@click.option("--amount-initiator-msat", required=True, type=int)
@click.option("--amount-responder-msat", required=True, type=int)
@click.option("--yes-mainnet", is_flag=True, help="Required to proceed if either node is on mainnet.")
def accept(
    payment_hash: str,
    responder_node: str,
    initiator_node: str,
    initiator_chain: str,
    responder_chain: str,
    amount_initiator_msat: int,
    amount_responder_msat: int,
    yes_mainnet: bool,
) -> None:
    """Accept a swap as the counterparty: create the mirrored hold invoice."""
    _confirm_real_money(initiator_node, responder_node, yes_mainnet=yes_mainnet)
    with _store() as store:
        service = SwapService(store)
        hold_client = _hold_client_for(responder_node)
        swap = service.accept(
            payment_hash=payment_hash,
            initiator_chain=initiator_chain,
            responder_chain=responder_chain,
            initiator_node=initiator_node,
            responder_node=responder_node,
            amount_msat_initiator=amount_initiator_msat,
            amount_msat_responder=amount_responder_msat,
            responder_hold_client=hold_client,
        )
        _print_swap(swap)
        click.echo("\nGive the responder invoice above back to the initiator to pay.")


@cli.command("pay")
@click.option("--from-node", required=True, help="the node making the payment")
@click.option("--bolt11", required=True)
@click.option("--yes-mainnet", is_flag=True, help="Required to proceed if the node is on mainnet.")
@click.option("--dry-run", is_flag=True, help="Decode and show the invoice without paying it.")
def pay(from_node: str, bolt11: str, yes_mainnet: bool, dry_run: bool) -> None:
    """Pay a bolt11 invoice from the given node (used by both sides to lock HTLCs)."""
    _confirm_real_money(from_node, yes_mainnet=yes_mainnet)
    cln_client = _cln_client_for(from_node)
    if dry_run:
        decoded = cln_client.decode(bolt11)
        click.echo(f"[dry-run] would pay {decoded.get('amount_msat')} msat to payment_hash={decoded.get('payment_hash')}")
        return
    result = cln_client.pay(bolt11)
    click.echo(f"payment status: {result.get('status', 'unknown')}")
    if "payment_preimage" in result:
        click.echo(f"preimage revealed: {result['payment_preimage']}")


@cli.command()
@click.option("--swap-id", required=True)
def monitor(swap_id: str) -> None:
    """Poll both hold invoices for a swap, print their current state, and
    advance the local state machine (INITIATED/COUNTERPARTY_LOCKED ->
    BOTH_LOCKED) once both invoices report HTLCs ACCEPTED. Safe to call
    repeatedly while waiting for the counterparty to pay."""
    with _store() as store:
        service = SwapService(store)
        swap = store.get(swap_id)
        if swap is None:
            click.echo(f"no such swap: {swap_id}", err=True)
            sys.exit(1)
        _print_swap(swap)
        initiator_hold = _hold_client_for(swap.initiator_node)
        responder_hold = _hold_client_for(swap.responder_node)
        i_state = r_state = None
        try:
            i_status = initiator_hold.get_status(swap.payment_hash)
            i_state = i_status.state
            click.echo(f"initiator invoice state: {i_state}")
        except Exception as exc:  # pragma: no cover - live-network only
            click.echo(f"initiator invoice state: unknown ({exc})")
        try:
            r_status = responder_hold.get_status(swap.payment_hash)
            r_state = r_status.state
            click.echo(f"responder invoice state: {r_state}")
        except Exception as exc:  # pragma: no cover - live-network only
            click.echo(f"responder invoice state: unknown ({exc})")

        if swap.state == SwapState.INITIATED and r_state is not None:
            # The initiator learns the responder created their mirrored
            # invoice (in this single-operator demo, its mere existence
            # confirms it). In a real two-party deployment this would be
            # driven by an out-of-band message from the counterparty.
            swap = service.mark_counterparty_locked(swap)

        if swap.state == SwapState.COUNTERPARTY_LOCKED and i_state == "ACCEPTED" and r_state == "ACCEPTED":
            own_hold = initiator_hold if swap.role.value == "initiator" else responder_hold
            own_hash = swap.payment_hash
            other_status_fn = (lambda: responder_hold.get_status(swap.payment_hash)) if swap.role.value == "initiator" else (
                lambda: initiator_hold.get_status(swap.payment_hash)
            )
            swap = service.mark_both_locked_if_ready(
                swap,
                own_hold_client=own_hold,
                own_payment_hash=own_hash,
                counterparty_status_fn=other_status_fn,
            )
            if swap.state == SwapState.BOTH_LOCKED:
                click.echo("\nboth hold invoices ACCEPTED -> swap is BOTH_LOCKED, ready to settle")


@cli.command()
@click.option("--swap-id", required=True)
def settle(swap_id: str) -> None:
    """Release the preimage for a swap (initiator side) to settle both legs."""
    with _store() as store:
        service = SwapService(store)
        swap = store.get(swap_id)
        if swap is None:
            click.echo(f"no such swap: {swap_id}", err=True)
            sys.exit(1)
        node_name = swap.initiator_node
        hold_client = _hold_client_for(node_name)
        swap = service.settle(swap, hold_client)
        click.echo(f"swap {swap_id} settled; preimage: {swap.preimage}")


@cli.command("settle-with-preimage")
@click.option("--swap-id", required=True)
@click.option("--preimage", required=True, help="preimage observed from the completed outgoing payment")
def settle_with_preimage(swap_id: str, preimage: str) -> None:
    """Responder-side settle: release the responder's own hold invoice
    using a preimage learned from the network (e.g. the
    `payment_preimage` printed by `pay` once the initiator settles their
    leg and the payment to them completes)."""
    with _store() as store:
        service = SwapService(store)
        swap = store.get(swap_id)
        if swap is None:
            click.echo(f"no such swap: {swap_id}", err=True)
            sys.exit(1)
        node_name = swap.responder_node
        hold_client = _hold_client_for(node_name)
        swap = service.settle_with_preimage(swap, preimage, hold_client)
        click.echo(f"swap {swap_id} settled; preimage: {swap.preimage}")


@cli.command()
@click.option("--swap-id", required=True)
@click.option("--reason", default="timeout")
def refund(swap_id: str, reason: str) -> None:
    """Cancel a swap's hold invoice (timeout/refund path)."""
    with _store() as store:
        service = SwapService(store)
        swap = store.get(swap_id)
        if swap is None:
            click.echo(f"no such swap: {swap_id}", err=True)
            sys.exit(1)
        node_name = swap.initiator_node if swap.role.value == "initiator" else swap.responder_node
        hold_client = _hold_client_for(node_name)
        swap = service.refund(swap, hold_client, reason=reason)
        click.echo(f"swap {swap_id} refunded: {reason}")


@cli.command("list")
def list_swaps() -> None:
    """List all known swaps."""
    with _store() as store:
        swaps = store.list_all()
        if not swaps:
            click.echo("no swaps found")
            return
        for swap in swaps:
            click.echo(f"{swap.swap_id}  {swap.state.value:22s}  {swap.payment_hash[:16]}...")


@cli.command()
@click.option("--swap-id", required=True)
def status(swap_id: str) -> None:
    """Show full details for one swap."""
    with _store() as store:
        swap = store.get(swap_id)
        if swap is None:
            click.echo(f"no such swap: {swap_id}", err=True)
            sys.exit(1)
        _print_swap(swap)


def main() -> None:
    cli()


if __name__ == "__main__":
    main()
