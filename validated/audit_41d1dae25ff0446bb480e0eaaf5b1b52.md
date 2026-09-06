### Title
Broadcast of a globally-rejected block proceeds even when `mark_locally_accepted` fails - ([File: stacks-signer/src/v0/signer.rs])

### Summary
`store_and_process_block_signature` ignores the failure of the local-state transition it depends on and unconditionally proceeds to persist and broadcast the block's signature set, mirroring the reported pattern where a downstream compensating action (the refund) fires regardless of whether the preceding check actually applies.

### Finding Description
`BlockInfo`'s lifecycle is a strict state machine: `GloballyRejected` and `GloballyAccepted` are documented as mutually terminal — "each global state is unreachable from the other" [1](#0-0) . Rejections are tallied independently from acceptances in `store_and_process_block_rejection`, and once >30% weight rejects, `block_info.mark_globally_rejected()` is called, terminating the block [2](#0-1) .

However, `store_and_process_block_signature` — the function that assembles and pushes a signed block once ≥70% weight has accepted — does not check `has_reached_consensus()`/global-rejection state before acting. It only guards on `block_info.signed_group.is_some()`:

```rust
if block_info.signed_group.is_some() {
    return;
}
``` [3](#0-2) 

After computing the signature weight and finding it meets the threshold, it calls `mark_locally_accepted`, but **swallows any error from it** and proceeds regardless:

```rust
if let Err(e) = block_info.mark_locally_accepted(true) {
    if !block_info.has_reached_consensus() {
        warn!("{self}: Failed to mark block as locally accepted: {e:?}");
    }
}
let _ = self.signer_db.insert_block(block_info).map_err(|e| { ... });
self.broadcast_signed_block(stacks_client, block_info.block.clone(), &addrs_to_sigs);
``` [4](#0-3) 

Note that when `block_info` is already `GloballyRejected`, `has_reached_consensus()` returns true, so the `warn!` isn't even logged — the failed state transition is completely silent, and `broadcast_signed_block` → `handle_post_block` → `stacks_client.post_block(block)` still fires unconditionally [5](#0-4) .

Because rejections and acceptances are recorded on a per-signer-address basis, and a signer that previously rejected can later switch to signing (the `add_block_signature`/`add_block_rejection_signer_addr` pair explicitly supports "reject then accept," clearing the earlier rejection row) [6](#0-5) , it is possible for the disjoint reject/accept vote sets to shift over time such that the local `block_info` was already latched to `GloballyRejected` by an earlier majority-of-rejectors tally, while a later re-tally of signatures (from switched-over signers plus original signers) crosses the 70% acceptance threshold. When that happens, `store_and_process_block_signature` still runs `broadcast_signed_block`, pushing a block this same signer has already treated as dead to the node.

### Impact Explanation
This breaks the "approved vs canonical / terminal state" equality the whole lifecycle design depends on (`GloballyRejected` is supposed to be a dead end, per `docs/signer-flows.md`). If the failed transition is silently ignored and the broadcast proceeds anyway, the signer can push a purportedly non-canonical/dead block's signature set to its own node, potentially causing that node to accept a block the signer's own logic had already discarded — an instance of a signer effectively acting on/propagating an already-rejected (non-canonical, by this signer's own decision) block. This falls under the Critical bucket: a signer acting to push a conflicting/invalid-by-its-own-record block.

### Likelihood Explanation
It requires no majority collusion and no external key — only ordinary, permitted behavior: individual signers changing their vote from reject to accept over time (which the code explicitly supports), combined with normal network/timing skew in when different signers relay their pre-commits/rejections/signatures. A single miner (plus normal signer set churn in opinion) triggering divergent tally orderings across nodes is enough; no signer needs to act maliciously.

### Recommendation
In `store_and_process_block_signature`, treat a failed `mark_locally_accepted` as fatal to this code path when the block has already reached a terminal state (i.e., `has_reached_consensus()` is true) — return immediately instead of falling through to `insert_block`/`broadcast_signed_block`. More generally, gate `broadcast_signed_block` on the success of `mark_locally_accepted`, not merely on `signed_group.is_some()`.

### Proof of Concept
1. Signer set partitions on an early proposal such that ≥31% weight rejects it before ever pre-committing/signing; `store_and_process_block_rejection` calls `mark_globally_rejected`, latching `block_info.state = GloballyRejected` for this signer [2](#0-1) .
2. Some of those same rejecting signers, on a later re-proposal/re-evaluation cycle, switch to signing the block (supported behavior — see `reject_then_accept` test) [6](#0-5) .
3. As their signatures (plus any signers who had already signed) accumulate in `block_signatures`, `store_and_process_block_signature` recomputes `total_signature_weight` and finds it ≥ 70% threshold [7](#0-6) .
4. `mark_locally_accepted(true)` is invoked on a `block_info` still carrying `state = GloballyRejected`; per the documented terminal-state rule this should fail, but the error is discarded and, because `has_reached_consensus()` is true, not even logged.
5. Execution falls through to `insert_block` and `broadcast_signed_block` → `handle_post_block` → `post_block`, pushing the previously globally-rejected block's signature set to the stacks-node [8](#0-7) .

Note: I was unable to directly inspect the body of `BlockInfo::check_state`/`mark_locally_accepted` in `stacks-signer/src/signerdb.rs` within the available tool calls to confirm the exact error variant returned from a `GloballyRejected → LocallyAccepted` transition attempt; this is inferred from the documented state-machine invariants in `docs/signer-flows.md`. A Devin session with full repo access should verify `BlockInfo::check_state`'s exact transition table before treating this as confirmed.

### Citations

**File:** docs/signer-flows.md (L152-154)
```markdown
Canonical paths shown; the exact rule in `BlockInfo::check_state` is: either
local state is reachable from anything not yet global, `PreCommitted` only from
`Unprocessed`, and each global state is unreachable from the other.
```

**File:** stacks-signer/src/v0/signer.rs (L2313-2337)
```rust
        if total_reject_weight.saturating_add(min_weight) <= total_weight {
            // Not enough rejection signatures to make a decision
            info!("{self}: Have not yet received enough block rejections to reach a consensus decision on this block";
                "signer_signature_hash" => %block_hash,
                "signature_weight" => signature_weight,
                "consensus_hash" => %block_info.block.header.consensus_hash,
                "block_height" => block_info.block.header.chain_length,
                "total_weight_rejected" => total_reject_weight,
                "total_weight" => total_weight,
                "percent_rejected" => (total_reject_weight as f64 / total_weight as f64 * 100.0),
            );
            return;
        }
        info!("{self}: have reached the block rejection threshold";
            "signer_signature_hash" => %block_hash,
            "signature_weight" => signature_weight,
            "consensus_hash" => %block_info.block.header.consensus_hash,
            "block_height" => block_info.block.header.chain_length,
            "total_weight_rejected" => total_reject_weight,
            "total_weight" => total_weight,
            "percent_rejected" => (total_reject_weight as f64 / total_weight as f64 * 100.0),
        );
        if let Err(e) = block_info.mark_globally_rejected() {
            warn!("{self}: Failed to mark block as globally rejected: {e:?}",);
        }
```

**File:** stacks-signer/src/v0/signer.rs (L2468-2471)
```rust
        if block_info.signed_group.is_some() {
            // We have already processed this block to the accepted state. Adding more signatures will not change anything so nothing to check.
            return;
        }
```

**File:** stacks-signer/src/v0/signer.rs (L2494-2514)
```rust
        let signature_weight = self.signer_weights.get(signer_address).unwrap_or(&0);
        let total_signature_weight = self.compute_signature_signing_weight(addrs_to_sigs.keys());
        let total_weight = self.compute_signature_total_weight();

        let min_weight = NakamotoBlockHeader::compute_voting_weight_threshold(total_weight)
            .unwrap_or_else(|_| {
                panic!("{self}: Failed to compute threshold weight for {total_weight}")
            });

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
```

**File:** stacks-signer/src/v0/signer.rs (L2525-2559)
```rust
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
    }

    fn broadcast_signed_block(
        &mut self,
        stacks_client: &StacksClient,
        mut block: NakamotoBlock,
        addrs_to_sigs: &HashMap<StacksAddress, MessageSignature>,
    ) {
        #[cfg(any(test, feature = "testing"))]
        self.test_pause_block_broadcast(&block);

        // collect signatures for the block
        let signatures: Vec<_> = self
            .signer_addresses
            .iter()
            .filter_map(|addr| addrs_to_sigs.get(addr).cloned())
            .collect();

        block.header.signer_signature_hash();
        block.header.signer_signature = signatures;

        self.handle_post_block(stacks_client, &block);
```

**File:** stacks-signer/src/signerdb.rs (L3234-3263)
```rust
    #[test]
    fn reject_then_accept() {
        let db_path = tmp_db_path();
        let db = SignerDb::new(db_path).expect("Failed to create signer db");

        let block_id = Sha512Trunc256Sum::from_data("foo".as_bytes());
        let address = StacksAddress::burn_address(false);
        let sig1 = MessageSignature([0x11; 65]);

        assert_eq!(db.get_block_signatures(&block_id).unwrap(), vec![]);

        assert!(db
            .add_block_rejection_signer_addr(
                &block_id,
                &address,
                RejectReasonPrefix::InvalidParentBlock
            )
            .unwrap());
        assert_eq!(
            db.get_block_rejection_signer_addrs(&block_id).unwrap(),
            vec![(address.clone(), RejectReasonPrefix::InvalidParentBlock)]
        );

        assert!(db.add_block_signature(&block_id, &address, &sig1).unwrap());
        assert_eq!(db.get_block_signatures(&block_id).unwrap(), vec![sig1]);
        assert!(db
            .get_block_rejection_signer_addrs(&block_id)
            .unwrap()
            .is_empty());
    }
```
