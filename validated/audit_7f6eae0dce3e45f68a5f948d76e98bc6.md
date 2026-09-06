Found it — this is the direct analog of the report's bug class: a check that is supposed to *block* an unsafe action is fail-open when the guarding data is unavailable, exactly like the liquidation check that silently omits enforcement for a `collateralId`/`debtId` whose `chainId` isn't configured.

### Title
Own-tenure conflict guard fails open when the node is unreachable, allowing a signer to sign a conflicting/duplicate block at the pre-commit threshold - (File: `stacks-signer/src/v0/signer.rs`)

### Summary
In the pre-commit-to-signature path, `Signer::handle_block_pre_commit` (around [1](#0-0) ) checks whether the proposed block's own tenure already has a conflicting signed block at or above the proposed height by querying the node via `stacks_client.get_tenure_tip`. If that node call fails (`Err(e)`) for any reason — timeout, connectivity issue, node busy — the code logs a warning and simply falls through, treating the tenure as "unconfirmed" and proceeding to sign the block, instead of refusing to sign (as documented in `docs/signer-flows.md` sections 5 and 7).

### Finding Description
The guard's purpose is to prevent the signer from placing a second signature on a block that conflicts with (duplicates or is superseded by) an already-signed block in the same tenure, once the pre-commit threshold is reached — the last line of defense against a double-sign, since `check_proposal`'s `DuplicateBlockFound` check runs only at proposal time and is never re-run.

The logic:
```rust
if conflicts.iter().any(|conflict| {
    conflict.consensus_hash == block_info.block.header.consensus_hash
        && !self.reorg_permit_stands(stacks_client, conflict)
}) {
    match stacks_client.get_tenure_tip(&block_info.block.header.consensus_hash) {
        Ok(tip) => { /* refuse to sign if tip_height >= proposed height */ }
        Err(e) => {
            warn!("... Treating the tenure as unconfirmed.");
            // falls through — no refusal
        }
    }
}
```
When the node answers, the guard correctly blocks signing if the tenure's tip is already at/above the proposed height. But when the node call errors (any `Err`, not just a proven-absent 404 as elsewhere in the same file), the code assumes "unconfirmed" and lets the signature go out. This is the equivalent of the liquidation contract's `chainId[collateralId] == ""` check: when the necessary configuration/data (the node's answer) is missing, the code should conservatively withhold the action (refuse to sign), but instead it silently skips the check and allows the unsafe action (signing) to proceed — an inverted, fail-open version of the same "missing data disables the safety check" bug class.

This directly contradicts the design principle stated in the same file's own documentation: "Whenever the node cannot be asked, the conflict keeps blocking: that only delays the replacement until the signature goes stale, whereas wrongly signing cannot be taken back" (`docs/signer-flows.md` around line 329). That principle is correctly implemented in `conflict_still_blocks` (used for cross-tenure/stale conflicts, see [2](#0-1) , where every `Err` branch `return`s `true` = keep blocking) but it is inverted in the own-tenure branch at line 1449-1455, where an `Err` falls through to allow signing.

### Impact Explanation
This breaks the "one-per-height" / non-conflicting-signature equality: a single miner (or a byzantine/faulty node interaction) can cause a signer to place a signature on a second, conflicting block in the same tenure at the pre-commit threshold whenever the signer's connection to its own Stacks node is momentarily interrupted (timeout, restart, network blip) at exactly the query point. Since a signature is a "bearer instrument" that can be aggregated toward the 70% threshold at any later point, this signer's stray signature on a superseded/duplicate block can contribute to an invalid/conflicting block reaching consensus alongside — or instead of — the block that was already signed. This matches the report's Critical impact category: a signer signing a conflicting block due to a check that fails open when configuration/data is unavailable.

### Likelihood Explanation
Triggering requires only a transient RPC failure between the signer and its own local Stacks node (a `get_tenure_tip` request failing for any reason — timeout, 5xx, connection reset) at the moment the pre-commit threshold is reached for a conflicting block in the same tenure. This is a routine operational condition (node under load, restart, brief network hiccup) rather than requiring a majority of signers or another party's key, making it a plausible single-signer/gossip-triggerable condition, consistent with the required "one-slot miner (plus gossip)" scope.

### Recommendation
Make the `Err` branch of the own-tenure `get_tenure_tip` check treat a lookup failure the same as `conflict_still_blocks` does: keep the conflict blocking (refuse to sign) rather than falling through to "treat the tenure as unconfirmed." Only a proven-negative response (e.g., a 404 confirming the tenure has no blocks) should be permitted to clear the guard; any other error should preserve the refusal, matching the stated design invariant that node-unreachable must never resolve in favor of signing.

### Proof of Concept
1. Two blocks, A and B, are proposed for the same tenure at the same/overlapping height; A is signed first (`GloballyAccepted`/`LocallyAccepted`), consistent with the existing `run_sibling_scenario` test harness in [3](#0-2) .
2. B reaches the pre-commit threshold. `handle_block_pre_commit` detects the conflict with A in the same tenure (`conflict.consensus_hash == block_info.block.header.consensus_hash`).
3. At this exact moment, simulate the signer's `stacks_client.get_tenure_tip` call failing (e.g., stop/delay the mock node's HTTP listener as done elsewhere in `tests.rs`, or inject a network error).
4. Observe `Err(e)` branch is taken at [4](#0-3) , logging "Treating the tenure as unconfirmed," and execution falls through to `mark_locally_accepted` / `handle_block_signature` / `send_block_response`, producing a signature over B despite A already being signed in the same tenure — the exact double-sign scenario the guard exists to prevent.

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

**File:** stacks-signer/src/v0/tests.rs (L770-826)
```rust
    #[test]
    fn signer_refuses_to_sign_second_sibling_tenure_start() {
        // Pin the fresh window far beyond the test's runtime so the guard can only take the
        // fresh branch; the stale branch is covered by the tests below.
        let (info_a, info_b, _) = run_sibling_scenario(Duration::from_secs(100_000), false, None);
        assert_a_signed(&info_a);
        // B is still pre-committed (the sibling is allowed to reach pre-commit), but the signer
        // must refuse to place a second signature on a conflicting same-height block in this
        // tenure while its signature on A is fresh.
        assert_eq!(
            info_b.state,
            BlockState::PreCommitted,
            "block B should be pre-committed but not promoted, got: {}",
            info_b.state
        );
        assert!(
            info_b.signed_self.is_none(),
            "block B must NOT be signed: the signer already signed a conflicting sibling in this tenure"
        );
    }

    #[test]
    fn stale_sibling_still_refused_when_canonical_tip_at_height() {
        // A zero timeout makes A's signature stale immediately, but the node reports A as the
        // canonical tip at the same height, so the replacement must still be refused.
        let (info_a, info_b, _) = run_sibling_scenario(Duration::ZERO, true, None);
        assert_a_signed(&info_a);
        assert_eq!(
            info_b.state,
            BlockState::PreCommitted,
            "block B should be pre-committed but not promoted, got: {}",
            info_b.state
        );
        assert!(
            info_b.signed_self.is_none(),
            "block B must NOT be signed: the conflicting sibling is canonical at this height"
        );
    }

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
