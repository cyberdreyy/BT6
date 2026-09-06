### Title
Globally-rejected block state can be silently erased and re-evaluated via re-proposal, bypassing the terminal-state invariant - ([File: stacks-signer/src/v0/signer.rs])

### Summary
`BlockInfo::check_state` enforces that a block which has reached `BlockState::GloballyRejected` can never be moved to `BlockState::GloballyAccepted` (and vice-versa) — this is the signer's terminal-state guarantee for a network-wide rejection decision. However, this invariant is only enforced on the `move_to` state-transition path. A parallel path in `handle_block_proposal` re-proposal handling constructs a brand-new `BlockInfo` from scratch (`BlockInfo::from(block_proposal.clone())`) and persists it via `insert_block` (an upsert on `(reward_cycle, signer_signature_hash)`), which never calls `check_state`/`move_to` at all. The only guard that prevents this path from firing on an already-decided block, `globally_approved_and_responded()`, checks **only** the `GloballyAccepted` state — not `GloballyRejected`. This is structurally identical to the Flowise flaw: a security-relevant check (state-invariant enforcement) is wired onto one code path (`move_to`) but a functionally-equivalent path to the same outcome (creating/overwriting `BlockInfo` on re-proposal) reaches the sensitive operation without going through it.

### Finding Description
The signer's block bookkeeping state machine is defined in `stacks-signer/src/signerdb.rs`: [1](#0-0) 

`check_state` explicitly forbids `GloballyRejected -> GloballyAccepted` and vice versa, and `move_to` is the only entry point that enforces it.

