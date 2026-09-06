### Title
V1-protocol `validate_tenure_change_payload` checks only globally-accepted blocks, letting a second tenure-start block be signed for a tenure with a locally-accepted first block - ([File: stacks-signer/src/chainstate/v1.rs])

### Summary
`v1::SortitionsView::validate_tenure_change_payload` guards against a duplicate tenure-start by querying `signer_db.get_last_globally_accepted_block(...)`, whereas the v2 path (`v2::GlobalStateView::validate_tenure_change_payload`) uses the strictly broader `signer_db.get_last_signed_block(...)` (locally OR globally accepted). [1](#0-0) [2](#0-1)  This means a v1-protocol signer that has only *locally* accepted (signed, not yet globally accepted) the first tenure-start block of a tenure will not flag a second, freshly-hashed tenure-start block for the same tenure as `DuplicateBlockFound`, allowing it to sign two conflicting tenure-start blocks for one tenure.

### Finding Description
The intended safety equality is: "for any tenure, at most one tenure-start block may be signed by this signer." That equality is implemented via `validate_tenure_change_payload`'s duplicate check, but the v1 implementation only consults `get_last_globally_accepted_block`, a strictly narrower predicate than `get_last_signed_block` used in v2. [1](#0-0)  The v2 code comment explicitly documents the rationale for including locally-accepted blocks: "Only blocks we have signed (locally or globally accepted) count here: a block we have merely pre-committed to carries no signature from us..." [3](#0-2)  — the v1 path never received the equivalent fix.

Exploit flow, given the attacker legitimately wins a sortition (single miner slot, own BTC):
1. Attacker proposes tenure-change block B1 for tenure T. Signer signs it and it becomes `LocallyAccepted` in `SignerDb` but has not yet reached global acceptance (e.g., network is slow, or attacker withholds/delays broadcasting the signature aggregate to the node).
2. Attacker crafts B2, a second, distinct tenure-change block for the same tenure T (different transaction/timestamp/etc.), giving it a fresh `signer_signature_hash`.
3. Because B2's hash is new, `should_reevaluate_block`'s `KNOWN` branch is skipped and B2 undergoes fresh evaluation via `handle_block_proposal` → `check_block_against_state` → v1 `check_proposal` → `validate_tenure_change_payload`.
4. Inside `validate_tenure_change_payload`, `get_last_globally_accepted_block(&block.header.consensus_hash)` returns `None` (B1 is only locally accepted, not global), so the duplicate check is skipped and B2 passes validation and gets signed.
5. The signer has now signed two distinct tenure-start blocks (B1 and B2) for the same tenure T — a chain-safety violation (conflicting/duplicate blocks signed by the same signer for one tenure).

This is not blocked elsewhere: the `RejectReason::DuplicateBlockFound` non-re-evaluable design in `should_reevaluate_reject_reason` only prevents re-evaluating the *same* proposal hash; it does nothing for a fresh hash, and no other check in v1's `check_proposal` re-derives "has this signer already signed a tenure-start block in this tenure" using local acceptance.

### Impact Explanation
This breaks the uniqueness/chain-safety property that a signer must not sign two conflicting blocks for the same tenure slot. A shared exploit across the whole v1-protocol signer set finalizes a fork/ambiguity at the tenure-start point, matching the Critical category ("a signer signing an invalid, non-canonical, or conflicting block (chain safety)"). It is repeatable per-tenure whenever the attacker can win a sortition and time B1/B2 around the local-vs-global acceptance gap.

### Likelihood Explanation
Preconditions: v1-protocol signer (pre-`GLOBAL_SIGNER_STATE_ACTIVATION_VERSION`), attacker must win a sortition slot (one miner slot, own BTC — consistent with the assumed attacker capability), and must get B1 locally accepted by the target signer without it reaching global acceptance before B2 arrives (achievable via normal network delay/timing, not requiring any privileged capability). Attacker cost is a single miner slot plus gossiping two block proposals — no majority of signers, no auth_token, no local access needed. This is feasible and repeatable across any tenure so long as the signer runs the v1 code path.

### Recommendation
Change `v1::SortitionsView::validate_tenure_change_payload` to use `signer_db.get_last_signed_block(&block.header.consensus_hash)` instead of `get_last_globally_accepted_block`, matching the v2 semantics and its documented rationale (count locally-accepted signatures, not just pre-commits, but exclude bare pre-commits).

### Proof of Concept
Rust test plan (in `stacks-signer/src/chainstate/tests`, mirroring `v2.rs` tests but exercising `v1::SortitionsView::check_proposal`):
1. Set up a `SignerDb` and `v1::SortitionsView` for tenure T with a valid sortition/miner.
2. Construct tenure-start block B1 for T, insert it into `SignerDb` in state `LocallyAccepted` (signed by this signer) via the same mechanism used in v2 tests, but do NOT mark it `GloballyAccepted`.
3. Construct a second, distinct tenure-start block B2 for tenure T (different tx/hash) with the same parent tenure and otherwise-valid tenure-change payload.
4. Call `v1_view.check_proposal(&client, &mut signer_db, &b2, false, ReplayTransactionSet::None)`.
5. Assert current (buggy) behavior: `check_proposal` returns `Ok(())` (i.e., no `DuplicateBlockFound`), proving the v1 path fails to reject B2.
6. After applying the fix (swap to `get_last_signed_block`), re-run the same test and assert `check_proposal` returns `Err(RejectReason::DuplicateBlockFound)`, matching v2's existing test coverage in `stacks-signer/src/chainstate/tests/v2.rs`. [4](#0-3)

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

**File:** stacks-signer/src/chainstate/tests/v2.rs (L1-1)
```rust
// Copyright (C) 2024-2026 Stacks Open Internet Foundation
```
