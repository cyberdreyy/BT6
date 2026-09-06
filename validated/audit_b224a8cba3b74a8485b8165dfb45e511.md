Confirmed: the changelog entry for `[3.4.0.0.2.0]` states *"When checking tenure change blocks, ensure there are no locally accepted blocks in the tenure, not just globally accepted blocks."* [1](#0-0)  This shows the fix was applied to the v2 chainstate path (`get_last_signed_block`) [2](#0-1) , but `SortitionsView::validate_tenure_change_payload` in `chainstate/v1.rs` still calls `get_last_globally_accepted_block` [3](#0-2) . Since `SortitionStateVersion::from_protocol_version` still routes any signer running below protocol version 2 (`GLOBAL_SIGNER_STATE_ACTIVATION_VERSION = 2`) to `V1` [4](#0-3) , and `SUPPORTED_SIGNER_PROTOCOL_VERSION` is only 2 [5](#0-4) , the v1 path is still live for any signer/network still on protocol version < 2, so this stale check is reachable, not dead code.

### Title
Stale `LocallyAccepted`-blind duplicate-tenure check in chainstate v1 lets a miner slip a second tenure-change block past `check_proposal` - (File: `stacks-signer/src/chainstate/v1.rs`)

### Summary
`SortitionsView::validate_tenure_change_payload` (v1) checks for an existing block in the current tenure using `signer_db.get_last_globally_accepted_block(...)` rather than `get_last_signed_block(...)`. A block that is only `LocallyAccepted` (i.e., this signer has already put its signature on it, but it hasn't yet reached the group/global threshold) is invisible to this query, so a second, competing tenure-start block for the same tenure passes `check_proposal` instead of being rejected with `DuplicateBlockFound`.

### Finding Description
The v1 code path is: [3](#0-2) 

Compare to the v2 code, which was explicitly fixed for this exact gap (see the regression test and changelog note): [2](#0-1) [6](#0-5) 

`check_proposal` in v1 dispatches into `validate_tenure_change_payload` whenever the proposed block carries a tenure-change payload [7](#0-6) . Because the check only queries `get_last_globally_accepted_block`, a block this signer has already signed (`LocallyAccepted`, not yet `GloballyAccepted`) does not block a second tenure-start proposal for the same tenure at `check_proposal` time. This is exactly analogous to the audit report's bug class: the code takes an unconditional branch (treat as "no duplicate, so proceed") without accounting for a relevant intermediate state (`LocallyAccepted`) that should route to the rejecting branch, just as `_swapPTsForTarget` took the "redeem" branch without checking the "redeem restricted" state that should have routed to "swap."

### Impact Explanation
`check_proposal`/`validate_tenure_change_payload` is the pre-validation gate before the block is even submitted to the node and before `handle_block_proposal`'s later pre-commit/sign machinery runs. Accepting the sibling tenure-change proposal here lets it proceed through validation, pre-commit, and toward a second signature attempt in the same tenure — the deliberate second line of defense (`DuplicateBlockFound`) documented as "run only at proposal arrival, never again" is bypassed for this exact scenario on the v1 path [8](#0-7) . This shifts full responsibility for preventing a double-sign entirely onto the pre-commit-time "own-tenure conflict" backstop (`handle_block_pre_commit`), rather than being caught early as intended and as it is on v2. That backstop only fires under narrower conditions (fresh vs. stale signed conflicts, per `conflict_still_blocks`/`get_signed_conflicts`), so relying on it alone for a case v2 already explicitly hardened against increases risk of a signer being coaxed into pre-committing to (and, if the backstop's stale-window logic is hit, potentially signing) a second sibling tenure-start block in the same tenure — an equivocation/double-sign risk, which maps to the Critical category ("a signer signing an invalid, non-canonical, or conflicting block").

### Likelihood Explanation
Reachable by any single miner (one-slot) simply re-proposing/crafting a second tenure-change block for a tenure in which this signer already holds a local signature, requiring no majority or other signers' keys — but only against signers still running on `SortitionStateVersion::V1`, i.e., signers below `GLOBAL_SIGNER_STATE_ACTIVATION_VERSION` (protocol version < 2). Given `SUPPORTED_SIGNER_PROTOCOL_VERSION` in this codebase is 2, V1 remains a supported, live code path for older/mixed-version signers on the network rather than dead legacy code, and there is no test in `chainstate/tests/v1.rs` mirroring the v2 regression test that guards this exact scenario, suggesting it was not backported.

### Recommendation
Change `SortitionsView::validate_tenure_change_payload` in `stacks-signer/src/chainstate/v1.rs` to use `signer_db.get_last_signed_block(&block.header.consensus_hash)` (matching v2's fix) instead of `get_last_globally_accepted_block`, so a `LocallyAccepted` block in the tenure also triggers `RejectReason::DuplicateBlockFound`. Add a regression test analogous to `check_tenure_change_rejects_when_locally_accepted_block_exists` in `chainstate/tests/v2.rs`, ported to `chainstate/tests/v1.rs`.

### Proof of Concept
1. Signer runs with protocol version < `GLOBAL_SIGNER_STATE_ACTIVATION_VERSION` (2), so `SortitionState::V1` is used [4](#0-3) .
2. A tenure-start block `B1` for tenure `T` is proposed, validated, and the signer signs it (`mark_locally_accepted`), but it does not reach the group threshold, so it stays `LocallyAccepted` (not `GloballyAccepted`).
3. The same or a colluding miner proposes a second tenure-start block `B2` for the same tenure `T` (e.g., after a stalled/slow proposal), with a `TenureChangePayload` whose `prev_tenure_consensus_hash` still matches.
4. `SortitionsView::check_proposal` → `validate_tenure_change_payload` calls `signer_db.get_last_globally_accepted_block(&T)`, which returns `None` because `B1` is only `LocallyAccepted` [3](#0-2) .
5. No `DuplicateBlockFound` rejection is raised; `B2` proceeds to submission/pre-commit, where it depends entirely on the separate pre-commit-time conflict guard to be stopped — a guard that v2's fix explicitly made unnecessary for this exact scenario by closing the gap earlier.

### Citations

**File:** stacks-signer/CHANGELOG.md (L43-48)
```markdown
### Fixed

* Fix duplicated binary name when running `stacks-signer --version` cli command
* Fixed an issue in the signer where it would return early if it detected a message from an unrecognized signer.
* Fixed flakiness in `check_capitulate_miner_view` test.
* When checking tenure change blocks, ensure there are no locally accepted blocks in the tenure, not just globally accepted blocks.
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

**File:** stacks-signer/src/chainstate/v1.rs (L454-465)
```rust
        Ok(())
    }

    /// in tenure changes, we need to check:
    /// (1) if the tenure change confirms the expected parent block (i.e.,
    /// the last globally accepted block in the parent tenure)
    /// (2) if the parent tenure was a valid choice
    fn validate_tenure_change_payload(
        &self,
        proposed_by: &ProposedBy,
        tenure_change: &TenureChangePayload,
        block: &NakamotoBlock,
```

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

**File:** stacks-signer/src/chainstate/mod.rs (L532-539)
```rust
impl SortitionStateVersion {
    /// Convert the protocol version to a sortition state version
    pub fn from_protocol_version(version: u64) -> Self {
        if version < GLOBAL_SIGNER_STATE_ACTIVATION_VERSION {
            Self::V1
        } else {
            Self::V2
        }
```

**File:** stacks-signer/src/v0/signer_state.rs (L50-53)
```rust
/// This is the latest supported protocol version for this signer binary
pub static SUPPORTED_SIGNER_PROTOCOL_VERSION: u64 = 2;
/// The version at which global signer state activates
pub static GLOBAL_SIGNER_STATE_ACTIVATION_VERSION: u64 = 2;
```

**File:** stacks-signer/src/chainstate/tests/v2.rs (L746-756)
```rust
}

/// Test that a tenure change proposal is rejected when a locally-accepted
/// (but not globally-accepted) block already exists in the same tenure.
///
/// This is a regression test: previously, the check used
/// `get_last_globally_accepted_block`, which would miss blocks in
/// `LocallyAccepted` or `PreCommitted` state and incorrectly allow
/// a duplicate tenure change.
#[test]
fn check_tenure_change_rejects_when_locally_accepted_block_exists() {
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
