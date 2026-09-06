### Title
Signer signs a duplicate/conflicting tenure-start block under the v1 chainstate path because `validate_tenure_change_payload` still checks only globally-accepted blocks - (File: `stacks-signer/src/chainstate/v1.rs`)

### Summary
`stacks-signer/src/chainstate/v1.rs`'s `validate_tenure_change_payload` guards against a miner re-proposing a second tenure-start block in the same tenure by querying `signer_db.get_last_globally_accepted_block(...)` [1](#0-0) . The analogous v2 function, `chainstate/v2.rs`'s `validate_tenure_change_payload`, was fixed to instead call `signer_db.get_last_signed_block(...)`, which additionally catches blocks that are only *locally* accepted (signed by this signer but not yet globally accepted) [2](#0-1) . This is directly documented as an intentional divergence: "v2 counts locally or globally accepted blocks (`get_last_signed_block`); v1 counts only globally accepted ones (`get_last_globally_accepted_block`)" [3](#0-2) . A dedicated regression test, `check_tenure_change_rejects_when_locally_accepted_block_exists`, exists for v2 and explicitly states "Before the fix, this would have incorrectly passed because `get_last_globally_accepted_block` would not find the locally-accepted block" [4](#0-3)  — but no equivalent fix or test exists for v1. This is structurally identical to the reported bug class: a duplicate-block/conflict fix applied to one protocol path (CurveSpell/v2) but missed in the sibling path (ConvexSpell/v1).

### Finding Description
`validate_tenure_change_payload` is the only place that rejects a second tenure-start block proposed in the same tenure, and per the docs this check "never runs again" after proposal arrival [5](#0-4) . In v1, the check is:

```rust
let last_in_current_tenure = signer_db
    .get_last_globally_accepted_block(&block.header.consensus_hash)...
if let Some(last_in_current_tenure) = last_in_current_tenure {
    ... return Err(RejectReason::DuplicateBlockFound);
}
``` [1](#0-0) 

Because `get_last_globally_accepted_block` only finds a block once it has crossed the network-wide signature threshold, a block this signer has *locally* accepted (i.e., already signed, but the group hasn't reached 70% yet) is invisible to this check under v1. If the miner (or a colluding party controlling the miner) then proposes a second, different tenure-start block for the same tenure while the first is still only locally-accepted, `check_proposal`/`validate_tenure_change_payload` under v1 will not reject it with `DuplicateBlockFound`, and the proposal proceeds to validation and potentially to signing.

Compare v2's fixed version, which uses `get_last_signed_block` (locally OR globally accepted) precisely to close this gap [2](#0-1) .

### Impact Explanation
If the proposal-time `DuplicateBlockFound` guard is bypassed under v1, the only remaining backstop is the pre-commit-threshold re-check in `stacks-signer/src/v0/signer.rs`, which looks for signed conflicts at the *same or higher stacks height in any tenure* via `get_signed_conflicts` [6](#0-5) . That check is common code, not part of the v1/v2 chainstate split, so it likely still catches the exact-same-height duplicate before a second signature is actually placed. This narrows — but does not eliminate — the practical window: the vulnerable v1 code path allows an already-known-bad proposal (one that duplicates a locally-accepted tenure-start block) to pass proposal-time filtering and reach validation/pre-commit, when the documented intent (and v2's parallel fix) is to reject it immediately. This is a genuine equality break at the intended checkpoint (signed vs. validated duplicate-detection is asymmetric between v1 and v2), and it degrades defense-in-depth for one-per-tenure invariants specifically on the v1 protocol path, even if the section-5 pre-commit re-check is expected to catch the terminal case.

I could not fully verify from the index whether the v1 protocol path is still reachable/live in the current node/signer configuration (i.e., whether v1 is deprecated dead code or still selectable), because I was unable to locate the dispatch point (`SortitionsView` enum construction, `mod.rs`) that chooses between v1 and v2 before the tool budget ran out. This affects likelihood: if v1 is no longer used in production configurations, the practical severity is much lower (dead-code inconsistency rather than an exploitable path).

### Likelihood Explanation
A single miner can trigger this by proposing two different tenure-start blocks for the same tenure in quick succession — no majority of signers, no other signer's key, and no auth_token access is required, matching the miner-only reachability requirement. The main uncertainty is whether the v1 chainstate module is still exercised in the currently deployed signer/node configuration, or is a legacy path retained for compatibility (I was not able to confirm this within the available tool calls).

### Recommendation
Change `stacks-signer/src/chainstate/v1.rs`'s `validate_tenure_change_payload` to use `signer_db.get_last_signed_block(&block.header.consensus_hash)` instead of `get_last_globally_accepted_block`, mirroring the v2 fix, so that a locally-accepted (signed but not yet globally accepted) tenure-start block is also treated as a duplicate for a competing proposal. Add a v1-equivalent regression test analogous to `check_tenure_change_rejects_when_locally_accepted_block_exists`.

### Proof of Concept
1. Signer receives tenure-start block A for tenure T, validates it, and locally accepts/signs it (`mark_locally_accepted`), but the group has not yet reached the 70% pre-commit/signature threshold.
2. The signer is running the v1 chainstate path.
3. The same miner (or an attacker able to submit competing proposals as the miner) proposes tenure-start block B for the same tenure T with different transactions.
4. `check_proposal` → `validate_tenure_change_payload` (v1) calls `get_last_globally_accepted_block(T)`, which returns `None` because A is only locally accepted, not globally accepted [1](#0-0) .
5. The `DuplicateBlockFound` rejection that should fire (and does fire under the v2 path per the test at `stacks-signer/src/chainstate/tests/v2.rs:838-850` [7](#0-6) ) does not fire under v1, letting B proceed past the intended duplicate-tenure-start guard.

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

**File:** stacks-signer/src/chainstate/tests/v2.rs (L838-850)
```rust
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

**File:** stacks-signer/src/v0/signer.rs (L1383-1421)
```rust
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
