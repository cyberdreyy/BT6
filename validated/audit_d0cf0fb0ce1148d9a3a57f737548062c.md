### Title
`validate_tenure_change_payload` in v1 chainstate misses locally-signed tenure-start blocks, allowing a second tenure-start block to be signed in the same tenure - ([File: stacks-signer/src/chainstate/v1.rs])

### Summary
`SortitionStateV1::validate_tenure_change_payload` guards against signing two tenure-start blocks in the same tenure by calling `signer_db.get_last_globally_accepted_block(&block.header.consensus_hash)` [1](#0-0)  whereas the v2 implementation of the same check uses `signer_db.get_last_signed_block(&block.header.consensus_hash)`, which also covers blocks the signer has only locally accepted/signed [2](#0-1) . A signer running in v1 mode (i.e. its negotiated protocol version is below `GLOBAL_SIGNER_STATE_ACTIVATION_VERSION`, selected via `SortitionStateVersion::from_protocol_version`) will therefore fail to detect that it already signed a first tenure-start block for a tenure if that block never reached global acceptance, and can be induced to sign a second, sibling tenure-start block for the same tenure.

### Finding Description
The intended invariant is: a signer signs at most one tenure-start block per tenure (`UNIQUENESS`). This is enforced at the end of `validate_tenure_change_payload` by checking whether *any block the signer has already signed* exists for `block.header.consensus_hash`.

- In v2, this uses `get_last_signed_block`, whose doc comment explicitly states it returns the last block the signer signed "locally accepted or globally accepted" (measured by `signed_self`/`signed_group`, not `approved_time`, precisely to include self-signed-but-not-yet-globally-accepted blocks) — see `SortitionData::get_tenure_last_block_info` doc [3](#0-2) .
- In v1, the same purpose check instead calls `get_last_globally_accepted_block`, which by name and by contrast with `get_last_signed_block` only returns a block once the network as a whole (not just this signer) has accepted it [4](#0-3) .

Root cause: the v1 branch's uniqueness check uses a narrower predicate (`GloballyAccepted`) than what actually represents "have I already signed a tenure-start block here" (`LocallyAccepted` or `GloballyAccepted`, i.e. `get_last_signed_block`). Version selection is per-signer, driven purely by that signer's own negotiated protocol version (`SortitionStateVersion::from_protocol_version`, threshold `GLOBAL_SIGNER_STATE_ACTIVATION_VERSION`) [5](#0-4) , so any individual signer still running old/v1 logic (e.g. mid-upgrade, or simply not yet bumped) is independently exposed — this does not require compromising a majority of signers.

Exploit flow (single miner-slot attacker, no privileged access):
1. Attacker wins a sortition and gossips a tenure-start `BlockProposal` A for tenure `T`. The v1 signer runs its normal validation, passes `check_tenure_change_confirms_parent` / `check_parent_tenure_choice` (these check the *parent* tenure's last block/choice, not duplicates within `T`), reaches the final check where `get_last_globally_accepted_block(T)` is `None` (no prior block exists), and signs A. A becomes `LocallyAccepted` in `signer_db`, but the attacker/network conditions (e.g. controlling the timing of the rest of the signer set) keep A from reaching `GloballyAccepted`.
2. Attacker crafts a second, sibling tenure-start `BlockProposal` B for the same tenure `T` (same `prev_tenure_consensus_hash`, i.e. same `parent_tenure_id`, so `check_tenure_change_confirms_parent`/`check_parent_tenure_choice` still pass) and gossips it.
3. The v1 signer re-enters `validate_tenure_change_payload`, calls `get_last_globally_accepted_block(T)` again — still `None` because A never became globally accepted — and wrongly returns `Ok(())` instead of `Err(RejectReason::DuplicateBlockFound)`, allowing B to be signed too.

Existing guards do not catch this: `check_tenure_change_confirms_parent` and `check_parent_tenure_choice` validate the *parent* tenure relationship, not intra-tenure duplication; the intra-tenure duplicate check is exactly the `get_last_globally_accepted_block`/`get_last_signed_block` call being analyzed, and only the v1 branch is narrowed.

### Impact Explanation
This breaks the UNIQUENESS safety property for tenure-start blocks in chain safety: a single v1-mode signer can be made to sign two conflicting tenure-start blocks (A and B) for the same tenure. If enough such signers exist (or if this occurs on the signer whose weight matters for threshold), this can contribute signatures toward two distinct tenure-start blocks that the node would otherwise consider mutually exclusive — a conflicting-block signature, which is categorized Critical (chain safety violation, uniqueness of tenure-start signing broken).

### Likelihood Explanation
Preconditions are realistic: any signer operating below `GLOBAL_SIGNER_STATE_ACTIVATION_VERSION` (v1 logic) hits this path unconditionally whenever it locally-but-not-globally accepts a tenure-start block, which is a normal transient state (waiting for the rest of the signer set to catch up). The attacker only needs one miner slot (to produce the two competing tenure-start block proposals for the same tenure/parent) and the ability to gossip both proposals — no majority-signer collusion, no node/local access, and no auth token are required. The attack is repeatable per tenure the attacker wins and per v1-mode signer in the set.

### Recommendation
Change `SortitionStateV1::validate_tenure_change_payload` to use `signer_db.get_last_signed_block(&block.header.consensus_hash)` (matching v2's semantics) instead of `get_last_globally_accepted_block`, so that a locally-accepted-and-signed tenure-start block also blocks a sibling tenure-start proposal for the same tenure.

### Proof of Concept
Rust test plan (in `stacks-signer/src/chainstate/tests/v1.rs`):
1. Construct a `SortitionStateV1` / `SignerDb` fixture for tenure `T` with a valid parent tenure choice (so `check_parent_tenure_choice` and `check_tenure_change_confirms_parent` pass).
2. Insert a `BlockInfo` for a tenure-start block A of tenure `T` into `signer_db` with state `LocallyAccepted` (i.e. `signed_self` set, `signed_group`/global acceptance not set) — do NOT mark it `GloballyAccepted`.
3. Assert baseline: `signer_db.get_last_signed_block(&T).unwrap().is_some()` is `true` (A is a signed block) while `signer_db.get_last_globally_accepted_block(&T).unwrap()` is `None` — this establishes the divergence.
4. Build a sibling tenure-start block B for tenure `T` with the same `prev_tenure_consensus_hash`/parent as A, and a matching `TenureChangePayload`.
5. Call `state.validate_tenure_change_payload(&proposed_by, &tenure_change, &b, &mut signer_db, &client)`.
6. Assert the bug: the call returns `Ok(())` (bypassing the uniqueness guard) instead of the expected `Err(RejectReason::DuplicateBlockFound)`.
7. As a regression companion, add the equivalent v2 test showing `get_last_signed_block` correctly returns `Some(A)` and v2's `validate_tenure_change_payload` correctly returns `Err(RejectReason::DuplicateBlockFound)` for the same setup, to demonstrate the asymmetry and validate the fix once v1 is changed to use `get_last_signed_block`.

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

**File:** stacks-signer/src/chainstate/v2.rs (L340-348)
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
```

**File:** stacks-signer/src/chainstate/mod.rs (L317-330)
```rust
    /// Get the last signed block from the given tenure if it has not timed out.
    /// Even globally accepted blocks are allowed to be timed out, as that
    /// triggers the signer to consult the Stacks node for the latest globally
    /// accepted block. This is needed to handle Bitcoin reorgs correctly.
    ///
    /// The timeout window is measured from the last time a signature actually covered the
    /// block: our own (`signed_self`) or the observed group/global acceptance
    /// (`signed_group`), whichever is later, matching how `get_signed_conflicts` measures
    /// endorsement freshness. `approved_time` is deliberately not used: it is stamped at
    /// pre-commit, which carries no signature, so it would close the window early. This also
    /// means a globally accepted block we never signed ourselves gets a full window from the
    /// time its acceptance was observed, rather than timing out instantly for lack of a
    /// timestamp.
    pub fn get_tenure_last_block_info(
```

**File:** stacks-signer/src/chainstate/mod.rs (L532-540)
```rust
impl SortitionStateVersion {
    /// Convert the protocol version to a sortition state version
    pub fn from_protocol_version(version: u64) -> Self {
        if version < GLOBAL_SIGNER_STATE_ACTIVATION_VERSION {
            Self::V1
        } else {
            Self::V2
        }
    }
```
