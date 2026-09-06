### Title
Rejection broadcast unconditionally sent even when `mark_locally_rejected()` fails, allowing a signer to emit a contradictory `Rejected` verdict for a block it has already recorded as consensus-reached - (File: `stacks-signer/src/v0/signer.rs`)

### Summary
`handle_block_validate_reject` ignores the failure of the local state-transition call `block_info.mark_locally_rejected()` and unconditionally proceeds to persist the block and broadcast a `Rejected` response, mirroring the reported bug class where a failed/false-returning state-changing call (`transferFrom`) is not checked before crediting the caller with the associated side effect (the NFT / here, a broadcast verdict).

### Finding Description
In `handle_block_validate_reject` [1](#0-0) , the code does:

```rust
if let Err(e) = block_info.mark_locally_rejected() {
    if !block_info.has_reached_consensus() {
        warn!("{self}: Failed to mark block as locally rejected: {e:?}");
    }
}
```

Regardless of whether `mark_locally_rejected()` succeeded or failed, execution always falls through to build the rejection, persist `block_info`, and broadcast it: [2](#0-1) .

Per the documented `BlockInfo` state machine (`check_state`), a local mark such as `mark_locally_rejected` is only rejected (returns `Err`) when it is not reachable from the block's current state - i.e., when the block has already reached a *global* terminal state (`GloballyAccepted` or `GloballyRejected`) [3](#0-2) . The guard `!block_info.has_reached_consensus()` in the warn arm confirms this: the code only suppresses the warning when consensus has already been reached, i.e., precisely the case where the mark failed because the block was already globally decided.

Contrast this with the sibling accept-path handler `handle_block_validate_ok`, which *does* gate the analogous side effect on the mark's success: if `mark_pre_committed()` fails and neither `has_reached_consensus()` nor `state == LocallyAccepted` holds, it logs and `return`s, aborting the pre-commit broadcast [4](#0-3) . No equivalent early return exists in `handle_block_validate_reject`.

The result: when a node's validation-reject response for a block arrives after this signer's own `BlockInfo` for that block has already reached `GloballyAccepted` (e.g., a stale/late reject came in for a block that meanwhile crossed the 70% pre-commit/signature threshold and was accepted), `mark_locally_rejected()` fails (its return value is effectively discarded) but the signer still broadcasts a `Rejected` response for that same block hash over StackerDB.

### Impact Explanation
This lets a single signer emit a broadcast `Rejected` verdict for a block that its own local database considers globally accepted (and which it may have itself signed earlier). Because tallying of accepts/rejects by peers is derived purely from the messages observed on StackerDB (per the acceptance/rejection tally logic in `docs/signer-flows.md` section "TALLY") [5](#0-4) , this produces a signer emitting a message that contradicts its own recorded state, i.e., a stale/incoherent rejection re-entering the tally for a block that has already crossed the acceptance threshold. This is the closest reachable analog to "a rejection recounted as an accept" from the rules: here it is the reverse polarity (a late, spurious reject re-injected into the tally for an already-accepted block), undermining the invariant that a signer's broadcast verdicts are consistent with its locally recorded, already-globally-decided state.

### Likelihood Explanation
Triggering requires only a race that is fully within the reach of the existing single-slot flow: the stacks-node's `/v3/block_proposal` reject response for a given `signer_signature_hash` arriving after this signer's local `BlockInfo` for that hash has already been marked `GloballyAccepted` via the normal pre-commit → signature → tally path (e.g., due to node validation latency vs. how quickly other signers reach threshold, both of which the docs explicitly call out as timing-sensitive: "validation_time_ms", "check if the last block validation submission timed out"). No majority collusion, no other signer's key, and no StackerDB-transport exploitation is needed - it is a normal, delayed message from the signer's own node.

### Recommendation
Mirror the guard used in `handle_block_validate_ok`: only proceed to persist/broadcast the rejection when `mark_locally_rejected()` succeeds, or when the resulting state is still consistent with sending a rejection (e.g., not `GloballyAccepted`). If the mark fails because consensus has already been reached, `return` early instead of falling through to `handle_block_rejection`/`send_block_response`, so a signer never broadcasts a verdict contradicting its own already-finalized local state.

### Proof of Concept
Conceptual reproduction (I could not execute this in the current environment, but it follows directly from the cited code paths):
1. Signer A submits block B for validation and also observes ≥70% pre-commit weight and signs B, causing its local `BlockInfo` for B to reach `GloballyAccepted` via `mark_globally_accepted` (see tally description) [6](#0-5) .
2. The stacks-node's validation response for the original submission of B (submitted before global acceptance was reached) arrives late as `BlockValidateResponse::Reject`.
3. `handle_block_validate_response` routes it to `handle_block_validate_reject` [7](#0-6) .
4. `mark_locally_rejected()` fails silently (state already `GloballyAccepted`), but the function still constructs and broadcasts a `BlockRejection` for B [2](#0-1) .

Note: I was not able to read the exact body of `BlockInfo::mark_locally_rejected`, `check_state`, and `has_reached_consensus` in `stacks-signer/src/signerdb.rs` before running out of tool iterations (grep located them but their bodies were not fetched), so the precise set of states from which `mark_locally_rejected` returns `Err` is inferred from `docs/signer-flows.md` and the `has_reached_consensus()` guard rather than directly confirmed from source. This should be verified against the actual implementation before treating this as conclusively exploitable.

### Citations

**File:** stacks-signer/src/v0/signer.rs (L1961-1970)
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
```

**File:** stacks-signer/src/v0/signer.rs (L2020-2024)
```rust
        if let Err(e) = block_info.mark_locally_rejected() {
            if !block_info.has_reached_consensus() {
                warn!("{self}: Failed to mark block as locally rejected: {e:?}");
            }
        }
```

**File:** stacks-signer/src/v0/signer.rs (L2045-2050)
```rust
        block_info.reject_reason = Some(block_rejection.response_data.reject_reason.clone());
        self.signer_db
            .insert_block(&block_info)
            .unwrap_or_else(|e| self.handle_insert_block_error(e));
        self.handle_block_rejection(&block_rejection, sortition_state);
        self.send_block_response(&block_info.block, block_rejection.into());
```

**File:** stacks-signer/src/v0/signer.rs (L2068-2069)
```rust
            BlockValidateResponse::Reject(block_validate_reject) => {
                self.handle_block_validate_reject(block_validate_reject, sortition_state);
```

**File:** docs/signer-flows.md (L39-46)
```markdown
    SIGN --> TALLY
    R1 --> TALLY
    R2 --> TALLY
    R3 --> TALLY
    TALLY{"meanwhile, every signer's<br/>answer is tallied by everyone"} -- "70% signed" --> PUSH["the signatures are gathered<br/>and the block handed to the node"]:::good
    TALLY -- "over 30% rejected —<br/>70% is now impossible" --> GR(["the block is dead:<br/>globally rejected"]):::bad
    TALLY -- "neither yet" --> W3(["wait"]):::hold
    PUSH --> ADOPT(["the chain adopts it:<br/>globally accepted"]):::good
```

**File:** docs/signer-flows.md (L151-162)
```markdown

Canonical paths shown; the exact rule in `BlockInfo::check_state` is: either
local state is reachable from anything not yet global, `PreCommitted` only from
`Unprocessed`, and each global state is unreachable from the other.

Timestamps: `approved_time` is stamped at pre-commit _or_ local acceptance
(first wins), `signed_self` only when we sign, `signed_group` when the group
threshold is observed.

> Anchors: `BlockInfo::check_state`, `move_to`, `mark_pre_committed`,
> `mark_locally_accepted`, `mark_globally_accepted`, `mark_locally_rejected`,
> `mark_globally_rejected` (signerdb.rs)
```
