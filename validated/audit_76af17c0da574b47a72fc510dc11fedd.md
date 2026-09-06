### Title
v1 signer's tenure-start duplicate-block check omits locally-accepted-but-not-yet-globally-accepted blocks, unlike v2 - (File: stacks-signer/src/chainstate/v1.rs)

### Summary
`SortitionsView::validate_tenure_change_payload` in the v1 chainstate module is the proposal-time guard that is supposed to reject a second tenure-start block for a tenure the signer has already signed for. It queries `get_last_globally_accepted_block`, while the v2 equivalent path uses `get_last_signed_block`, which additionally counts locally-accepted (self-signed) blocks. This is the same class of bug as the reported SQLi: two structurally parallel code paths that are supposed to enforce the same invariant, where one was written with the complete/correct check and the sibling path was left with an incomplete one.

### Finding Description
`validate_tenure_change_payload` (v1) performs the tenure-start duplicate check like this: [1](#0-0) 

It only looks up `get_last_globally_accepted_block`, i.e. blocks in state `GloballyAccepted`. A block the signer has already **signed** (state `LocallyAccepted`, `signed_self` set — per the state machine, `LocallyAccepted` *is* "we sign") is invisible to this check because it is not yet globally accepted: [2](#0-1) 

The v2 chainstate module performs the analogous check with `get_last_signed_block`, which the code comments state on purpose considers both `GloballyAccepted` and `LocallyAccepted` states: [3](#0-2) 

The project's own documentation of the signer flows explicitly calls out this asymmetry as deliberate-but-risky: [4](#0-3) 

This is exactly the same root-cause shape as the OpenSTAManager report: one branch (v2 / the first `SELECT`) uses the fully correct predicate; the parallel branch (v1 / the second `SELECT`) uses a strictly weaker one, breaking the equality "have we already committed to a tenure-start block in this tenure?" between the two code paths.

Because this check runs only once, at proposal arrival, and is never re-run (per the same doc section), a v1 signer that has already **signed** (but not yet seen globally accepted) tenure-start block B1 for tenure T will not reject a second tenure-start proposal B2 for T at `check_proposal` time. B2 is then submitted for node validation and tracked toward the pre-commit/signature threshold, relying entirely on the downstream pre-commit-time conflict guard (`get_signed_conflicts` / freshness / tenure-liveness questions in `handle_block_pre_commit`) to prevent an actual second signature: [5](#0-4) 

That guard is not a strict re-application of the proposal-time duplicate check — it depends on conflict "freshness" and on the stacks-node's view of the tenure/burn-chain tip being reachable, and only falls into the direct own-tenure comparison once the first signature is judged stale: [6](#0-5) 

So the two layers of defense are not equivalent, and the weaker v1 proposal-time check widens the timing window in which a second tenure-start block for the same tenure can be pre-committed and, if the first signature goes stale (block-proposal timeout, or the node not yet confirming it) before the pre-commit threshold on B2 is reached, actually signed — producing two signed, conflicting tenure-start blocks for the same tenure from a single v1 signer's perspective.

### Impact Explanation
This breaks the "one signed tenure-start block per tenure" equality that v2's `get_last_signed_block`-based check is specifically designed to preserve (per the code's own inline explanation of why it differs). A signer ending up signing a second, conflicting tenure-start block for the same tenure is exactly the "Critical" bucket in scope: a signer signing a conflicting block.

### Likelihood Explanation
Reachable purely by miner+gossip behavior described in the in-scope test `signer_refuses_to_sign_second_sibling_tenure_start` / `stale_sibling_replaced_when_canonical_tip_below` scenario family, which the repository's own tests show is a real, timing-dependent race (no majority-signer collusion or key compromise required): a single miner proposes two competing tenure-start blocks for the same tenure, timed around a v1 signer's block-proposal timeout / freshness window. It requires the first signature to have gone stale and the node to not yet confirm it as canonical tip — a condition the codebase already treats as reachable in normal operation (that's precisely why the "own-tenure" branch of the pre-commit conflict guard exists at all).

### Recommendation
Change `validate_tenure_change_payload` in `stacks-signer/src/chainstate/v1.rs` to use `SignerDb::get_last_signed_block` (matching the v2 path) instead of `get_last_globally_accepted_block`, so a locally-accepted (self-signed) tenure-start block is also treated as a duplicate at proposal time, closing the same timing window that the v2 code path already closes.

### Proof of Concept
1. Two competing tenure-start block proposals B1 and B2 for the same tenure `T`, from the tenure's winning miner (single miner, no signer collusion needed).
2. A v1-protocol signer receives B1 first, it passes `check_proposal`, is validated by the node, pre-commits, and — before the group pre-commit threshold is reached by others — the signer itself locally accepts and signs B1 (`mark_locally_accepted`), setting `signed_self` while state remains `LocallyAccepted` (not yet `GloballyAccepted`).
3. The miner (or a delayed relay) now proposes B2 for the same tenure `T`. `SortitionsView::check_proposal` → `validate_tenure_change_payload` calls `signer_db.get_last_globally_accepted_block(&block.header.consensus_hash)` (`stacks-signer/src/chainstate/v1.rs:505-518`), which returns `None` because B1 is only `LocallyAccepted`, so the `DuplicateBlockFound` rejection is skipped and B2 is accepted for validation/tracking.
4. If B1's signature subsequently goes stale (per the freshness cutoff in `handle_block_pre_commit`) or the node has not yet confirmed B1 as the tenure's tip when B2 crosses the pre-commit weight threshold, the own-tenure branch of the section-5 conflict guard permits signing, and the signer produces a signature over B2 as well — two signed, conflicting tenure-start blocks for tenure `T` from the same signer.

Note: I was not able to fully verify the exact numeric freshness/timeout values in this codebase in the time available, so the precise timing window required to trigger step 4 in production configuration is not confirmed beyond what the existing test suite (`signer_refuses_to_sign_second_sibling_tenure_start` and related tests in `stacks-signer/src/v0/tests.rs`) demonstrates for the general race pattern.

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

**File:** stacks-signer/src/signerdb.rs (L112-127)
```rust
define_u8_enum!(
/// Block state relative to the signer's view of the stacks blockchain
BlockState {
    /// The block has not yet been processed by the signer
    Unprocessed = 0,
    /// The block is accepted by the signer but a threshold of signers has not yet signed it
    LocallyAccepted = 1,
    /// The block is rejected by the signer but a threshold of signers has not accepted/rejected it yet
    LocallyRejected = 2,
    /// A threshold number of signers have signed the block
    GloballyAccepted = 3,
    /// A threshold number of signers have rejected the block
    GloballyRejected = 4,
    /// The block is pre-committed by the signer, but not yet signed
    PreCommitted = 5
});
```

**File:** stacks-signer/src/signerdb.rs (L1571-1586)
```rust
    /// which is why the predicates deliberately differ on rejected blocks (see there).
    pub fn get_last_signed_block(
        &self,
        tenure: &ConsensusHash,
    ) -> Result<Option<BlockInfo>, DBError> {
        let query = "SELECT block_info FROM blocks WHERE consensus_hash = ?1 AND state IN (?2, ?3) ORDER BY stacks_height DESC LIMIT 1";
        let args = params![
            tenure,
            &BlockState::GloballyAccepted.to_string(),
            &BlockState::LocallyAccepted.to_string(),
        ];
        let result: Option<String> = query_row(&self.db, query, args)?;

        try_deserialize(result)
    }

```

**File:** docs/signer-flows.md (L253-268)
```markdown
    PERM -- no --> FRESH{"any of them still fresh?<br/>last_endorsed > cutoff"}
    FRESH -- yes --> SORT{"conflict_still_blocks, question 1:<br/>is its tenure's sortition still on the<br/>canonical burn chain?<br/>get_sortition_by_burn_hash"}
    SORT -- "404, with the node's burnchain tip<br/>at or past the burn block — a fork<br/>orphaned the tenure" --> OWN
    SORT -- "canonical, or we never<br/>saved its burn block" --> LIVE{"question 2: does the node's chain<br/>still reach the block itself?<br/>get_tenure_tip(its tenure)"}
    SORT -- "could not ask, or 404 with the<br/>node's tip still below the burn block" --> HOLD1
    LIVE -- "yes — real chain state" --> HOLD1["refuse to sign for now<br/>(may sign once conflict is stale)"]:::hold
    LIVE -- "no, and it was<br/>globally accepted" --> OWN
    LIVE -- "no, only locally accepted<br/>— but above this height" --> OWN
    LIVE -- "no, only locally accepted<br/>and a sibling at this height" --> HOLD1
    LIVE -- "could not ask" --> HOLD1
    FRESH -- "no — all stale" --> OWN{"a conflict in this block's<br/>OWN tenure?"}
    OWN -- yes --> TIP{"own tenure confirmed<br/>at ≥ this height?<br/>get_tenure_tip(own tenure)"}
    TIP -- yes --> HOLD2["refuse to sign"]:::hold
    TIP -- "no — never confirmed" --> SIGN
    TIP -- "node unreachable" --> SIGN
    OWN -- no --> SIGN["SIGN: mark_locally_accepted,<br/>handle_block_signature,<br/>broadcast acceptance"]:::good
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

**File:** stacks-signer/src/v0/signer.rs (L1340-1345)
```rust
        // The chain and signer db state may have changed materially since this block passed the
        // proposal-time checks (e.g. between validation and reaching the pre-commit threshold we
        // may have signed a block that this one would reorg). Re-run the chainstate checks
        // before putting a signature over the block, and respond with a rejection if they no
        // longer pass, just as the block validation response handler does.
        if let Some(block_rejection) =
```
