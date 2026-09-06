### Title
Unbounded tenure-history walk in `check_parent_tenure_choice` lets a single miner stall a signer's block-proposal evaluation - ([File: stacks-signer/src/client/stacks_client.rs])

### Summary
When a proposed block's `TenureChangePayload` names a `prev_tenure_consensus_hash` that differs from the signer's last-known sortition, the shared chainstate code walks the entire tenure history between the two points to decide whether the implied reorg is legitimate. That walk (`StacksClient::get_tenure_forking_info`) is an unbounded `while` loop with no maximum-iteration cap, no depth limit, and no timeout — it keeps issuing HTTP requests to the node and growing an in-memory `VecDeque` until it reaches the claimed parent tenure, however far back that is. A miner can choose an ancient parent tenure for their block commit (the exact "vtxindex=0"-style attack the code's own comment warns about), forcing this walk to traverse a large fraction of chain history synchronously inside proposal evaluation, in the same way the reported CVE's OctoRPKI walked unboundedly long CA chains until it blew past its iteration budget and crashed.

### Finding Description
`SortitionData::check_parent_tenure_choice` (`stacks-signer/src/chainstate/mod.rs:170-295`) is invoked whenever a block's tenure-change payload does not build on the last sortition: [1](#0-0) 

It calls `client.get_tenure_forking_info(&self.parent_tenure_id, &self.prior_sortition)`, where `parent_tenure_id` comes directly from the sortition/block-commit data the miner controls, and `prior_sortition` is the signer's actual last-known sortition: [2](#0-1) 

`get_tenure_forking_info` (`stacks-signer/src/client/stacks_client.rs:318-357`) implements the walk as an unbounded loop: each iteration calls `get_tenure_forking_info_step`, which asks the node for up to `DEPTH_LIMIT` (10, per `stackslib/src/net/api/get_tenures_fork_info.rs:38`) tenures at a time, and the outer `while` loop simply keeps re-querying with the new frontier until `chosen_parent` is reached, with no bound on the number of outer iterations: [3](#0-2) 

Because `parent_tenure_id` is taken from the block-commit's claimed parent (exactly the value the code comment in `validate_tenure_change_payload` calls out as attacker-influenced — "This catches block commits with bad parent_block_ptr (e.g., vtxindex=0 exploit)"), a single miner who wins one sortition slot can build a block whose tenure change names a parent tenure deep in chain history instead of the immediate predecessor. That is sufficient to make `check_parent_tenure_choice` walk (and accumulate in memory) every tenure between the real last sortition and the attacker-chosen ancient parent, in chunks of ~10 per round trip, with no depth limit, no timeout, and no maximum-iteration guard on the client side — the exact "no bound on iterations while consuming attacker-influenced chain data" pattern described in the OctoRPKI advisory (CWE-834 / CWE-754).

### Impact Explanation
This computation runs synchronously as part of proposal validation on the signer's main processing path (`check_block_against_local_state`/`check_block_against_global_state` → `check_proposal` → `validate_tenure_change_payload` → `check_parent_tenure_choice`). A sufficiently deep claimed-parent tenure forces the signer to spend a long, unbounded amount of time and memory walking history via repeated node RPCs before it can render any verdict on the proposal (accept, reject, or otherwise). While this proposal is being evaluated, the signer's proposal-handling logic is tied up, which can delay or starve evaluation of legitimate concurrent proposals — a liveness degradation of the signer consistent with the "wedged into never signing valid blocks" impact class. In the worst case (very deep chosen parent, e.g., near genesis) resource growth is effectively proportional to full chain depth, which is the same unbounded-iteration crash/hang class as the referenced advisory.

### Likelihood Explanation
Triggering the code path only requires a miner to win a single sortition slot and craft a block whose tenure-change payload's `prev_tenure_consensus_hash` corresponds to a distant ancestor tenure rather than the immediate one — a value derived from the block commit's parent pointer, which the code's own inline comment acknowledges miners can manipulate. No cooperation from other signers or majority control is needed; gossip of the single crafted proposal to signers is sufficient to trigger the unbounded walk on each signer that evaluates it.

### Recommendation
Bound `StacksClient::get_tenure_forking_info`'s outer loop with an explicit maximum number of iterations/maximum total tenures fetched (and/or a wall-clock timeout), and reject the proposal with a clear `RejectReason` once the bound is exceeded instead of continuing to walk indefinitely. Consider also capping how far back `check_parent_tenure_choice` is willing to accept a reorg-style parent (e.g., relative to `MAX_FORK_DEPTH`, which is already used elsewhere in the signer for similar staleness bounds) before even issuing the RPC calls.

### Proof of Concept
1. As the sole miner winning a sortition, construct a `NakamotoBlock` whose `TenureChangePayload.prev_tenure_consensus_hash` references a tenure many sortitions back (not the immediately preceding one), while still passing the burn-token/parent-block-ptr checks enforced at the block-commit level.
2. Propose this block to the signer set via the normal block-proposal gossip path.
3. On each signer, `handle_block_proposal` → `check_block_against_state` → `check_proposal` → `validate_tenure_change_payload` detects `tenure_change.prev_tenure_consensus_hash != parent_tenure_id`'s sibling case (mismatch from `prior_sortition`) and calls `check_parent_tenure_choice`, which invokes `client.get_tenure_forking_info(parent_tenure_id, prior_sortition)`.
4. Observe that `get_tenure_forking_info`'s `while` loop (`stacks-signer/src/client/stacks_client.rs:333-354`) issues repeated `/v3/tenures/fork_info` RPCs and grows its `VecDeque` proportionally to the distance between the claimed parent tenure and the real last sortition, with no cap — measurably delaying the signer's response to this and subsequent proposals as the chosen depth increases.

### Citations

**File:** stacks-signer/src/chainstate/mod.rs (L176-195)
```rust
        // if the parent tenure is the last sortition, it is a valid choice.
        // if the parent tenure is a reorg, then all of the reorged sortitions
        //  must either have produced zero blocks _or_ produced their first (and only) block
        //  very close to the burn block transition.
        if self.prior_sortition == self.parent_tenure_id {
            return Ok(true);
        }
        info!(
            "Most recent miner's tenure does not build off the prior sortition, checking if this is valid behavior";
            "sortition_state.consensus_hash" => %self.consensus_hash,
            "sortition_state.prior_sortition" => %self.prior_sortition,
            "sortition_state.parent_tenure_id" => %self.parent_tenure_id,
        );

        let tenures_reorged =
            client.get_tenure_forking_info(&self.parent_tenure_id, &self.prior_sortition)?;
        if tenures_reorged.is_empty() {
            warn!("Miner is not building off of most recent tenure, but stacks node was unable to return information about the relevant sortitions. Marking miner invalid.");
            return Ok(false);
        }
```

**File:** stacks-signer/src/chainstate/v1.rs (L469-481)
```rust
        // Check that the tenure change's prev_tenure matches the sortition's known parent tenure.
        // This catches block commits with bad parent_block_ptr (e.g., vtxindex=0 exploit).
        let parent_tenure_id = &proposed_by.state().data.parent_tenure_id;
        if &tenure_change.prev_tenure_consensus_hash != parent_tenure_id {
            warn!(
                "Block commit parent tenure mismatch: the block commit's parent_block_ptr does not correspond to the actual parent tenure";
                "committed_parent_tenure" => %parent_tenure_id,
                "actual_parent_tenure" => %tenure_change.prev_tenure_consensus_hash,
                "consensus_hash" => %block.header.consensus_hash,
                "signer_signature_hash" => %block.header.signer_signature_hash(),
            );
            return Err(RejectReason::InvalidParentBlock);
        }
```

**File:** stacks-signer/src/client/stacks_client.rs (L328-357)
```rust
        let mut tenures: VecDeque<TenureForkingInfo> =
            self.get_tenure_forking_info_step(chosen_parent, last_sortition)?;
        if tenures.is_empty() {
            return Ok(vec![]);
        }
        while tenures.back().map(|x| &x.consensus_hash) != Some(chosen_parent) {
            let new_start = tenures.back().ok_or_else(|| {
                ClientError::InvalidResponse(
                    "Should have tenure data in forking info response".into(),
                )
            })?;
            let mut next_results =
                self.get_tenure_forking_info_step(chosen_parent, &new_start.consensus_hash)?;
            if next_results.pop_front().is_none() {
                return Err(ClientError::InvalidResponse(
                    "Could not fetch forking info all the way back to the requested chosen_parent"
                        .into(),
                ));
            }
            if next_results.is_empty() {
                return Err(ClientError::InvalidResponse(
                    "Could not fetch forking info all the way back to the requested chosen_parent"
                        .into(),
                ));
            }
            tenures.extend(next_results);
        }

        Ok(tenures.into_iter().collect())
    }
```
