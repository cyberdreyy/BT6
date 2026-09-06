### Title
Fail-open `check_latest_block_in_tenure` on node RPC error lets a signer place its final, irreversible signature over a non-canonical block at the pre-commit recheck - (File: `stacks-signer/src/chainstate/mod.rs`)

### Summary
`SortitionData::check_latest_block_in_tenure` treats a failed `get_tenure_tip` RPC call as "the proposal is higher than the tenure tip" and returns `Ok(true)` (pass) rather than failing closed. This fail-open behavior is explicitly justified in the code's own comment by the assumption that "this proposal ultimately must be passed to the stacks-node for proposal processing" — i.e. the node's `/v3/block_proposal` endpoint is the real backstop. That assumption is true only at the *first* call site (fresh proposal evaluation, before the node has validated the block). The same function, via `check_block_against_signer_db_state`, is reused as the *final* re-check immediately before the signer emits its group signature at the pre-commit threshold (`handle_block_pre_commit`) — a point at which no further node validation of the full block occurs. If the RPC call errors at that specific moment, the fail-open path silently waives the "does this block confirm the tenure tip we expect" veto with no backstop left to catch it, exactly mirroring the reported bug class: a validation routine that silently passes on a lookup/connectivity failure, reused later in a context where the resulting HTTP/signature action is no longer re-validated.

