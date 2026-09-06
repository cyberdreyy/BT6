Confirmed: the codebase has a generic conversion `SignerChainstateError -> RejectReason::ConnectivityIssues` [1](#0-0) , which is the designed "retryable, don't get stuck" path — `ConnectivityIssues` is in the re-evaluable set in `should_reevaluate_reject_reason` [2](#0-1) . However, the tenure-extend checks in both `v1.rs` and `v2.rs` bypass this generic mapping: they call `client.get_tenure_tip(...)` directly and, on any error (including a transient connectivity failure to the signer's own node), map it to `RejectReason::InvalidTenureExtend` [3](#0-2) [4](#0-3) . `InvalidTenureExtend` is in the *non*-re-evaluable branch of `should_reevaluate_reject_reason` [5](#0-4) , so once a signer hits this path it never reconsiders that exact block again, even after the node recovers and even if the miner resends the identical proposal.

### Title
Transient node RPC errors during tenure-extend evaluation are misclassified as a sticky `InvalidTenureExtend` instead of retryable `ConnectivityIssues`, permanently wedging a signer against a valid tenure-extend block - (File: stacks-signer/src/chainstate/v1.rs, stacks-signer/src/chainstate/v2.rs)

### Summary
This mirrors the audited bug class where an external dependency's failure (a `require`/check in another contract) is allowed to permanently block a legitimate operation instead of being treated as a transient, retriable condition. Here, the "external call" is the signer's own local RPC to its stacks-node (`get_tenure_tip`), and a transient failure of that call during tenure-extend evaluation is folded into the same "hard/invalid" bucket as genuine tenure-extend policy violations, rather than the crate's own `ConnectivityIssues` bucket that is designed to be retried.

### Finding Description
`SortitionsView::check_proposal` (v1) and `GlobalStateView::check_proposal` (v2) both evaluate tenure-extend transactions by calling `client.get_tenure_tip(...)` to fetch the current burn view of the tenure. If this call errors for any reason — a busy/overloaded local node, a timeout, a transient HTTP error, node restart, etc. — the error is mapped directly to `RejectReason::InvalidTenureExtend`: [3](#0-2) [4](#0-3) 

This differs from every other error path in the signer's proposal-evaluation pipeline, which funnels `SignerChainstateError`/`ClientError` failures through the blanket `From<SignerChainstateError> for RejectReason` impl that always produces `RejectReason::ConnectivityIssues`: [1](#0-0) 

The distinction matters because `handle_block_proposal`'s re-proposal logic explicitly treats these two reasons differently. `should_reevaluate_reject_reason` places `ConnectivityIssues` in the "may be transient, re-evaluate on re-proposal" bucket, but places `InvalidTenureExtend` in the "no need to re-validate, decision is final" bucket: [6](#0-5) 

So a signer that experiences a transient failure calling its own node's `get_tenure_tip` endpoint while evaluating a full- or read-count tenure-extend block will store a rejection with `RejectReason::InvalidTenureExtend` for that exact `signer_signature_hash`, and `should_reevaluate_block`/`should_reevaluate_reject_reason` will cause any subsequent identical re-proposal (the miner's proposal loop re-sends unchanged proposals until it accumulates rejection weight, per the comment at `handle_block_proposal`) to be answered with the stale, cached rejection rather than being re-evaluated — even after the node recovers.

### Impact Explanation
This is a liveness/wedge issue on the individual signer: the affected signer is permanently prevented from ever accepting that particular (otherwise valid) tenure-extend block, regardless of whether the underlying connectivity problem is transient and resolves within milliseconds. Under the "High" impact bucket ("a signer wedged into never signing valid blocks"), this qualifies: a signer's own local, ordinary infra hiccup at exactly the wrong instant converts what should be a retryable condition into a hard, sticky rejection that the signer can never revisit for that block, deviating from the crate's own designed retry semantics for connectivity failures.

### Likelihood Explanation
The trigger condition (a transient error from the signer's local RPC call to its own stacks-node during evaluation of a tenure-extend proposal) is not attacker-controlled and cannot be deliberately induced by a miner's block content — it depends on the signer's own node's health/load at the moment the tenure-extend block is evaluated. Given operational realities (node under load, restarts, GC pauses, brief network blips between signer and node), this is plausible to occur naturally, but it is not a griefing vector a single miner can reliably trigger on demand. This weakens the "one-slot miner (plus gossip) can trigger" criterion required by scope, and the practical impact is bounded to one signer's local wedge (not a set-wide equivocation or safety break) unless it happens to coincide across many signers simultaneously (e.g., a shared node outage), which is a correlated-infra scenario rather than a single-signer logic flaw exploitable by a miner.

### Recommendation
In both `v1.rs`'s and `v2.rs`'s tenure-extend evaluation, map `client.get_tenure_tip` errors through the same `RejectReason::ConnectivityIssues` classification used elsewhere in the chainstate module (e.g., by converting through `SignerChainstateError`/`ClientError` rather than hard-coding `RejectReason::InvalidTenureExtend`), so that `should_reevaluate_reject_reason` correctly re-evaluates the block on the next re-proposal once the node's transient error clears.

### Proof of Concept
Not exploitable via a concrete step-by-step miner/gossip PoC within the strict scope of this task: the failure requires the signer's own local RPC call to its stacks-node to error at the exact moment a tenure-extend block is evaluated, which is an operational/infra condition rather than something a one-slot miner can deterministically trigger through block content or message crafting. This is flagged as a design inconsistency with a plausible but not miner-triggerable-on-demand path, and should be treated as a defensive-robustness finding rather than a confirmed, reproducible exploit meeting the strict analog-scan criteria.

### Citations

**File:** stacks-signer/src/chainstate/mod.rs (L64-68)
```rust
impl From<SignerChainstateError> for RejectReason {
    fn from(error: SignerChainstateError) -> Self {
        RejectReason::ConnectivityIssues(error.to_string())
    }
}
```

**File:** stacks-signer/src/v0/signer.rs (L2705-2734)
```rust
/// Determine if a block should be re-evaluated based on its rejection reason˝
fn should_reevaluate_reject_reason(block_info: &BlockInfo) -> bool {
    if let Some(reject_reason) = &block_info.reject_reason {
        match reject_reason {
            RejectReason::ValidationFailed(ValidateRejectCode::UnknownParent)
            | RejectReason::ValidationFailed(ValidateRejectCode::NotFoundError)
            | RejectReason::NoSortitionView
            | RejectReason::ConnectivityIssues(_)
            | RejectReason::TestingDirective
            | RejectReason::InvalidTenureExtend
            | RejectReason::ConsensusHashMismatch { .. }
            | RejectReason::NoSignerConsensus
            | RejectReason::NotRejected
            | RejectReason::Unknown(_) => true,
            RejectReason::ValidationFailed(_)
            | RejectReason::RejectedInPriorRound
            | RejectReason::SortitionViewMismatch
            | RejectReason::ReorgNotAllowed
            | RejectReason::InvalidBitvec
            | RejectReason::PubkeyHashMismatch
            | RejectReason::InvalidMiner
            | RejectReason::NotLatestSortitionWinner
            | RejectReason::InvalidParentBlock
            | RejectReason::DuplicateBlockFound
            | RejectReason::IrrecoverablePubkeyHash
            | RejectReason::ProblematicTransactions
            | RejectReason::ProposalTooOld => {
                // No need to re-validate these types of rejections.
                false
            }
```

**File:** stacks-signer/src/chainstate/v1.rs (L412-416)
```rust
            let tenure_tip = client.get_tenure_tip(sortition_consensus_hash)
                .map_err(|e| {
                    warn!("Could not load current tenure tip while evaluating a tenure-extend; cannot approve."; "err" => %e);
                    RejectReason::InvalidTenureExtend
                })?;
```

**File:** stacks-signer/src/chainstate/v2.rs (L219-223)
```rust
            let tenure_tip = client.get_tenure_tip(tenure_id)
                .map_err(|e| {
                    warn!("Could not load current tenure tip while evaluating a tenure-extend; cannot approve."; "err" => %e);
                    RejectReason::InvalidTenureExtend
                })?;
```
