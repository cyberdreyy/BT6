### Title
Transient node-connectivity failure lets a signer sign a conflicting sibling block in its own tenure - ([File: stacks-signer/src/v0/signer.rs])

### Summary
`handle_block_pre_commit` in `stacks-signer/src/v0/signer.rs` re-checks whether a block that just crossed the pre-commit threshold conflicts with a block the signer already signed. For **fresh** conflicts, the check `conflict_still_blocks` is fail-closed: any error contacting the stacks-node (unreachable, timeout, etc.) leaves the conflict "in place" and the signer refuses to sign [1](#0-0) . But for **stale** same-tenure conflicts, the fallback re-check calls `get_tenure_tip` and, if that call fails for *any* reason (including a transient RPC error to the signer's own node), the code explicitly treats the tenure as "unconfirmed" and falls through to signing the new, conflicting block anyway [2](#0-1) .

### Finding Description
The signer's core safety invariant is "never sign two conflicting blocks at the same height in the same tenure" (an equivocation guard). This is enforced in `handle_block_pre_commit`:

1. First, any **fresh** conflict (endorsed within `tenure_last_block_proposal_timeout`) is checked via `conflict_still_blocks`, which is deliberately fail-closed: on any node-query failure it returns `true` (keep blocking) [3](#0-2) .
2. Once a same-tenure conflict has gone **stale** (past `tenure_last_block_proposal_timeout`, a purely local, wall-clock-driven timeout unrelated to actual chain state), the code performs a second, independent live check via `get_tenure_tip` to decide whether the old block is still the canonical tip (still blocks) or not (safe to sign the replacement) [4](#0-3) .
3. If that `get_tenure_tip` call errors — including a transient network blip, node restart, node-under-load timeout, or any other connectivity failure with the signer's own stacks-node — the code logs "Treating the tenure as unconfirmed" and does **not** refuse to sign, instead falling through to `mark_locally_accepted` and broadcasting a signature [5](#0-4) .

This asymmetry is the direct analog of the reported bug class: an external dependency's transient unavailability (there: an L2 sequencer; here: the signer's local stacks-node RPC) is supposed to trigger a conservative "wait/hold" fallback, but instead the specific code path silently fails open and performs the very state-changing action (a signature) the guard exists to prevent. The developer's own comments acknowledge the asymmetry: elsewhere in the same file, "If we have no saved burn block, or the node is unreachable, the conflict keeps blocking" is stated as the general safety principle [6](#0-5) , yet the own-tenure re-check at lines 1432–1456 deliberately deviates from that principle for stale conflicts.

Concretely: a miner proposes block A at height H in tenure T; the local signer signs A. Later (after `tenure_last_block_proposal_timeout` has elapsed with no further confirmation), the same miner proposes a competing block B at height H in the same tenure T (e.g., because A never reached the network / node in time). If, at the exact moment B crosses the pre-commit threshold, this signer's `get_tenure_tip` RPC call to its own node fails for any reason (timeout, node restart, resource exhaustion, network hiccup — none of which require a majority of signers, another signer's key, or `auth_token`/local access), the signer signs B despite already having signed A, violating the one-signature-per-height/tenure invariant for that signer.

### Impact Explanation
This is a Critical-class issue per the stated rubric: "a signer signing an invalid, non-canonical, or conflicting block." The equivocation guard the signer set relies on to prevent contributing towards two competing blocks at the same height is bypassed purely by transient connectivity noise, not by any adversarial majority. If enough signers experience the same class of transient node hiccup around the same re-proposal window (plausible, since node load / restarts / network conditions are correlated across an operator's infra or across the network during congestion), both A and B could independently accumulate signer weight toward the 70% threshold, creating the conditions for a chain fork/equivocation at the consensus layer — exactly the kind of "conflicting signature" outcome the guard is designed to prevent.

### Likelihood Explanation
Likelihood is moderate: it requires (a) a miner (a single actor, "one-slot miner") to produce a genuine same-height sibling in its own tenure — a normal occurrence in reorg/timeout scenarios already tested elsewhere in this codebase (see `stale_sibling_replaced_when_canonical_tip_below` in `stacks-signer/src/v0/tests.rs`) — combined with (b) a transient RPC failure between the signer and its own stacks-node at the precise moment of re-evaluation. Node RPC hiccups (timeouts, restarts, backpressure) are common operational events, not adversarial actions, making this readily triggerable without any privileged access or majority collusion.

### Recommendation
Make the own-tenure stale-conflict re-check fail-closed, symmetric with `conflict_still_blocks`: on `get_tenure_tip` error, refuse to sign (or retry later) rather than treating the tenure as "unconfirmed" and signing. If falling through is intentional to preserve liveness, it should require corroborating evidence (e.g., a successful negative answer from the node) rather than an outright RPC failure, and/or should be bounded by retries/backoff before defaulting to sign.

### Proof of Concept
1. Signer signs block A (tenure T, height H) via `handle_block_pre_commit`, recording a `SignedConflictInfo` entry.
2. Wait longer than `tenure_last_block_proposal_timeout` so A's endorsement is stale (`conflict.last_endorsed <= freshness_cutoff`), removing it from the fresh-conflict `conflict_still_blocks` gate at lines 1403–1421.
3. Miner proposes conflicting block B at the same height H in the same tenure T; B reaches the pre-commit threshold and `handle_block_pre_commit` runs for it.
4. At line 1436, `stacks_client.get_tenure_tip(...)` returns an `Err` (simulate via a stalled/unreachable node connection, as already exercised by existing unit-test infrastructure using `MockServerClient`/`write_response` patterns in `stacks-signer/src/v0/tests.rs` and `stacks-signer/src/tests/signer_state.rs`).
5. Execution falls into the `Err(e)` branch at lines 1449–1456 ("Treating the tenure as unconfirmed"), skips the refusal, and proceeds to `mark_locally_accepted` for B at line 1467 — the signer has now signed two conflicting blocks (A and B) at the same height in the same tenure.

### Citations

**File:** stacks-signer/src/v0/signer.rs (L1134-1136)
```rust
    /// If we have no saved burn block, or the node is unreachable, the conflict keeps blocking.
    /// That only delays the replacement until our signature goes stale, whereas wrongly signing
    /// cannot be taken back.
```

**File:** stacks-signer/src/v0/signer.rs (L1176-1206)
```rust
                        Err(e) => {
                            warn!("{self}: Failed to fetch the node's burnchain tip while checking a conflicting block's tenure: {e:?}. Leaving the conflict in place.";
                                "conflicting_consensus_hash" => %conflict.consensus_hash,
                            );
                            return true;
                        }
                    }
                }
                Err(e) => {
                    warn!("{self}: Failed to check whether a conflicting block's tenure is still canonical: {e:?}. Leaving the conflict in place.";
                        "conflicting_consensus_hash" => %conflict.consensus_hash,
                    );
                    return true;
                }
            }
        }
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

**File:** stacks-signer/src/v0/signer.rs (L1432-1465)
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
        if !conflicts.is_empty() {
            info!(
                "{self}: Reached the pre-commit threshold for a block that conflicts with previously signed or accepted blocks, but none of those conflicts still blocks it. Signing the replacement.";
                "signer_signature_hash" => %block_hash,
                "block_height" => block_info.block.header.chain_length,
                "num_conflicts" => conflicts.len(),
            );
        }
```