### Finding Description
`check_latest_block_in_tenure` in [1](#0-0)  does:
```
let tip = match client.get_tenure_tip(tenure_id) {
    Ok(tip) => tip.anchored_header,
    Err(e) => {
        warn!(... "Assuming proposal is higher than the parent tenure for now.");
        return Ok(true);
    }
};
```
This function is the single implementation backing both `check_tenure_change_confirms_parent` and `confirms_latest_block_in_same_tenure` [2](#0-1) , which are in turn invoked by `check_block_against_signer_db_state` [3](#0-2) .

`check_block_against_signer_db_state` is called from three places documented in `docs/signer-flows.md`: proposal arrival (inside `check_proposal`), the validate-ok handler, and — critically — the pre-commit-threshold handler right before the signature is produced [4](#0-3) . The pre-commit path explicitly exists because "the chain and signer db state may have changed materially since this block passed the proposal-time checks... Re-run the chainstate checks before putting a signature over the block" [5](#0-4) .

The "assume higher" fallback is documented as safe only because "this proposal ultimately must be passed to the stacks-node for proposal processing: so, if we pass the block height check here, we are relying on the stacks-node proposal endpoint to do the validation" [6](#0-5) . At the pre-commit-threshold call site this reasoning is stale: the block was already submitted to and returned OK from `/v3/block_proposal` earlier; the whole point of this specific re-check is to catch changes that happened *after* that node validation, with no further node validation to follow. A transient RPC failure (node restart, timeout, momentary unavailability during burnchain-fork processing) at exactly this recheck causes the veto to be silently skipped, and the signer proceeds straight to `SIGN: mark_locally_accepted` [7](#0-6) .

This is structurally the same bug class as the PraisonAI SSRF: a validator that swallows a lookup error and passes by default, where the pass was only safe under an assumption ("DNS will resolve again the same way" / "the node endpoint will re-validate") that does not hold at the point the action (HTTP POST / block signature) is actually taken.

### Impact Explanation
If the tenure-tip lookup errors during the pre-commit recheck, the signer can sign a block that does not actually confirm as many blocks as the tenure legitimately has — the exact condition `check_latest_block_in_tenure` exists to veto (`SortitionViewMismatch` rejection). Since this recheck sits immediately before the irreversible signature (`mark_locally_accepted`/`handle_block_signature`), a bypass here means a signer can contribute its signature toward a conflicting/non-canonical block, undermining the "signer never signs a block that doesn't confirm the canonical tenure tip" safety invariant this whole recheck layer was added to guarantee (Critical-class impact per the rules: a signer signing a non-canonical/conflicting block).

### Likelihood Explanation
Reachable by a single miner slot without needing a majority of signers, another signer's key, or auth: the miner simply needs its re-proposed block (or a fresh proposal reaching pre-commit threshold) to have its final chainstate recheck coincide with a stacks-node RPC hiccup on the signer's own node — a normal, non-adversarial-flooding occurrence (node restart, temporary unavailability, GC pause, etc.), which the code's own comments acknowledge as an expected, handled case ("Failed to fetch the tenure tip... Assuming proposal is higher for now"). The bug is in the logic itself, not in exploiting volumetric flooding.

### Recommendation
Do not reuse the same "fail open on RPC error" fallback for both the proposal-time check and the pre-signature recheck. At minimum, `check_block_against_signer_db_state`'s pre-commit-threshold call path should treat a `get_tenure_tip` error as a `ConnectivityIssues` rejection (fail closed) rather than delegating to `check_latest_block_in_tenure`'s `Ok(true)` fallback, since no further node validation will occur before the signature is emitted. Alternatively, split `check_latest_block_in_tenure` into two variants with different error-handling policy depending on whether a node re-validation still follows.

### Proof of Concept
1. A miner proposes block B in tenure T; the proposal-time check passes and B is submitted to `/v3/block_proposal`, which returns OK.
2. Between the OK response and reaching 70% pre-commit weight, the signer's local node briefly fails to answer `get_tenure_tip(T)` (restart/timeout/reorg-processing hiccup).
3. `handle_block_pre_commit` reaches the threshold and calls `check_block_against_signer_db_state` → `check_latest_block_in_tenure`, whose `client.get_tenure_tip` call errors; the function returns `Ok(true)` (see `stacks-signer/src/chainstate/mod.rs:450-461`).
4. `check_block_against_signer_db_state` returns `None` (no rejection); the signer proceeds to `SIGN: mark_locally_accepted`, placing its signature over B even though B may not actually confirm the tenure's true latest tip — with no remaining node-side check to catch the discrepancy.

### Citations

**File:** stacks-signer/src/chainstate/mod.rs (L366-374)
```rust
    /// Check whether or not `block` is higher than the highest block in `tenure_id`.
    ///  returns `Ok(true)` if `block` is higher, `Ok(false)` if not.
    ///
    /// If we can't look up `tenure_id`, assume `block` is higher.
    /// This assumption is safe because this proposal ultimately must be passed
    /// to the `stacks-node` for proposal processing: so, if we pass the block
    /// height check here, we are relying on the `stacks-node` proposal endpoint
    /// to do the validation on the chainstate data that it has.
    ///
```

**File:** stacks-signer/src/chainstate/mod.rs (L450-461)
```rust
        let tip = match client.get_tenure_tip(tenure_id) {
            Ok(tip) => tip.anchored_header,
            Err(e) => {
                warn!(
                    "Failed to fetch the tenure tip for the parent tenure: {e:?}. Assuming proposal is higher than the parent tenure for now.";
                    "proposed_block_consensus_hash" => %block.header.consensus_hash,
                    "signer_signature_hash" => %block.header.signer_signature_hash(),
                    "parent_tenure" => %tenure_id,
                );
                return Ok(true);
            }
        };
```

**File:** stacks-signer/src/chainstate/mod.rs (L488-520)
```rust
    pub fn check_tenure_change_confirms_parent(
        tenure_change: &TenureChangePayload,
        block: &NakamotoBlock,
        signer_db: &mut SignerDb,
        client: &StacksClient,
        tenure_last_block_proposal_timeout: Duration,
        reorg_attempts_activity_timeout: Duration,
    ) -> Result<bool, ClientError> {
        Self::check_latest_block_in_tenure(
            &tenure_change.prev_tenure_consensus_hash,
            block,
            signer_db,
            client,
            tenure_last_block_proposal_timeout,
            reorg_attempts_activity_timeout,
        )
    }

    fn confirms_latest_block_in_same_tenure(
        block: &NakamotoBlock,
        signer_db: &mut SignerDb,
        client: &StacksClient,
        proposal_config: &ProposalEvalConfig,
    ) -> Result<bool, ClientError> {
        Self::check_latest_block_in_tenure(
            &block.header.consensus_hash,
            block,
            signer_db,
            client,
            proposal_config.tenure_last_block_proposal_timeout,
            proposal_config.reorg_attempts_activity_timeout,
        )
    }
```

**File:** stacks-signer/src/v0/signer.rs (L1340-1366)
```rust
        // The chain and signer db state may have changed materially since this block passed the
        // proposal-time checks (e.g. between validation and reaching the pre-commit threshold we
        // may have signed a block that this one would reorg). Re-run the chainstate checks
        // before putting a signature over the block, and respond with a rejection if they no
        // longer pass, just as the block validation response handler does.
        if let Some(block_rejection) =
            self.check_block_against_signer_db_state(stacks_client, &block_info.block)
        {
            warn!(
                "{self}: Reached the pre-commit threshold for a block, but it no longer passes the chainstate checks. Rejecting.";
                "signer_signature_hash" => %block_hash,
                "block_height" => block_info.block.header.chain_length,
                "reject_code" => %block_rejection.reason_code,
                "reject_reason" => &block_rejection.reason,
            );
            if let Err(e) = block_info.mark_locally_rejected() {
                if !block_info.has_reached_consensus() {
                    warn!("{self}: Failed to mark block as locally rejected: {e:?}");
                }
            };
            self.signer_db
                .insert_block(&block_info)
                .unwrap_or_else(|e| self.handle_insert_block_error(e));
            self.handle_block_rejection(&block_rejection, sortition_state);
            self.send_block_response(&block_info.block, block_rejection.into());
            return;
        }
```

**File:** stacks-signer/src/v0/signer.rs (L1803-1850)
```rust
    fn check_block_against_signer_db_state(
        &mut self,
        stacks_client: &StacksClient,
        proposed_block: &NakamotoBlock,
    ) -> Option<BlockRejection> {
        let signer_signature_hash = proposed_block.header.signer_signature_hash();
        // If this is a tenure change block, ensure that it confirms the correct number of blocks from the parent tenure.
        if let Some(tenure_change) = proposed_block.get_tenure_change_tx_payload() {
            // Ensure that the tenure change block confirms the expected parent block
            match SortitionData::check_tenure_change_confirms_parent(
                tenure_change,
                proposed_block,
                &mut self.signer_db,
                stacks_client,
                self.proposal_config.tenure_last_block_proposal_timeout,
                self.proposal_config.reorg_attempts_activity_timeout,
            ) {
                Ok(true) => return None,
                Ok(false) => {
                    return Some(self.create_block_rejection(
                        RejectReason::SortitionViewMismatch,
                        proposed_block,
                    ))
                }
                Err(e) => {
                    warn!("{self}: Error checking block proposal: {e}";
                        "signer_signature_hash" => %signer_signature_hash,
                        "block_id" => %proposed_block.block_id()
                    );
                    return Some(self.create_block_rejection(
                        RejectReason::ConnectivityIssues(
                            "error checking block proposal".to_string(),
                        ),
                        proposed_block,
                    ));
                }
            }
        }

        // Ensure that the block is the last block in the chain of its current tenure.
        match SortitionData::check_latest_block_in_tenure(
            &proposed_block.header.consensus_hash,
            proposed_block,
            &mut self.signer_db,
            stacks_client,
            self.proposal_config.tenure_last_block_proposal_timeout,
            self.proposal_config.reorg_attempts_activity_timeout,
        ) {
```

**File:** docs/signer-flows.md (L248-268)
```markdown
    TH -- yes --> RECHECK{"chainstate checks still pass?<br/>check_block_against_signer_db_state<br/>→ section 7"}
    RECHECK -- no --> REJ["mark_locally_rejected,<br/>handle_block_rejection,<br/>broadcast rejection"]:::bad
    RECHECK -- yes --> CONF["signed conflicts at height ≥ h,<br/>in ANY tenure<br/>get_signed_conflicts"]
    CONF --> PERM{"covered by a reorg permit whose<br/>permitting sortition is still canonical?<br/>reorg_permit_stands"}
    PERM -- yes --> EXCL(["excluded — our signature must not<br/>block a replacement we sanctioned"]):::good
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

**File:** docs/signer-flows.md (L391-398)
```markdown
`check_latest_block_in_tenure` answers "does this block confirm the tip we
expect?" and it runs in three places: at proposal arrival (inside
`check_proposal`), at validate-ok, and at the moment of signing. _Which_ tenure
it is asked about depends on the block: a tenure-change block is checked against
its **parent** tenure, every other block against its **own**. Never both. The
pivotal helper is `get_tenure_last_block_info`, which considers only blocks that
carry a signature (`get_last_signed_block`): a pre-commit never vetoes anything,
it only counts as miner activity.
```
