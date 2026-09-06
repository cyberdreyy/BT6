### Title
Too-short `tenure_last_block_proposal_timeout` lets a signer be induced into signing two conflicting sibling blocks at the same height - ([File: stacks-signer/src/v0/signer.rs])

### Summary
`stacks-signer`'s pre-commit-to-signature path guards against equivocation (signing two different blocks at the same chain height) only while the earlier signature is "fresh," where freshness is a single fixed wall-clock window, `tenure_last_block_proposal_timeout` (default 30s). Once that window elapses, the guard is dropped and, for a same-tenure conflict, is replaced only by a live query of the node's current tenure tip. If the first (conflicting) block was signed locally but never reached the network-wide signature threshold needed to be pushed to the node, the node's tip never advances, the fallback check passes, and the signer signs the second, conflicting block — producing two valid signer signatures over siblings at the same height from a single signer. This is the same bug class as the reported "TWAP duration too short" finding: a security-critical duration is set too low relative to the real-world time an attacker fully controls, converting a timing parameter into an exploitable safety gap.

### Finding Description
The equivocation guard lives in the pre-commit → sign path: [1](#0-0) 

`get_signed_conflicts` returns any other block already signed (locally or by the group) at the same or higher chain height. A conflict only blocks signing if it is "fresh":

```
conflict.last_endorsed > freshness_cutoff
    && !self.reorg_permit_stands(...)
    && self.conflict_still_blocks(...)
```

`freshness_cutoff = now - tenure_last_block_proposal_timeout` [2](#0-1) 

Once `last_endorsed` falls outside this window (default 30 seconds, per the shipped sample config), the "fresh" gate no longer blocks the new signature. The code path that runs *instead*, for a stale conflict, only inspects conflicts sharing the **same** `consensus_hash` (same tenure) and asks the node for its current tenure tip: [3](#0-2) 

If the node's tenure tip has not advanced to (or past) the proposed height — which is exactly the case when the earlier conflicting block never collected enough signature weight to be broadcast to the node — this fallback check also passes, and the code falls through to: [4](#0-3) 

which unconditionally signs and broadcasts acceptance of the new block, even though this exact signer already signed a different block at the same height moments earlier (just past the 30s freshness window).

The comment above this logic explicitly documents the design intent that used to gate this: "a signature must not be superseded while it's still 'fresh'" [5](#0-4) , and existing unit tests confirm the exact edge case is reachable and expected to flip from "refuse" to "sign" purely as a function of the timeout value, not of any node-verified finality: [6](#0-5) 

Crucially, the value governing this window is attacker-observable and its lower bound is not enforced anywhere beyond a config default; a block proposer (a single miner slot) fully controls the pacing of its own proposals and can trivially wait out a 30-second window with no need for a majority of signers, a stolen key, or any node-consensus defect — mirroring the Uniswap V3 TWAP report's core lesson that a duration parameter set too low relative to what an attacker can freely wait out converts a "safe by construction" invariant into a race the attacker wins with patience alone.

### Impact Explanation
This breaks the "a signer never signs two conflicting blocks at the same height" invariant — explicitly listed as a Critical-severity outcome (a signer signing a conflicting block). If a miner (or colluding relay) partitions block-A's proposal so it collects less than the 70% signature threshold, waits past `tenure_last_block_proposal_timeout`, then proposes conflicting block B at the same height in the same tenure, every signer that signed A can independently be led to also sign B. If, over time, enough signers (potentially all of them) sign both A and B, both blocks can independently cross the 70% weight threshold required for global acceptance — producing two blocks, each carrying a threshold-satisfying aggregate of valid signer signatures at the same chain height. This is a genuine equivocation/fork condition at the protocol's core safety property, not merely a liveness nuisance.

### Likelihood Explanation
Likelihood is low-to-moderate: it requires a miner (or an actor able to control what block proposals different signers receive, e.g., via selective StackerDB gossip/timing) to prevent block A from reaching the 70% acceptance threshold on the first pass, and then simply wait out a fixed, publicly known 30-second window before proposing B. No majority of signers, stolen keys, or node bugs are needed — only ordinary control over proposal timing and content, which a block-producing miner inherently has. The main obstacle for an attacker is orchestrating a genuine signature-weight split during the first proposal round (e.g. via network delay or selective retraction), which is plausible but not guaranteed on a healthy network — analogous to the original report's caveat that the attack "requires limited liquidity" for the TWAP case.

### Recommendation
- Do not let a locally-issued signature over one block ever become "non-blocking" for a conflicting sibling at the same height purely due to a wall-clock timeout when the conflicting block was never confirmed dead by the node (i.e., require `conflict_still_blocks`/node-tip evidence to independently confirm the earlier block cannot still become canonical, rather than only checking it for same-tenure conflicts after freshness expires).
- Track, per signer, whether it has already signed *any* block at a given height/tenure combination and require an explicit, node-verified proof of non-canonicity (e.g., tenure tip strictly above the conflicting block, or a `NewBlock`/rejection event referencing that specific hash) before allowing a second signature at the same height, regardless of how much wall-clock time has passed.
- Consider raising `tenure_last_block_proposal_timeout`'s default and/or decoupling the "freshness" gate from the fallback path so the fallback is only skipped when the earlier proposal is provably abandoned (not simply "timed out").

### Proof of Concept
Conceptual sequence (cannot be executed without a running multi-signer testnet, but each step is directly supported by the code cited above):
1. Miner proposes block A at height H in tenure T. A subset of signers (e.g., 40% weight) sign A before the miner stops re-broadcasting it (never reaching the 70% `mark_locally_accepted`/broadcast threshold documented in `docs/signer-flows.md` lines 361-388), so A never reaches the stacks node.
2. Miner waits `tenure_last_block_proposal_timeout` (default 30s, `stacks-signer/src/config.rs` line 180 and `sample/conf/signer/mainnet-signer-conf.toml` line 142) plus a small margin.
3. Miner proposes conflicting block B at the same height H, same tenure T (e.g., different transaction set).
4. Every signer that signed A now evaluates B via `get_signed_conflicts` in `stacks-signer/src/v0/signer.rs` lines 1383-1421: `conflict.last_endorsed` (time A was signed) is now older than `freshness_cutoff`, so the fresh-conflict check does not block.
5. The same-tenure fallback (lines 1432-1457) queries `stacks_client.get_tenure_tip`; since A never reached the node, `tip_height < H`, so this check also does not block.
6. The signer proceeds to lines 1458-1478, signs B, and broadcasts acceptance — despite having already signed conflicting block A moments earlier.
7. If enough signers repeat this, both A and B can each independently accumulate ≥70% signature weight, producing two conflicting, fully-signed blocks at height H.

### Citations

**File:** stacks-signer/src/v0/signer.rs (L1368-1382)
```rust
        // A pre-commit may be superseded by a competing proposal at the same height (e.g. a
        // re-proposed tenure-start block after the first failed to reach consensus), but a
        // signature must not be superseded while it's still "fresh". A signed block at the
        // same or higher height in ANY tenure is a conflict: two blocks at the same height are
        // siblings no matter which tenure they belong to (e.g. the next tenure's tenure-start
        // block conflicts with the current tenure's block at the same height). Blocks in
        // tenures whose reorg we sanctioned under the reorg-timing rules are excluded, but
        // only while the sortition the permit was granted to is still canonical
        // (`check_parent_tenure_choice` records the permit, `reorg_permit_stands` re-derives
        // its validity from the node); every other question about whether a conflict is
        // still live is derived from the node in `conflict_still_blocks`.
        //
        // Unlike the chainstate check above, a refusal here is "for now" rather than a
        // broadcast rejection: a later pre-commit re-evaluation may still sign the block once
        // the conflicting signature has gone stale.
```

**File:** stacks-signer/src/v0/signer.rs (L1383-1421)
```rust
        let conflicts = match self
            .signer_db
            .get_signed_conflicts(block_info.block.header.chain_length, &block_hash)
        {
            Ok(conflicts) => conflicts,
            Err(e) => {
                warn!("{self}: Failed to query the signed blocks. Refusing to sign block {block_hash}: {e:?}");
                return;
            }
        };
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

**File:** stacks-signer/src/v0/signer.rs (L1423-1457)
```rust
        // No conflict is both fresh and still live. A conflict that no longer matters, i.e.
        // stale, or provably dead per `conflict_still_blocks`, cannot veto on its own. A
        // stale conflict in another tenure in particular no longer speaks for us: whether this
        // block may replace what another tenure built is settled by the chainstate checks above.
        // A stale conflict in this block's own tenure still blocks if the node already has that
        // tenure at or above the proposed height, since the proposal then duplicates state the
        // node has already built on. (The chainstate checks don't cover this for tenure-change
        // blocks: those check the parent tenure instead of their own.)
        // The permit check is deferred to here so that only same-tenure conflicts pay for it.
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

**File:** stacks-signer/src/v0/signer.rs (L1458-1478)
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
        self.signer_db
            .insert_block(&block_info)
            .unwrap_or_else(|e| self.handle_insert_block_error(e));
        let accepted = self.create_block_acceptance(&block_info.block);
        // have to save the signature _after_ the block info
        self.handle_block_signature(stacks_client, sortition_state, &accepted);
        self.send_block_response(&block_info.block, accepted.into());
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
