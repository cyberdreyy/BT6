## Analysis

This maps cleanly onto the report's bug class: an "already exists" check that uses the wrong state predicate, letting a duplicate/conflicting object slip past a supposedly definitive guard. In RocketJoe, `createPair()`'s existence check could be front-run so the legitimate creation call silently fails. In `stacks-signer`, the analogous equality is **"has this signer already signed a block in this tenure?"**, enforced by `validate_tenure_change_payload`'s `DuplicateBlockFound` check — and the v1 implementation queries the wrong state.

### Root cause

`SortitionsView::validate_tenure_change_payload` (v1) checks for a duplicate tenure-start block using: [1](#0-0) 

This calls `signer_db.get_last_globally_accepted_block`, which only returns a block once **the whole signer set** has reached consensus on it — not once *this* signer has locally accepted (signed) it.

The v2 equivalent was fixed to use `get_last_signed_block`, which counts locally-accepted blocks too: [2](#0-1) 

The regression test explicitly documents that this was a real, previously-shipped bug in the shared logic ("Before the fix, this would have incorrectly passed because `get_last_globally_accepted_block` would not find the locally-accepted block"), and v1 still contains the pre-fix behavior: [3](#0-2) 

The project's own internal documentation states the asymmetry directly and flags that this proposal-time check is never re-run later in the pipeline: [4](#0-3) 

### Why this is the RocketJoe analog

- **RocketJoe**: `createPair()`'s revert-on-exists check can't tell a legitimate pair from a griefer's pre-created pair — the check uses the wrong "existence" fact, permanently blocking legitimate state transitions.
- **stacks-signer v1**: `validate_tenure_change_payload`'s duplicate check uses the wrong "already signed" fact (global-only instead of local-or-global), so a **single miner** (one slot, no other signer's key or majority needed) can re-propose a second, different tenure-start block for a tenure this signer has *already locally accepted/signed*, and `check_proposal` will not reject it as `DuplicateBlockFound`. The proposal is then submitted to the node for validation and can reach the pre-commit stage.

The only remaining backstop is the pre-commit-time conflict re-check in `handle_block_pre_commit`, which is time-boundedly effective (`freshness_cutoff = now - tenure_last_block_proposal_timeout`) and calls out to the node (`conflict_still_blocks`, `reorg_permit_stands`): [5](#0-4) 

If the first (locally-accepted) block's `last_endorsed` timestamp has aged past `tenure_last_block_proposal_timeout` before the second proposal crosses the pre-commit threshold — a realistic window given typical timeout configs and miner-controlled proposal timing — the stale-conflict path takes over and no longer vetoes the signature: [6](#0-5) 

This lets a v1 signer sign two different, conflicting tenure-start blocks for the same tenure — breaking the "one signature per tenure-start" equality — solely through a single miner's own proposal sequencing, with no cooperation from other signers.

### Title
Weak Duplicate-Block Check in v1 `validate_tenure_change_payload` Lets a Single Miner Get a Locally-Signed Conflicting Tenure-Start Block Past the Proposal Guard - (`stacks-signer/src/chainstate/v1.rs`)

### Summary
The v1 chainstate's tenure-change duplicate check (`validate_tenure_change_payload`) only queries `get_last_globally_accepted_block`, ignoring blocks this signer has *locally* accepted (signed) but that have not yet reached global consensus. A single miner can therefore re-propose a second, different tenure-start block for a tenure the signer has already signed, and the proposal-time guard will not flag it as `DuplicateBlockFound`.

### Finding Description
`validate_tenure_change_payload` (v1) is supposed to guarantee that a signer never processes a second competing tenure-start block once it has already committed a signature within that tenure. It implements this via a lookup at [7](#0-6) , using `get_last_globally_accepted_block` — a predicate that is only true once the *entire signer set* reaches consensus, not once *this* signer signs. The v2 chainstate uses the correct, stricter predicate `get_last_signed_block` (locally OR globally accepted) at [8](#0-7) , and a regression test confirms this was a genuine, previously-exploitable gap in the shared logic [9](#0-8) .

Because the duplicate check runs only once, at proposal arrival, and is never re-evaluated at validate-ok time [4](#0-3) , a v1 signer that has locally accepted block A in tenure T will accept a differently-constructed tenure-start block B for the same tenure T past `check_proposal`, submit it for node validation, and carry it through to the pre-commit stage. The last remaining safeguard — the pre-commit-time "signed conflicts" re-check in `handle_block_pre_commit` — only vetoes the signature while the conflicting signature is still "fresh" (`last_endorsed > freshness_cutoff`) [10](#0-9) ; once that window elapses, the stale-conflict branch permits signing block B despite the still-standing signature on block A [6](#0-5) .

### Impact Explanation
This lets a lone miner (no other signer key, no majority) obtain the signer's signature on two different, conflicting blocks within the same tenure — directly matching the "Critical" bucket: a signer producing a signature on a conflicting block, undermining the one-block-per-tenure-start invariant the duplicate check exists to enforce.

### Likelihood Explanation
Reachable by a single, ordinary miner slot: propose block A, wait for it to be locally accepted, then propose a different block B for the same tenure once the freshness window on A's local-acceptance timestamp has lapsed (a miner fully controls proposal timing and can simply wait past `tenure_last_block_proposal_timeout`). No cooperation from other signers or privileged access is required.

### Recommendation
Change v1's `validate_tenure_change_payload` to use `get_last_signed_block` (as v2 already does), so the duplicate-block check reflects this signer's own locally-accepted signature, not only globally-accepted consensus.

### Proof of Concept
1. Miner proposes tenure-start block A for tenure T; signer's `check_proposal` (v1) passes, node validates it, signer locally accepts (signs) A via `mark_locally_accepted` [11](#0-10) .
2. Block A fails to gather the network-wide pre-commit/signature threshold (e.g. other signers are slow/offline), so it never becomes globally accepted.
3. Miner waits until A's `last_endorsed`/local-acceptance timestamp passes `tenure_last_block_proposal_timeout`, then proposes a different tenure-start block B for the same tenure T (different transactions/timestamp).
4. `validate_tenure_change_payload` (v1) queries `get_last_globally_accepted_block(T)` — returns `None` since A is only locally accepted — so the `DuplicateBlockFound` check does not fire, and `check_proposal` accepts B.
5. B is submitted to the node, validated, and reaches the pre-commit stage; because A's conflict is now stale, the pre-commit conflict guard in `handle_block_pre_commit` no longer blocks it, and the signer signs B — producing signatures on two conflicting blocks for the same tenure.

### Citations

**File:** stacks-signer/src/chainstate/v1.rs (L505-518)
```rust
        let last_in_current_tenure = signer_db
            .get_last_globally_accepted_block(&block.header.consensus_hash)
            .map_err(|e| {
                SignerChainstateError::from(ClientError::InvalidResponse(e.to_string()))
            })?;
        if let Some(last_in_current_tenure) = last_in_current_tenure {
            warn!(
                "Miner block proposal contains a tenure change, but we've already signed a block in this tenure. Considering proposal invalid.";
                "proposed_block_consensus_hash" => %block.header.consensus_hash,
                "proposed_block_signer_signature_hash" => %block.header.signer_signature_hash(),
                "last_in_tenure_signer_signature_hash" => %last_in_current_tenure.block.header.signer_signature_hash(),
            );
            return Err(RejectReason::DuplicateBlockFound);
        }
```

**File:** stacks-signer/src/chainstate/v2.rs (L340-357)
```rust
        // We already confirmed in check miner activity that the current tenure is valid. So check we are not
        // reorging the tenure blocks. Only blocks we have signed (locally or globally accepted) count
        // here: a block we have merely pre-committed to carries no signature from us, so it is safe to
        // accept a competing tenure-start block in its place if it failed to reach consensus.
        let last_in_current_tenure = signer_db
            .get_last_signed_block(&block.header.consensus_hash)
            .map_err(|e| {
                SignerChainstateError::from(ClientError::InvalidResponse(e.to_string()))
            })?;
        if let Some(last_in_current_tenure) = last_in_current_tenure {
            warn!(
                "Miner block proposal contains a tenure change, but we've already signed a block in this tenure. Considering proposal invalid.";
                "proposed_block_consensus_hash" => %block.header.consensus_hash,
                "proposed_block_signer_signature_hash" => %block.header.signer_signature_hash(),
                "last_in_tenure_signer_signature_hash" => %last_in_current_tenure.block.header.signer_signature_hash(),
            );
            return Err(RejectReason::DuplicateBlockFound);
        }
```

**File:** stacks-signer/src/chainstate/tests/v2.rs (L801-850)
```rust
    let mut existing_block_info = BlockInfo::from(existing_block_proposal);
    existing_block_info.mark_locally_accepted(false).unwrap();
    signer_db.insert_block(&existing_block_info).unwrap();

    // Now build a *second* tenure-start block proposal for the same tenure.
    // This simulates the miner attempting to replace their first block (e.g.,
    // with different transactions). The tenure change tx must have
    // cause=BlockFound with a coinbase to be recognized as a tenure-start block.
    let tenure_change_payload = TenureChangePayload {
        tenure_consensus_hash: cur_sortition.data.consensus_hash.clone(),
        prev_tenure_consensus_hash: cur_sortition.data.parent_tenure_id.clone(),
        burn_view_consensus_hash: cur_sortition.data.consensus_hash.clone(),
        previous_tenure_end: block.header.parent_block_id.clone(),
        previous_tenure_blocks: 1,
        cause: TenureChangeCause::BlockFound,
        pubkey_hash: Hash160::from_node_public_key(&StacksPublicKey::from_private(&block_sk)),
    };
    let tenure_change_tx = make_tenure_change_tx(tenure_change_payload);
    let coinbase_tx = StacksTransaction::new(
        TransactionVersion::Testnet,
        TransactionAuth::Standard(TransactionSpendingCondition::new_initial_sighash()),
        TransactionPayload::Coinbase(CoinbasePayload([0; 32]), None, Some(VRFProof::empty())),
    );
    *block.executed_and_skipped_txs_mut() = vec![tenure_change_tx, coinbase_tx];
    block.header.sign_miner(&block_sk).unwrap();

    let exit_flag = Arc::new(AtomicBool::new(false));
    let moved_exit_flag = exit_flag.clone();

    let serve = std::thread::spawn(move || {
        crate::client::tests::write_response_nonblockinig(
            &server,
            response.as_bytes(),
            moved_exit_flag,
        );
    });

    let result = sortitions_view.check_proposal(&stacks_client, &mut signer_db, &block);

    exit_flag.store(true, Ordering::SeqCst);
    serve.join().unwrap();

    // The proposal should be rejected because there's already a locally-accepted
    // block in this tenure. Before the fix, this would have incorrectly passed
    // because get_last_globally_accepted_block would not find the locally-accepted block.
    assert!(
        matches!(result, Err(RejectReason::DuplicateBlockFound)),
        "Expected DuplicateBlockFound rejection when a locally-accepted block exists in the tenure, got: {result:?}"
    );
}
```

**File:** docs/signer-flows.md (L425-437)
```markdown
Two things belong to the proposal path only and are **not** re-run at validate-ok
or at signing:

- `validate_tenure_change_payload` rejects with `DuplicateBlockFound` when we
  have already accepted a block in the tenure a tenure-change block is starting.
  v2 counts locally or globally accepted blocks (`get_last_signed_block`); v1
  counts only globally accepted ones (`get_last_globally_accepted_block`).
- the v2 `check_proposal` wrapper checks miner pubkey hash, consensus hash, the
  pox bitvec, and tenure-extend rules before delegating here.

Because the duplicate check never runs again, a block that crosses the pre-commit
threshold long after it was proposed relies on section 5's own-tenure conflict
guard to cover the same ground.
```

**File:** stacks-signer/src/v0/signer.rs (L1368-1421)
```rust
        // A pre-commit may be superseded by a competing proposal at the same height (e.g. a
        // re-proposed tenure-start block after the first failed to reach consensus), but a
        // signature must not be superseded while it's still "fresh". A signed block at the
        // same or higher height in ANY tenure is a conflict: two blocks at the same height are
        // siblings no matter which tenure they belong to (e.g. the next tenure's tenure-start
        // block conflicts with the current tenure's block at the same height). Blocks in
        // tenures whose reorg we sanctioned under the reorg-timing rules are excluded, but
        // only while the sortition the permit was granted to is still canonical
        // (`check_parent_tenure_choice` records the permit, `reorg_permit_stands` re-derives
        // its validity from the node); every other question about whether a conflict is
        // still live is derived from the node in `conflict_still_blocks`.
        //
        // Unlike the chainstate check above, a refusal here is "for now" rather than a
        // broadcast rejection: a later pre-commit re-evaluation may still sign the block once
        // the conflicting signature has gone stale.
        let conflicts = match self
            .signer_db
            .get_signed_conflicts(block_info.block.header.chain_length, &block_hash)
        {
            Ok(conflicts) => conflicts,
            Err(e) => {
                warn!("{self}: Failed to query the signed blocks. Refusing to sign block {block_hash}: {e:?}");
                return;
            }
        };
        let freshness_cutoff = get_epoch_time_secs().saturating_sub(
            self.proposal_config
                .tenure_last_block_proposal_timeout
                .as_secs(),
        );
        // A fresh signature only blocks while the block it covers could still be part of the
        // chain: see `conflict_still_blocks`, which asks the node whether it is. Check
        // freshness first: it is a local timestamp comparison, while `reorg_permit_stands`
        // and `conflict_still_blocks` each query the node, so stale conflicts cost no
        // round-trips.
        if let Some(conflict) = conflicts.iter().find(|conflict| {
            conflict.last_endorsed > freshness_cutoff
                && !self.reorg_permit_stands(stacks_client, conflict)
                && self.conflict_still_blocks(
                    stacks_client,
                    conflict,
                    block_info.block.header.chain_length,
                )
        }) {
            warn!(
                "{self}: Reached the pre-commit threshold for a block, but we have recently signed or accepted a different block at the same or higher height. Refusing to sign.";
                "signer_signature_hash" => %block_hash,
                "block_height" => block_info.block.header.chain_length,
                "conflicting_signer_signature_hash" => %conflict.signer_signature_hash,
                "conflicting_block_height" => conflict.stacks_height,
                "conflicting_consensus_hash" => %conflict.consensus_hash,
            );
            return;
        }
```

**File:** stacks-signer/src/v0/signer.rs (L1423-1465)
```rust
        // No conflict is both fresh and still live. A conflict that no longer matters, i.e.
        // stale, or provably dead per `conflict_still_blocks`, cannot veto on its own. A
        // stale conflict in another tenure in particular no longer speaks for us: whether this
        // block may replace what another tenure built is settled by the chainstate checks above.
        // A stale conflict in this block's own tenure still blocks if the node already has that
        // tenure at or above the proposed height, since the proposal then duplicates state the
        // node has already built on. (The chainstate checks don't cover this for tenure-change
        // blocks: those check the parent tenure instead of their own.)
        // The permit check is deferred to here so that only same-tenure conflicts pay for it.
        if conflicts.iter().any(|conflict| {
            conflict.consensus_hash == block_info.block.header.consensus_hash
                && !self.reorg_permit_stands(stacks_client, conflict)
        }) {
            match stacks_client.get_tenure_tip(&block_info.block.header.consensus_hash) {
                Ok(tip) => {
                    let tip_height = tip.anchored_header.height();
                    if tip_height >= block_info.block.header.chain_length {
                        warn!(
                            "{self}: Reached the pre-commit threshold for a block that conflicts with previously signed or accepted blocks, and the canonical tip of its tenure is already at or above the proposed height. Refusing to sign.";
                            "signer_signature_hash" => %block_hash,
                            "block_height" => block_info.block.header.chain_length,
                            "canonical_tip_height" => tip_height,
                        );
                        return;
                    }
                }
                Err(e) => {
                    warn!(
                        "{self}: Failed to fetch the canonical tip of the proposed block's tenure: {e:?}. Treating the tenure as unconfirmed.";
                        "signer_signature_hash" => %block_hash,
                        "consensus_hash" => %block_info.block.header.consensus_hash,
                    );
                }
            }
        }
        if !conflicts.is_empty() {
            info!(
                "{self}: Reached the pre-commit threshold for a block that conflicts with previously signed or accepted blocks, but none of those conflicts still blocks it. Signing the replacement.";
                "signer_signature_hash" => %block_hash,
                "block_height" => block_info.block.header.chain_length,
                "num_conflicts" => conflicts.len(),
            );
        }
```

**File:** stacks-signer/src/v0/signer.rs (L1961-1975)
```rust
            if let Err(e) = block_info.mark_pre_committed() {
                // The block may have reached enough signatures before we validated the block so should fail to mark pre-committed
                // but still call to make sure the timestamps and validity are updated correctly.
                if !block_info.has_reached_consensus()
                    && block_info.state != BlockState::LocallyAccepted
                {
                    warn!("{self}: Failed to mark block as approved: {e:?}",);
                    return;
                }
            }

            self.signer_db
                .insert_block(&block_info)
                .unwrap_or_else(|e| self.handle_insert_block_error(e));
            self.send_block_pre_commit(signer_signature_hash.clone());
```
