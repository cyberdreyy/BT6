### Title
Node-unreachable fallback in `handle_block_pre_commit`'s stale same-tenure conflict check lets a signer place a second (equivocating) signature on a conflicting block - (File: stacks-signer/src/v0/signer.rs)

### Summary
`handle_block_pre_commit` guards against a signer double-signing two conflicting blocks in the same tenure by asking the stacks-node whether the tenure's canonical tip already covers the earlier signed block. When that RPC call fails (`get_tenure_tip` returns `Err`), the code does not refuse to sign - it logs a warning, treats the tenure as "unconfirmed", and falls through to sign the new, conflicting block anyway. This mirrors the SSRF report's root cause class: a security-relevant decision is made by blindly trusting the *absence* of a trustworthy external answer instead of failing closed.

### Finding Description
The pre-commit → signature path in `stacks-signer/src/v0/signer.rs::handle_block_pre_commit` re-validates chainstate consistency and then checks for signed conflicts before producing a signature [1](#0-0) .

For conflicts that are still *fresh* (within `tenure_last_block_proposal_timeout`), `conflict_still_blocks` fails closed: on any node-connectivity error it returns `true` (keep blocking) [2](#0-1) .

But for a *stale* conflict inside the block's **own tenure**, a second, separate check runs at signing time to decide whether the tenure's canonical tip already covers that earlier signed block: [3](#0-2) 

If `get_tenure_tip` errors (timeout, temporary node unavailability, node busy validating a large/expensive proposal, restart, etc.), the `Err` arm only warns and does **not** `return`; execution falls through to: [4](#0-3) 

and the signer proceeds to `mark_locally_accepted` / broadcast its acceptance signature over block B, even though block A (already signed by this signer, `signed_self`, in the very same tenure) may still be the real canonical tip. This is explicitly documented as an intentional fail-open design choice ("For the own-tenure question below, an unreachable node is instead treated as unconfirmed and the signature goes out") [5](#0-4) , contrasted with every other conflict-freshness question in the same function, which fails closed when the node cannot be asked [6](#0-5) .

Reaching this branch requires only:
1. A tenure whose tip was signed by this signer once (`A`), now older than `tenure_last_block_proposal_timeout` (default 30s) since the signature was placed - i.e. the earlier conflict has gone "stale" per the freshness cutoff [7](#0-6) .
2. A second, conflicting block `B` proposed in the same tenure that reaches the ≥70% pre-commit weight threshold (ordinary gossip among signers - no majority-of-signers compromise or extra keys required, since pre-commits merely need to be relayed/observed).
3. The signer's own RPC call to its stacks-node for `get_tenure_tip` failing at that exact moment (a transient node hiccup, restart, or resource contention while validating a heavy proposal - not necessarily flooding).

None of this needs a majority of signers, another signer's key, or the auth_token; it only needs the ordinary block-proposal/pre-commit gossip that any one-slot miner and the existing signer set already produce, plus a single unlucky/opportunistically-timed RPC failure.

### Impact Explanation
This breaks the "signed vs validated" equality that the equivocation guard exists to protect: it allows this signer to place its cryptographic signature over two conflicting blocks at the same height in the same tenure. If the earlier block A was in fact the real canonical tip, this is a genuine signature over a conflicting block by a functioning signer - matching the report's Critical impact category ("a signer signing an invalid, non-canonical, or conflicting block"). It weakens the safety margin the 70%-threshold signature scheme depends on, since equivocating signatures from otherwise-honest signers reduce the effective distinctness of the signer set's votes across forks.

### Likelihood Explanation
Medium-low but realistic: it requires (a) the earlier signed block to first go stale (default 30s window), (b) a genuinely conflicting re-proposal in the same tenure crossing the 70% pre-commit threshold, and (c) the signer-to-node RPC call failing specifically at that moment. Node RPC hiccups (restarts, GC pauses, resource contention from block validation load, or transient network partitions between signer and its own node) are not exotic events in a distributed deployment, and the code path is exercised on every stale same-tenure conflict resolution, so the window recurs naturally during normal reorg-recovery flows documented and tested elsewhere in this file (e.g. `stale_sibling_replaced_when_canonical_tip_below` in `stacks-signer/src/v0/tests.rs`) [8](#0-7) .

### Recommendation
Make the own-tenure stale-conflict check fail closed like `conflict_still_blocks` does: on `get_tenure_tip` error, refuse to sign and retry on the next pre-commit re-evaluation instead of falling through to `mark_locally_accepted`. If liveness is a concern, bound the closed-fail retries with the same freshness/staleness timers already used elsewhere in this function rather than defaulting to "assume clear to sign" on the very check meant to prevent equivocation.

### Proof of Concept
1. Signer signs block A (tenure T, height h) locally (`signed_self` set) via the normal pre-commit → threshold → sign flow.
2. Wait past `tenure_last_block_proposal_timeout` (default 30s) so A's signature is "stale" per `freshness_cutoff` in `handle_block_pre_commit` [9](#0-8) .
3. Miner (or gossip) proposes conflicting block B, same tenure T, height h. B is validated and pre-commits from the signer set accumulate to ≥70% weight, triggering `handle_block_pre_commit` for B on this signer.
4. `check_block_against_signer_db_state` passes (no sticky rejection), and the first fresh-conflict loop finds no fresh, live conflict (A is stale) so it does not return early.
5. The stale same-tenure branch fires: `stacks_client.get_tenure_tip(&T)` returns `Err` (simulate via a stalled/slow validation endpoint or node restart, similar to `TEST_VALIDATE_STALL` used elsewhere in the test-suite, e.g. `stacks-node/src/tests/signer/v0/reorg.rs`).
6. The `Err` arm only warns; code falls through and the signer marks B `LocallyAccepted` and broadcasts its signature over B - a second signature from the same signer over a conflicting block in tenure T, despite A potentially still being the canonical tip.

### Citations

**File:** stacks-signer/src/v0/signer.rs (L1176-1204)
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

**File:** stacks-signer/src/v0/signer.rs (L1393-1421)
```rust
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

**File:** stacks-signer/src/v0/signer.rs (L1458-1471)
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
        if let Err(e) = block_info.mark_locally_accepted(false) {
            if !block_info.has_reached_consensus() {
                warn!("{self}: Failed to mark block as locally accepted: {e:?}",);
            }
        }
```

**File:** docs/signer-flows.md (L299-320)
```markdown
1. **Is the conflict's tenure still on the canonical burn chain?** The signer
   saved the tenure's burn block when it arrived (section 8), and
   `/v3/sortitions/burn/:hash` resolves it against the node's canonical fork. A
   404 means a burnchain fork orphaned the tenure: everything it built is void,
   and the conflict is dead no matter what state its block is in. But a 404
   alone is not proof — the same endpoint 404s a perfectly canonical burn block
   when the node is still catching up (and on internal data misses), so it is
   only trusted once the node's burnchain tip (`get_peer_info`) is at or past
   the stored burn block's height; below that, the conflict keeps blocking and
   the next evaluation retries. If the burn block was never saved (a restart,
   or the tenure predates us), the question is skipped rather than guessed.
2. **Does the node's canonical Stacks chain still reach the block itself?**
   - **it does** — real chain state; keep blocking;
   - **it does not, and the block was globally accepted** — the node once _did_
     have it, so a reorg moved past it. That is proof it is dead;
   - **it does not, and the block was never globally accepted** — a block is
     not handed to the node until the whole signer set has signed it, so this
     may mean "not yet seen" rather than "dead". A sibling at the same height
     therefore keeps blocking, since signing both would be the double-sign this
     guard exists for; a block _above_ the proposal does not, because it is no
     sibling and abandoning an unconfirmed block to restart beneath it is a
     reorg, not an equivocation.
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

**File:** stacks-signer/src/config.rs (L43-43)
```rust
const DEFAULT_TENURE_LAST_BLOCK_PROPOSAL_TIMEOUT_SECS: u64 = 30;
```

**File:** stacks-signer/src/v0/tests.rs (L809-826)
```rust
    #[test]
    fn stale_sibling_replaced_when_canonical_tip_below() {
        // A zero timeout makes A's signature stale immediately, and the node's canonical tip
        // is still the parent (height 9): A failed to be confirmed, so the signer must sign
        // the replacement rather than stall the tenure (the reorg-recovery case).
        let (info_a, info_b, _) = run_sibling_scenario(Duration::ZERO, false, None);
        assert_a_signed(&info_a);
        assert_eq!(
            info_b.state,
            BlockState::LocallyAccepted,
            "block B should be signed: the conflicting sibling timed out and is not canonical, got: {}",
            info_b.state
        );
        assert!(
            info_b.signed_self.is_some(),
            "block B should carry our signature after the conflict timed out unconfirmed"
        );
    }
```