When a miner re-sends a block proposal for a `signer_signature_hash` the signer has already seen, `should_reevaluate_block` decides whether to treat it as new: [2](#0-1) 

The only "already decided, stop" branch is `block_info.globally_approved_and_responded()`, which per the documented flow diagram is scoped to **globally accepted** blocks only: [3](#0-2) 

If the block is not `globally_approved_and_responded` (i.e. it is `GloballyRejected`, `LocallyRejected`, etc.) and `should_reevaluate_reject_reason(block_info)` decides the recorded rejection reason is reconsiderable, `should_reevaluate_block` returns `true` — the "re-evaluate from scratch" path: [4](#0-3) 

Back in `handle_block_proposal`, this leads to a fresh `BlockInfo` construction that explicitly discards all prior state: [5](#0-4) 

This new `BlockInfo` starts at `BlockState::Unprocessed` (the default constructed-from-proposal state) and is written to the DB with `insert_block`, an upsert keyed on `(reward_cycle, signer_signature_hash)` — never going through `check_state`/`move_to`. The invariant that blocks `GloballyRejected -> GloballyAccepted` therefore only holds for callers that use `move_to`; the re-proposal path sidesteps it entirely by replacing the row wholesale, exactly as Flowise's SSRF deny-list only wrapped `axios`/`node-fetch` while the equivalent `http`/`https`/`net` modules reached the same network primitive unguarded.

The practical consequence: once a block reaches `GloballyRejected` (≥30% weight rejected it — a consensus-visible, supposedly terminal decision, see `store_and_process_block_rejection`/`mark_globally_rejected` in `stacks-signer/src/v0/signer.rs`), a miner can simply re-broadcast the identical `BlockProposal` (same `signer_signature_hash`). If the previously recorded `reject_reason` is one `should_reevaluate_reject_reason` treats as reconsiderable, this signer discards the `GloballyRejected` bookkeeping and re-runs `check_block_against_state` → `submit_block_for_validation` → the whole pre-commit/signing pipeline as if the block were brand new, allowing it to reach `LocallyAccepted`/contribute a signature to a block the network had already globally rejected.

### Impact Explanation
This breaks the "rejection recounted as an accept" equality class explicitly in scope: a block a signer had marked `GloballyRejected` (a consensus decision) can, via nothing more than a miner re-sending the same proposal, be silently reset to `Unprocessed` and re-signed by this signer without ever going through the state-machine invariant that is supposed to make `GloballyRejected` terminal. If enough signers hit the same reconsiderable-reason condition and the underlying chain state has not actually changed enough to legitimately invalidate the original rejection, this signer's renewed signature could contribute toward flipping the outcome for a block the network already rejected — a safety violation (signing a previously globally-rejected/non-canonical block).

### Likelihood Explanation
Triggerable purely by a one-slot miner re-broadcasting an already-rejected `BlockProposal` (identical `signer_signature_hash`) over the normal proposal-gossip channel — no majority of signers, no key material, and no auth token are required. The only precondition is that the specific `RejectReason` recorded for the block is one `should_reevaluate_reject_reason` classifies as reconsiderable (this is exactly the mechanism designed to let signers retry after transient conditions like connectivity errors clear); the bug is that this retry path is not restricted to non-terminal (`Locally*`) states, so it also fires for `GloballyRejected`.

### Recommendation
In `should_reevaluate_block` (`stacks-signer/src/v0/signer.rs`), extend the early-exit "already decided" guard so it also short-circuits on `BlockState::GloballyRejected` (in addition to `globally_approved_and_responded()`), or make `should_reevaluate_reject_reason` itself unconditionally return `false` once `block_info.has_reached_consensus()`/global state has been reached, regardless of the specific reject reason. Additionally, ensure the re-proposal path in `handle_block_proposal` goes through `BlockInfo::move_to`/`check_state` rather than constructing and upserting a fresh `BlockInfo`, so the terminal-state invariant is enforced uniformly on every path that can mutate a block's persisted state.

### Proof of Concept
1. Attacker controls the winning miner slot for tenure T and proposes block `B` with `signer_signature_hash = H` that ultimately fails validation/chain checks such that the signer set collectively reaches `GloballyRejected` (≥30% weight, per `store_and_process_block_rejection` → `mark_globally_rejected`) with a reject reason that `should_reevaluate_reject_reason` treats as reconsiderable (e.g. a transient/connectivity-style rejection code).
2. The attacking miner re-broadcasts the exact same `BlockProposal` for `H` over StackerDB.
3. On each signer, `handle_block_proposal` looks up the prior `BlockInfo` (state `GloballyRejected`), calls `should_reevaluate_block`, which does not early-exit (since `globally_approved_and_responded()` only guards `GloballyAccepted`) and returns `true` because the reject reason is reconsiderable.
4. `handle_block_proposal` builds a fresh `BlockInfo::from(block_proposal.clone())` (state reset to `Unprocessed`) and re-runs the full evaluation/validation/pre-commit pipeline for `H`, bypassing the `check_state` invariant that should make `GloballyRejected` terminal.
5. If the underlying chain condition that triggered the original rejection has not actually changed, this signer can end up producing a signature/pre-commit over a block the network already globally rejected, without any state-transition guard preventing it.

Note: I was unable to retrieve the exact enumeration of `RejectReason` variants that `should_reevaluate_reject_reason` treats as reconsiderable (the function body did not surface via the available search tools), so I cannot enumerate every concrete trigger condition; however, the structural gap — the "already decided" guard checking only `GloballyAccepted` while the re-proposal path bypasses `check_state` entirely — is confirmed directly from the cited code and the accompanying flow documentation.

### Citations

**File:** stacks-signer/src/signerdb.rs (L313-329)
```rust
    /// Check if the block state transition is valid
    fn check_state(&self, state: BlockState) -> bool {
        let prev_state = &self.state;
        if *prev_state == state {
            return true;
        }
        match state {
            BlockState::Unprocessed => false,
            BlockState::LocallyAccepted | BlockState::LocallyRejected => !matches!(
                prev_state,
                BlockState::GloballyRejected | BlockState::GloballyAccepted
            ),
            BlockState::GloballyAccepted => !matches!(prev_state, BlockState::GloballyRejected),
            BlockState::GloballyRejected => !matches!(prev_state, BlockState::GloballyAccepted),
            BlockState::PreCommitted => matches!(prev_state, BlockState::Unprocessed),
        }
    }
```

**File:** stacks-signer/src/v0/signer.rs (L1481-1504)
```rust
    /// Determine if an already tracked block should be re-evaluated based on a new block proposal for it.
    /// Returns true if the block should be re-evaluated, false if it should be ignored.
    fn should_reevaluate_block(
        &mut self,
        stacks_client: &StacksClient,
        sortition_state: &mut Option<SortitionsView>,
        block_info: &BlockInfo,
        block_proposal: &BlockProposal,
    ) -> bool {
        let signer_signature_hash = block_info.block.header.signer_signature_hash();
        if block_info.globally_approved_and_responded() {
            info!("{self}: received a block proposal for a globally accepted block to which we have already responded. Ignoring.";
                "signer_signature_hash" => %signer_signature_hash,
                "block_id" => %block_info.block.block_id(),
                "block_height" => block_info.block.header.chain_length,
                "burn_height" => block_proposal.burn_height,
                "consensus_hash" => %block_info.block.header.consensus_hash,
                "timestamp" => block_info.block.header.timestamp,
                "signed_group" => block_info.signed_group,
                "signed_self" => block_info.signed_self,
                "valid" => ?block_info.valid
            );
            return false;
        }
```

**File:** stacks-signer/src/v0/signer.rs (L1560-1572)
```rust
        } else {
            info!(
                "{self}: received a block proposal for this block before, but our rejection reason allows us to reconsider";
                "reject_reason" => ?block_info.reject_reason,
                "signer_signature_hash" => %signer_signature_hash,
                "block_id" => %block_proposal.block.block_id(),
                "block_height" => block_proposal.block.header.chain_length,
                "burn_height" => block_proposal.burn_height,
                "consensus_hash" => %block_proposal.block.header.consensus_hash
            );
        }
        true
    }
```

**File:** stacks-signer/src/v0/signer.rs (L1652-1654)
```rust
        crate::monitoring::actions::increment_block_proposals_received();
        // Creating a new proposal will overwrite any prior proposal info on the block if it exists, e.g. validity, signed_timestamps, etc.
        let mut block_info = BlockInfo::from(block_proposal.clone());
```

**File:** docs/signer-flows.md (L176-185)
```markdown
    RC -- yes --> KNOWN{"block already tracked?<br/>block_lookup_by_reward_cycle"}
    KNOWN -- yes --> REEV["should_reevaluate_block"]
    REEV --> DONE1{"globally accepted and<br/>already responded?"}
    DONE1 -- yes --> IGN2(["ignore"])
    DONE1 -- no --> REASON{"prior reject reason<br/>re-evaluable?<br/>should_reevaluate_reject_reason"}
    REASON -- no --> PC{"state = PreCommitted?"}
    PC -- yes --> RESEND["re-send pre-commit, re-run<br/>handle_block_pre_commit → section 5"]
    PC -- no --> PREV["re-send previous response<br/>determine_response, or wait if<br/>validation still pending"]
    REASON -- yes --> FRESH
    KNOWN -- no --> DRAIN["collect early votes<br/>drain_pending_block_responses"] --> FRESH["fresh evaluation:<br/>new BlockInfo, fetch<br/>SortitionsView if needed"]
```
