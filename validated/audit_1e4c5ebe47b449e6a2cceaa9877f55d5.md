Based on the analysis, I found a genuine asymmetry: `store_and_process_block_signature` (the path triggered when observing *other signers'* `BlockAccepted` messages reach the 70% threshold, at `stacks-signer/src/v0/signer.rs:2443-2537`) calls `block_info.mark_locally_accepted(true)` directly on threshold, with **no call to `get_signed_conflicts`, `reorg_permit_stands`, or `conflict_still_blocks`** — unlike the pre-commit path (`handle_block_pre_commit`, `signer.rs:1383-1465`), which explicitly re-derives the conflict set before signing. [1](#0-0) 

However, I could not fully verify whether this is actually exploitable by the constrained attacker, because:

1. The `signed_group` path is fed by `add_block_signature`, which only accepts *signatures from other signers* — an unprivileged attacker holding only one miner slot's BTC and no signer key cannot forge a valid `BlockAccepted` signature; `handle_block_signature` requires signature recovery (`Secp256k1PublicKey::recover_to_pubkey_without_validating_low_s`) and `is_valid_signer` checks against the real signer set.
2. Reaching the 70% "acceptance threshold" via `signed_group` inherently requires that a real majority of signer weight already validated and signed a *conflicting* sibling block through the (guarded) pre-commit-to-signature path for the *other* sibling — i.e., getting into this state at all would already imply a majority-signer condition or a genuine double-sign already produced elsewhere, not something achievable by an attacker with one slot's proposal-crafting power alone.
3. I was unable to trace, within the available context, an exact single-attacker-controlled scenario where `store_and_process_block_signature`'s missing conflict-recheck is reached with a genuinely conflicting sibling block while all other guards (chainstate re-check, `check_block_against_signer_db_state`, freshness, `conflict_still_blocks`) are bypassable using only crafted `BlockProposal`s and gossiped messages from a single slot.

Given the attacker model constraints (one miner slot's BTC, own crafted proposals/gossip, at most one signer's weight, no majority-signer collusion, no auth_token), I cannot construct a reproducible single-attacker path that reaches `store_and_process_block_signature`'s un-rechecked `mark_locally_accepted(true)` with a genuinely still-live conflicting sibling, because doing so requires that the local signer already has stored a fresh conflicting signed block (`signed_self`) at the same height — and the moment `add_block_signature` records this instance's own conflicting signature is already gated by the pre-commit path's guard. The gap exists in code but I could not establish an end-to-end reachable exploit chain usable by the constrained unprivileged attacker as specified, given the available index coverage. Given the uncertainty in fully tracing this specific path and the significant investigation already done showing the primary guard (`get_signed_conflicts`/`conflict_still_blocks`) is deliberately and symmetrically enforced in the two documented equivocation code paths (`handle_block_pre_commit` and `check_block_against_signer_db_state`), and the extensive test coverage (`signer_refuses_to_sign_second_sibling_tenure_start`, `fresh_conflict_in_another_tenure_blocks_signing`, `stale_sibling_still_refused_when_canonical_tip_at_height`, etc.) confirming the guard's invariants hold across the scenarios reachable by a single proposer/slot attacker:

#No vulnerability found for this question.

### Citations

**File:** stacks-signer/src/v0/signer.rs (L2503-2537)
```rust
        if min_weight > total_signature_weight {
            info!("{self}: Received block acceptance, but have not yet reached the acceptance threshold.";
                "signer_signature_hash" => %block_hash,
                "signature_weight" => signature_weight,
                "consensus_hash" => %block_info.block.header.consensus_hash,
                "block_height" => block_info.block.header.chain_length,
                "total_weight_approved" => total_signature_weight,
                "total_weight" => total_weight,
                "percent_approved" => (total_signature_weight as f64 / total_weight as f64 * 100.0),
            );
            return;
        }
        info!("{self}: have reached the block acceptance threshold";
            "signer_signature_hash" => %block_hash,
            "signature_weight" => signature_weight,
            "consensus_hash" => %block_info.block.header.consensus_hash,
            "block_height" => block_info.block.header.chain_length,
            "total_weight_approved" => total_signature_weight,
            "total_weight" => total_weight,
            "percent_approved" => (total_signature_weight as f64 / total_weight as f64 * 100.0),
        );

        // have enough signatures to broadcast!
        // move block to LOCALLY accepted state.
        // It is only considered globally accepted IFF we receive a new block event confirming it OR see the chain tip of the node advance to it.
        if let Err(e) = block_info.mark_locally_accepted(true) {
            if !block_info.has_reached_consensus() {
                warn!("{self}: Failed to mark block as locally accepted: {e:?}");
            }
        }
        let _ = self.signer_db.insert_block(block_info).map_err(|e| {
            warn!("Failed to set group threshold signature timestamp for {block_hash}: {e:?}");
            panic!("{self} Failed to write block to signerdb: {e}");
        });
        self.broadcast_signed_block(stacks_client, block_info.block.clone(), &addrs_to_sigs);
```
