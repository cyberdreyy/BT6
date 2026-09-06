### Title
Own-tenure double-sign guard fails open on a `get_tenure_tip` API error, allowing a signer to sign a conflicting/duplicate block - (File: `stacks-signer/src/v0/signer.rs`)

### Summary
`handle_block_pre_commit` protects against a signer double-signing two blocks at the same height in the same tenure by asking the stacks-node, via `get_tenure_tip`, whether it already has that tenure at or above the proposed height. When that node RPC call fails for any reason (timeout, non-2xx status, malformed body), the error is only logged; the code does not refuse to sign or retry — it silently falls through the guard and proceeds to sign, exactly mirroring the "blockless API call" bug class: an external call's failure is swallowed instead of bubbling up to block an unsafe action.

### Finding Description
In `handle_block_pre_commit`, once the pre-commit weight threshold is reached, stale (non-fresh) conflicts in the block's own tenure are checked directly against the node instead of through the generic `conflict_still_blocks` helper: [1](#0-0) 

If a same-tenure conflict exists and its reorg permit doesn't stand, the code calls `stacks_client.get_tenure_tip(...)`. On success, it correctly refuses to sign when the node's tip already covers the proposed height. But on `Err(e)`, it only warns and "treats the tenure as unconfirmed" — there is no `return`, so execution falls through to the sign path: [2](#0-1) 

This is explicitly different from the sibling function `conflict_still_blocks`, which is deliberately fail-closed: every `Err` branch there returns `true` ("leaving the conflict in place") so that a node error blocks signing rather than permitting it: [3](#0-2) 

The design intent for the own-tenure branch is documented as an intentional fail-open tradeoff ("an unreachable node is instead treated as unconfirmed and the signature goes out"), but this branch exists specifically to catch the case the rest of the pipeline cannot: `check_proposal`'s `DuplicateBlockFound` check only runs at proposal arrival, so a block crossing the pre-commit threshold minutes later relies solely on this own-tenure guard to prevent signing two competing blocks at the same height in the same tenure. [4](#0-3) [5](#0-4) 

Any transient failure of `get_tenure_tip` (client timeout, 5xx, malformed JSON, etc.) at exactly the moment a stale same-tenure conflict must be checked disables this specific safety check without any retry or backoff, unlike `submit_block_for_validation`/`check_pending_block_validations`, which do properly requeue on failure.

### Impact Explanation
This breaks the "one accepted block per height/tenure" invariant: a signer can sign a second, conflicting block in the same tenure it had already signed/accepted at or above the proposed height, purely because a single RPC call to its own node happened to fail. This is a Critical-class break per the rules — "a signer signing an invalid, non-canonical, or conflicting block" — since it directly produces conflicting signatures from an otherwise honest signer, without requiring a majority of signers, another signer's key, or any adversarial control beyond triggering (or waiting for) a transient node/API hiccup.

### Likelihood Explanation
The precondition is narrow but realistic: a stale (post-freshness-cutoff) same-tenure conflict must exist (miner re-proposes at the same height after an earlier proposal was already signed), and `get_tenure_tip` must fail at that specific check. Given that `get_tenure_tip` is a normal HTTP call to the signer's local stacks-node (subject to timeouts, node restarts, temporary overload, or being asked to reopen state), transient failures are a routine occurrence in this codebase — the existence of retry logic in `submit_block_for_validation`/`check_pending_block_validations` for other stacks-node calls in this same file underscores that such failures are expected in production. No majority collusion or external attacker action is required; a single degraded local node interaction is sufficient to disable this guard at the critical moment.

### Recommendation
Make the own-tenure double-sign check fail closed, consistent with `conflict_still_blocks`: on `Err(e)` from `get_tenure_tip`, treat the conflict as still blocking (refuse to sign and return) rather than falling through to the sign path. If retry is desired instead of an outright refusal, defer/requeue the pre-commit evaluation (similar to `insert_pending_block_validation`) rather than proceeding to sign on an unanswered query.

### Proof of Concept
1. Signer S validates and signs block `B1` at height `h` in tenure `T` (recorded as a `SignedConflictInfo` for tenure `T`, height `h`).
2. Time passes such that `B1`'s conflict entry becomes "stale" (`last_endorsed <= freshness_cutoff`), e.g. through normal timeout/backoff of the freshness window.
3. A different block `B2` is proposed at height `h` in the same tenure `T` (e.g. after a miner re-proposal) and is validated OK; pre-commits accumulate to the 70% threshold, invoking `handle_block_pre_commit` for `B2`.
4. Because `B1`'s conflict is stale, the fresh-conflict guard at `stacks-signer/src/v0/signer.rs:1403` does not trigger; execution reaches the same-tenure stale-conflict block at line 1432.
5. `conflict.consensus_hash == block_info.block.header.consensus_hash` is true (same tenure `T`) and the reorg permit does not stand, so `stacks_client.get_tenure_tip(&T)` is called.
6. Simulate/observe a transient failure of this call (connection reset, 503, timeout, or malformed response) — the node genuinely already has `B1` at height `h`, but the query itself errors out.
7. Per lines 1449-1455, the error is only logged ("Treating the tenure as unconfirmed"); no `return` occurs.
8. Execution falls through to the final signing path and S signs `B2`, producing two signatures over conflicting blocks at height `h` in tenure `T` from the same signer — the exact equivocation this guard exists to prevent.

### Citations

**File:** stacks-signer/src/v0/signer.rs (L1192-1206)
```rust
        let node_reaches_conflict = match stacks_client.get_tenure_tip(&conflict.consensus_hash) {
            Ok(tip) => tip.anchored_header.height() >= conflict.stacks_height,
            // A 404 is an answer, not a failure: the node has no blocks in that tenure at all.
            Err(ClientError::RequestFailure(reqwest::StatusCode::NOT_FOUND)) => false,
            Err(e) => {
                warn!("{self}: Failed to fetch the canonical tip of a conflicting block's tenure: {e:?}. Leaving the conflict in place.";
                    "conflicting_consensus_hash" => %conflict.consensus_hash,
                    "conflicting_block_height" => conflict.stacks_height,
                );
                return true;
            }
        };
        node_reaches_conflict
            || (!conflict.globally_accepted && conflict.stacks_height <= proposed_height)
    }
```

**File:** stacks-signer/src/v0/signer.rs (L1432-1457)
```rust
        if conflicts.iter().any(|conflict| {
            conflict.consensus_hash == block_info.block.header.consensus_hash
                && !self.reorg_permit_stands(stacks_client, conflict)
        }) {
            match stacks_client.get_tenure_tip(&block_info.block.header.consensus_hash) {
                Ok(tip) => {
                    let tip_height = tip.anchored_header.height();
                    if tip_height >= block_info.block.header.chain_length {
                        warn!(
                            "{self}: Reached the pre-commit threshold for a block that conflicts with previously signed or accepted blocks, and the canonical tip of its tenure is already at or above the proposed height. Refusing to sign.";
                            "signer_signature_hash" => %block_hash,
                            "block_height" => block_info.block.header.chain_length,
                            "canonical_tip_height" => tip_height,
                        );
                        return;
                    }
                }
                Err(e) => {
                    warn!(
                        "{self}: Failed to fetch the canonical tip of the proposed block's tenure: {e:?}. Treating the tenure as unconfirmed.";
                        "signer_signature_hash" => %block_hash,
                        "consensus_hash" => %block_info.block.header.consensus_hash,
                    );
                }
            }
        }
```

**File:** stacks-signer/src/v0/signer.rs (L1458-1466)
```rust
        if !conflicts.is_empty() {
            info!(
                "{self}: Reached the pre-commit threshold for a block that conflicts with previously signed or accepted blocks, but none of those conflicts still blocks it. Signing the replacement.";
                "signer_signature_hash" => %block_hash,
                "block_height" => block_info.block.header.chain_length,
                "num_conflicts" => conflicts.len(),
            );
        }
        // It is only considered globally accepted IFF we receive a new block event confirming it OR see the chain tip of the node advance to it.
```

**File:** docs/signer-flows.md (L280-286)
```markdown
- the re-check only ever looks at _one_ tenure (a tenure-change block's parent,
  or any other block's own), so a signed sibling at the same height in a third
  tenure is invisible to it;
- the `DuplicateBlockFound` check that would catch a second block in the same
  tenure lives in `check_proposal` and runs only at proposal arrival, never
  again. A block that crosses the pre-commit threshold minutes later has no
  other guard, which is what the own-tenure branch above covers.
```

**File:** docs/signer-flows.md (L329-341)
```markdown
Whenever the node cannot be asked, the conflict keeps blocking: that only delays
the replacement until the signature goes stale, whereas wrongly signing cannot be
taken back. The one recorded exception is a tenure whose reorg we sanctioned
under the reorg-timing rules (section 8): there the node still serves the
conflict as fully live — replacing it is only legitimate because we permitted it
— so no question asked of the node about the _conflict_ could clear it. Instead
the record carries the permitting tenure's sortition, and `reorg_permit_stands`
asks the node whether that sortition is still canonical: while it is, the
conflict is excluded outright; if a burnchain fork orphaned it, the reorg we
sanctioned can no longer happen and the conflict gets its voice back. A false
404 there needs no tip-height guard — it merely restores a conflict, which at
worst delays the replacement. For the own-tenure question below, an unreachable
node is instead treated as unconfirmed and the signature goes out.
```
