### Title
Stale own-tenure signature guard lets a signer sign a conflicting sibling block at the same height/tenure - ([File: stacks-signer/src/v0/signer.rs])

### Summary
`handle_block_pre_commit`'s own-tenure conflict guard only vetoes a *stale* previously-signed block in the same tenure when the node's tenure tip has *already* reached the proposed height. If the node has not yet observed the earlier signed block (e.g. it never collected enough weight to be pushed/adopted), the guard is bypassed and the signer will put its signature over a second, conflicting block at the same height and tenure once the freshness window elapses. This breaks the "nobody signs two blocks at the same height in the same tenure" invariant the guard exists to enforce.

### Finding Description
Signing happens in `handle_block_pre_commit` (`stacks-signer/src/v0/signer.rs`, ~lines 1330-1466). Before signing, the signer loads all previously-signed conflicting blocks at ≥ this height via `SignerDb::get_signed_conflicts` [1](#0-0) , then applies two filters:

1. A *fresh-conflict* filter (any tenure): blocks signing if a conflict's `last_endorsed` is newer than `freshness_cutoff` **and** `conflict_still_blocks` returns true [2](#0-1) .
2. A *stale own-tenure* filter: for any remaining conflict whose `consensus_hash` equals the proposed block's tenure, the signer asks the node for `get_tenure_tip` of that tenure and only refuses to sign if `tip_height >= chain_length` of the new proposal [3](#0-2) .

The second filter is the *only* remaining guard against a same-tenure duplicate once a conflict has gone stale (`last_endorsed <= freshness_cutoff`), since the proposal-time `DuplicateBlockFound` check in `check_proposal` runs only once, at proposal arrival, and is never re-run at pre-commit time (documented explicitly in `docs/signer-flows.md`, section 5) [4](#0-3) . But this filter treats "the node hasn't yet reached that height in this tenure" as proof that it is safe to sign the replacement — it does not distinguish "the earlier signed block never got pushed to the node" from "the node is merely lagging." If the earlier block `B1` (which this signer already signed, `signed_self`/`signed_group` set) never accumulated enough total signer weight to be assembled and pushed to the node, the node's tenure tip stays below `B1`'s height indefinitely. Once `tenure_last_block_proposal_timeout` elapses, `B1`'s conflict record becomes stale, and the own-tenure filter's `tip_height >= chain_length` check is false, so nothing blocks the signer from also signing a second, different block `B2` proposed at the exact same height in the exact same tenure.

This is a genuine equivocation: the same signer now holds a signature over two mutually exclusive blocks (`B1` and `B2`) at the same height in the same tenure. Because `freshness_cutoff` is computed per-signer from its own local clock and its own local `last_endorsed` timestamp, different signers in the set go "stale" on `B1` at different times, so over time the signer set's weight can fragment/re-accumulate across both `B1` and `B2`, each potentially able to gather signatures independently — precisely the double-sign scenario the guard's own documentation says it exists to prevent ("a sibling at the same height therefore keeps blocking, since signing both would be the double-sign this guard exists for") [5](#0-4) , but only for the *fresh* case, not the stale one.

### Impact Explanation
This is a Critical-class break under the given rubric: a signer signing a conflicting block. A single miner (with normal signer-set participation, not requiring a colluding majority) can produce this by proposing a block that gets locally accepted/signed by a signer but stalls before reaching global acceptance (e.g. the remaining weight is withheld, delayed, or rejected by other signers for unrelated reasons), waiting out `tenure_last_block_proposal_timeout`, and then re-proposing a different transaction set at the same height in the same tenure. The signer's own conflict guard no longer blocks the second, competing block once staleness sets in and the node's tenure tip hasn't advanced, so the signer signs both.

### Likelihood Explanation
No colluding majority of signers is needed — only ordinary block proposal/gossip behavior plus a delay by the miner exceeding `tenure_last_block_proposal_timeout` (default proposal timing config), which a miner fully controls by choosing when to re-propose. The precondition ("`B1` was signed by this signer but never reached the node") can occur naturally whenever the initial proposal's assembled signature set doesn't get pushed in time (network conditions, other signers slow/offline, timeouts), which is not an unusual runtime condition.

### Recommendation
Do not treat "the node hasn't yet reached this tenure's height" as proof of safety for a *stale* own-tenure conflict. Distinguish, as `conflict_still_blocks` already does for the fresh-conflict case, between a sibling at the *same* height (which must keep blocking regardless of staleness, since signing it is exactly a double-sign) and a genuinely higher/superseded block (which is a legitimate reorg-to-restart case). Concretely, the own-tenure filter at `stacks-signer/src/v0/signer.rs:1432-1457` should refuse to sign whenever an un-superseded, un-globally-accepted, same-height conflict exists in the same tenure, independent of the `last_endorsed` freshness cutoff, mirroring the sibling-height logic already documented for `conflict_still_blocks`.

### Proof of Concept
1. Miner proposes block `B1` at height `H` in tenure `T` with tx set `X`. Signer `S` (and enough of the signer set) pre-commit and `S` reaches the pre-commit threshold; `S` signs `B1` (`mark_locally_accepted`, `signed_self`/`signed_group` set) via `handle_block_pre_commit` [6](#0-5) .
2. `B1` never accumulates enough total weight to be assembled and pushed to the stacks-node (e.g. some signers reject it for an unrelated, non-consensus-fatal reason, or are offline) — the node's tenure tip for `T` stays below `H`.
3. Miner waits longer than `tenure_last_block_proposal_timeout`, so `B1`'s `last_endorsed` timestamp becomes stale for signer `S` (`freshness_cutoff` in `handle_block_pre_commit` moves past it) [7](#0-6) .
4. Miner proposes a different block `B2` at the same height `H` in the same tenure `T` with a different tx set `Y` (e.g. omitting/reordering transactions). `S` validates and pre-commits it.
5. In `handle_block_pre_commit` for `B2`, the fresh-conflict filter no longer matches `B1` (stale). The own-tenure filter finds the `T`-tenure conflict `B1`, queries `get_tenure_tip(T)`; since the node still has not adopted `B1`, `tip_height < H`, so the check at lines 1432-1457 does not return/refuse [3](#0-2) .
6. `S` proceeds to sign `B2` as well, producing signatures over two conflicting blocks at height `H` in tenure `T` — an equivocation that other parts of the codebase (`reorg.rs` test at lines 224-234) explicitly assert must never happen [8](#0-7) .

Note: I was not able to execute this scenario against a live/test cluster within this session (no filesystem/terminal access here), so this is a static-analysis-based finding derived from reading the guard logic and its own documentation of intended behavior; a Devin session with repo/test access would be needed to actually reproduce the double-sign in an integration test (e.g. adapting `stacks-node/src/tests/signer/v0/reorg.rs`).

### Citations

**File:** stacks-signer/src/signerdb.rs (L1606-1625)
```rust
    pub fn get_signed_conflicts(
        &self,
        height: u64,
        excluded_signer_signature_hash: &Sha512Trunc256Sum,
    ) -> Result<Vec<SignedConflictInfo>, DBError> {
        let query = "SELECT b.consensus_hash, b.signer_signature_hash, b.stacks_height, b.state,
                MAX(COALESCE(b.signed_self, 0), COALESCE(b.signed_group, 0)) AS last_endorsed,
                st.superseded_by_consensus_hash, st.superseded_by_burn_block_hash
            FROM blocks b
            LEFT JOIN superseded_tenures st ON st.consensus_hash = b.consensus_hash
            WHERE (b.signed_self IS NOT NULL OR b.signed_group IS NOT NULL)
                AND b.stacks_height >= ?1
                AND b.signer_signature_hash != ?2
            ORDER BY b.stacks_height DESC";
        let args = params![
            u64_to_sql(height)?,
            excluded_signer_signature_hash.to_string(),
        ];
        query_rows(&self.db, query, args)
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

**File:** stacks-signer/src/v0/signer.rs (L1466-1478)
```rust
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

**File:** docs/signer-flows.md (L314-320)
```markdown
   - **it does not, and the block was never globally accepted** — a block is
     not handed to the node until the whole signer set has signed it, so this
     may mean "not yet seen" rather than "dead". A sibling at the same height
     therefore keeps blocking, since signing both would be the double-sign this
     guard exists for; a block _above_ the proposal does not, because it is no
     sibling and abandoning an unconfirmed block to restart beneath it is a
     reorg, not an equivocation.
```

**File:** stacks-node/src/tests/signer/v0/reorg.rs (L224-234)
```rust
    // No signer should have put a signature over block N': it is a sibling of
    // the already-signed block N, so accepting it would be a double-sign.
    for (_chunk, message) in get_stackerdb_signer_messages() {
        if let SignerMessage::BlockResponse(BlockResponse::Accepted(accepted)) = message {
            assert_ne!(
                accepted.signer_signature_hash,
                block_proposal_n_prime.header.signer_signature_hash(),
                "A signer accepted block N', which conflicts with the already-signed block N"
            );
        }
    }
```
