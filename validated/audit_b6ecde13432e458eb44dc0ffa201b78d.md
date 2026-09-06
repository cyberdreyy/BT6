## Title
v1 signer chainstate accepts a duplicate tenure-change block that only the local signer set has signed — `DuplicateBlockFound` check uses `get_last_globally_accepted_block` instead of `get_last_signed_block` ([File: stacks-signer/src/chainstate/v1.rs])

### Summary
`validate_tenure_change_payload` in the v1 chainstate path only rejects a second tenure-start block for the same tenure when a prior block in that tenure has already reached **global** acceptance. The v2 path was patched (confirmed by the CHANGELOG entry "When checking tenure change blocks, ensure there are no locally accepted blocks in the tenure, not just globally accepted blocks" and the regression test `check_tenure_change_rejects_when_locally_accepted_block_exists`) to use `get_last_signed_block`, which also catches `LocallyAccepted` blocks. v1 was never updated and still calls `get_last_globally_accepted_block`. [1](#0-0) 

vs. the fixed v2 equivalent: [2](#0-1) 

### Finding Description
This is the same bug class as the reported jq `$ENV` bypass: one enforcement path (the "explicit" route, `env`) is closed, but an equivalent alternate route (`$ENV`) to the identical outcome was left open. Here, the "explicit" route is `DuplicateBlockFound` detection via `get_last_globally_accepted_block`, and the "equivalent alternate route" is a block that is `LocallyAccepted` (i.e., has already crossed this v1 signer's own 70% pre-commit/signature threshold and is `signed_self`/`signed_group`) but has not yet been observed by the node as globally accepted. Both states represent "we have already signed a block in this tenure," but only the globally-accepted state trips the guard on v1.

Per `docs/signer-flows.md` §7, this `DuplicateBlockFound` check is a proposal-time-only gate — it is never re-run at validate-ok or at signing: [3](#0-2) 

The only backstop for a block that later crosses the pre-commit threshold is the signing-time conflict guard `get_signed_conflicts`, but that guard only fires for a signer who *personally* signed the first block, or for a conflict where the *group* threshold has already been publicly observed: [4](#0-3) 

So a v1 signer who never validated/pre-committed the first tenure-start block (e.g., because the miner raced two competing tenure-change proposals to disjoint slices of the signer set via StackerDB gossip) has no record of that first block as a self-signed conflict, and the group threshold for it may not yet be publicly observed. For that signer, `validate_tenure_change_payload` is the *only* gate against the second, competing tenure-change block for the same tenure — and on v1 that gate is blind to `LocallyAccepted` state.

### Impact Explanation
A single miner can craft two distinct tenure-change/coinbase blocks (differing e.g. in included transactions) for the same tenure and race them to different, non-overlapping-enough subsets of a v1-protocol-version signer set via normal proposal gossip. Signers that already locally/self-signed block A are blocked from also signing block B by their own signing-time conflict check, but signers that never processed A (because B reached them first, or A never reached them) will validate B as if no duplicate exists, and can pre-commit/sign it. This creates two independently signed candidate blocks for the same tenure height that were vetted by *different* signer subsets — an equivocation vector at the tenure-start point, breaking the "one signed block per tenure/height" invariant that v2 restores by checking `get_last_signed_block`. This falls squarely in the "signer signing a conflicting block" / equivocation-guard category described as Critical/High in the analog rules, since it is triggerable by a single miner plus ordinary gossip and requires no majority-signer collusion.

### Likelihood Explanation
Requires only a malicious/faulty miner (a "one-slot miner") controlling proposal broadcast timing on StackerDB, and applies specifically to signers still running the v1 (pre-global-state) chainstate/protocol path — which remains in the codebase and is explicitly gated by `determine_active_signer_protocol_version`/`uses_global_state()` rather than removed. No majority signer collusion, node code change, or private key compromise is needed; only network-level message ordering control, which a miner naturally has.

### Recommendation
Change `validate_tenure_change_payload` in `stacks-signer/src/chainstate/v1.rs` to use `signer_db.get_last_signed_block(&block.header.consensus_hash)` (mirroring the fixed v2 logic) instead of `get_last_globally_accepted_block`, so that a `LocallyAccepted`/`PreCommitted-with-signature` block in the tenure also trips `DuplicateBlockFound`.

### Proof of Concept
1. Run a mixed/legacy fleet where at least some signers are still on the v1 (local-sortition-view) chainstate path (`uses_global_state() == false`).
2. Malicious miner proposes tenure-start block A (tenure T, height h) to signer subset S1. S1 validates, pre-commits, and signs A (locally accepted, `signed_self` set), but S1's aggregate weight is below the network-wide 70% threshold, so global acceptance is never reached and no `NewBlock` event / `get_last_globally_accepted_block` hit occurs for T.
3. Miner proposes a different tenure-start block B (same tenure T, same height h, different tx set) to signer subset S2 (disjoint from or not overlapping enough with S1) that never saw/validated A.
4. On S2 (v1 signers), `validate_tenure_change_payload` calls `get_last_globally_accepted_block(T)`, which returns `None` since A is only locally accepted — the duplicate check passes, and S2 validates and signs B.
5. Two block-signature candidates (A signed by S1, B signed by S2) now exist for the same tenure/height, produced entirely by miner-controlled gossip ordering with no majority-signer collusion — confirmed as previously-fixed-but-only-in-v2 by the code comment/test at `stacks-signer/src/chainstate/tests/v2.rs:748-850` and the CHANGELOG entry `"When checking tenure change blocks, ensure there are no locally accepted blocks in the tenure, not just globally accepted blocks."`

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

**File:** docs/signer-flows.md (L274-286)
```markdown
Order matters here: the chainstate re-check runs first and produces an explicit
(sticky) rejection when the block now conflicts with a signed one. The conflict
guard behind it is the silent backstop for what that re-check cannot see, and
silence keeps the door open to sign later once the conflict goes stale. Two
blind spots make the guard necessary:

- the re-check only ever looks at _one_ tenure (a tenure-change block's parent,
  or any other block's own), so a signed sibling at the same height in a third
  tenure is invisible to it;
- the `DuplicateBlockFound` check that would catch a second block in the same
  tenure lives in `check_proposal` and runs only at proposal arrival, never
  again. A block that crosses the pre-commit threshold minutes later has no
  other guard, which is what the own-tenure branch above covers.
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
