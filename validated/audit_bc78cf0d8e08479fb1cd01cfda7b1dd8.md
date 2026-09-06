## Finding

`store_and_process_block_signature` in `stacks-signer/src/v0/signer.rs` tallies signature weight and can drive a block to `LocallyAccepted` + `broadcast_signed_block` without ever consulting `block_info.valid`, unlike every other place a threshold decision is made. [1](#0-0) 

### Title
Signer can mark a block it never validated (or already rejected) as accepted and rebroadcast it, because `store_and_process_block_signature` never re-checks `block_info.valid` - (File: stacks-signer/src/v0/signer.rs)

### Summary
Every path in the v0 signer that turns accumulated weight into a state transition re-checks the block's local validity or chain-state before acting — `handle_block_pre_commit` explicitly bails with `if !block_info.valid.unwrap_or(false) { ... return; }` before counting toward the pre-commit threshold, and `handle_block_validate_ok` re-runs `check_block_against_signer_db_state` before moving to `PreCommitted`. `store_and_process_block_signature`, the function that tallies observed *signatures* and can mark a block `LocallyAccepted` and push it to the node, has no equivalent guard. [2](#0-1) [3](#0-2) 

### Finding Description
`store_and_process_block_signature` records the signature, then decides whether to redirect the message into the pre-commit path (which *does* check `valid`) using a single condition: `signer_address != self.stacks_address && !has_committed(block_hash, signer_address)`.

`has_committed` is backed by the `block_pre_commits` table, which `handle_block_pre_commit` populates *unconditionally* — the "Always save the pre-commit" comment at the top of that function, before any validity check runs: [4](#0-3) 

So the sequence:
1. A peer's `BlockPreCommit` arrives for a block this signer has not yet validated (`block_info.valid == None`) or has already locally rejected (`valid == Some(false)`, e.g. via `check_block_against_signer_db_state` failing, analogous to the MetaVesT "already terminated" precondition). `add_block_pre_commit` stores it (`has_committed` now `true`), and the function returns silently at the `!block_info.valid.unwrap_or(false)` check.
2. Later, that same peer's `BlockResponse::Accepted` (an actual signature) arrives. `handle_block_signature` → `store_and_process_block_signature` runs. Because `has_committed` is already `true`, the redirect-to-`handle_block_pre_commit` branch is skipped, and execution falls straight through to weight tallying, `mark_locally_accepted(true)`, and `broadcast_signed_block` — with `block_info.valid` never inspected and `check_block_against_signer_db_state` never re-run.

This exactly mirrors the reported bug class: a majority/weight computation (`getMajorityVotingPower` ≈ `total_signature_weight` vs `min_weight`) proceeds without first checking that the underlying object is in a terminated/invalid state, because that check was only performed — and short-circuited — at an earlier, different call site.

### Impact Explanation
This lets the local signer's own bookkeeping (`LocallyAccepted`, `signed_group`) and its `broadcast_signed_block`/`handle_post_block` push to the node proceed for a block the signer itself never validated or explicitly rejected, bypassing the "wait for our own validation" and "re-check chainstate before acting on threshold" invariants that every other threshold-crossing path enforces (see `docs/signer-flows.md` sections 4–6 and the dedicated regression test `signer_waits_for_validation_before_signing`, which only covers the pre-commit/signing path, not this one). [5](#0-4) 

### Likelihood Explanation
This requires no majority of signers to be malicious — it is triggered purely by message *ordering* (a pre-commit observed before validation completes, followed later by the real signature from the same peer), which the flow diagram in the repo explicitly calls out as a real race ("A pre-commit for a block we have not seen proposed yet... has not reached us" / early votes parked and replayed). A single miner controlling proposal timing plus normal network jitter can realistically produce this ordering.

### Recommendation
In `store_and_process_block_signature`, before tallying weight and calling `mark_locally_accepted`, add the same guard used in `handle_block_pre_commit`: bail (or re-run `check_block_against_signer_db_state`) if `!block_info.valid.unwrap_or(false)`, so a block this signer has not validated, or has already locally rejected, cannot be pushed to `LocallyAccepted`/broadcast purely from tallied peer signatures.

### Proof of Concept
1. Signer S receives a `BlockProposal` for block `B`; validation to the node is still pending (`valid == None`).
2. Peer P sends `BlockPreCommit(B)` to S. `handle_block_pre_commit` stores it via `add_block_pre_commit` (now `has_committed(B, P) == true`), then returns early because `valid` is `None` → `unwrap_or(false)` is `false`.
3. S's own validation later completes and the block fails `check_block_against_signer_db_state` (e.g. a conflicting block was already signed) → `mark_locally_rejected`, `valid == Some(false)`.
4. Peer P (with sufficient weight, e.g. the 75%-weight scenario exercised in `test_single_signature_threshold`) sends `BlockResponse::Accepted(B)`. `handle_block_signature` → `store_and_process_block_signature`: `signer_address(P) != self` but `has_committed(B, P) == true`, so the pre-commit redirect is skipped; weight tally proceeds and crosses `min_weight`; `mark_locally_accepted(true)` and `broadcast_signed_block` execute — even though S's own `block_info.valid == Some(false)`. [6](#0-5)

### Citations

**File:** stacks-signer/src/v0/signer.rs (L1274-1281)
```rust
        };
        // Always save the pre-commit - we will need to store signer responses for determining which
        // are misbehaving, offline, etc.
        // commit message is from a valid sender! store it
        self.signer_db
            .add_block_pre_commit(block_hash, stacker_address)
            .unwrap_or_else(|_| panic!("{self}: Failed to save block pre-commit"));

```

**File:** stacks-signer/src/v0/signer.rs (L1316-1331)
```rust
        if block_info.signed_self.is_some() {
            debug!(
                "{self}: Received pre-commit for a block that we have already signed. Doing nothing...",
            );
            return;
        }

        if !block_info.valid.unwrap_or(false) {
            // We received a pre-commit for a block that we have not validated or we have already marked this block as invalid.
            // We should not do anything further as we do not know what our response should be and we do not change our votes on rejected
            // blocks unless we receive a new block proposal for it and the reject reason allows us to reconsider.
            debug!(
                "{self}: Received a pre-commit for a block that we have not determined to be valid: {:?}. Doing nothing...", block_info.valid
            );
            return;
        }
```

**File:** stacks-signer/src/v0/signer.rs (L2442-2538)
```rust
    /// Store the block acceptance signature and check if we have reached a consensus decision on the block because of it. If we have, update the block state accordingly and broadcast the block if accepted.
    fn store_and_process_block_signature(
        &mut self,
        stacks_client: &StacksClient,
        sortition_state: &mut Option<SortitionsView>,
        block_info: &mut BlockInfo,
        signer_address: &StacksAddress,
        signature: &MessageSignature,
    ) {
        let block_hash = &block_info.signer_signature_hash();
        // signature is valid! store it.
        // if this returns false, it means the signature already exists in the DB, so just return.
        if !self
            .signer_db
            .add_block_signature(block_hash, signer_address, signature)
            .unwrap_or_else(|_| panic!("{self}: Failed to save block signature"))
        {
            return;
        }

        // If this isn't our own signature and we haven't seen a pre-commit from this signer yet, try treating it as a pre-commit in case the caller is running an outdated version
        if signer_address != &self.stacks_address && !self.signer_db.has_committed(block_hash, signer_address).inspect_err(|e| warn!("Failed to check if pre-commit message already considered for {signer_address:?} for {block_hash}: {e}")).unwrap_or(false) {
            self.handle_block_pre_commit(stacks_client, sortition_state, signer_address, block_hash);
            return;
        }

        if block_info.signed_group.is_some() {
            // We have already processed this block to the accepted state. Adding more signatures will not change anything so nothing to check.
            return;
        }
        // do we have enough signatures to broadcast?
        // i.e. is the threshold reached?
        let signatures = self
            .signer_db
            .get_block_signatures(block_hash)
            .unwrap_or_else(|_| panic!("{self}: Failed to load block signatures"));

        // put signatures in order by signer address (i.e. reward cycle order)
        let addrs_to_sigs: HashMap<_, _> = signatures
            .into_iter()
            .filter_map(|sig| {
                let Ok(public_key) = Secp256k1PublicKey::recover_to_pubkey_without_validating_low_s(
                    block_hash.bits(),
                    &sig,
                ) else {
                    return None;
                };
                let addr = StacksAddress::p2pkh(self.mainnet, &public_key);
                Some((addr, sig))
            })
            .collect();

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
    }
```

**File:** stacks-node/src/tests/signer/v0/signers_wait_for_validation.rs (L33-56)
```rust
#[test]
#[ignore]
/// Test that signers don't issue signatures until they have validated the block
///
/// This test verifies a race condition where a signer receives enough pre-commits
/// to exceed the 70% threshold before receiving its own block validation response.
/// The signer should NOT issue a signature until it has confirmed the block is valid.
///
/// Test Setup:
/// - Distribute signers across two miners (4 on miner 1, 1 on miner 2)
/// - Signers on different miners use different validation endpoints
///
/// Test Execution:
/// 1. Propose a block to all signers
/// 2. Pause block validation on miner 2 (the single signer)
/// 3. 4 signers on miner 1 issue pre-commits, pushing threshold over 70%
/// 4. The single signer on miner 2 receives all pre-commits but its validation is stalled
/// 5. Verify the single signer does NOT issue a signature until validation completes
/// 6. Resume validation and confirm the block is accepted
///
/// Test Assertion:
/// The signer waits for its own validation before issuing a signature, preventing
/// race conditions where it could sign before discovering the block is invalid.
fn signer_waits_for_validation_before_signing() {
```

**File:** stackslib/src/chainstate/nakamoto/tests/mod.rs (L3846-3873)
```rust
    #[test]
    // Test with 4 signers, but one has 75% weight. Only the whale signs
    // and the block is valid
    fn test_single_signature_threshold() {
        let signers = [
            (Secp256k1PrivateKey::random(), 75),
            (Secp256k1PrivateKey::random(), 10),
            (Secp256k1PrivateKey::random(), 5),
            (Secp256k1PrivateKey::random(), 10),
        ];
        let reward_set = make_reward_set(&signers);

        let mut header = NakamotoBlockHeader::empty();

        // Sign the block with just the whale
        let message = header.signer_signature_hash().0;
        let signer_signature = signers
            .iter()
            .take(1)
            .map(|(s, _)| s.sign(&message).expect("Failed to sign block sighash"))
            .collect::<Vec<_>>();

        header.signer_signature = signer_signature;

        header
            .verify_signer_signatures(&reward_set, StacksEpochId::latest())
            .expect("Failed to verify signatures");
    }
```
