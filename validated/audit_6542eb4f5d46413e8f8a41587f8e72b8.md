### Title
Signer treats a `get_tenure_tip` RPC failure as "unconfirmed" rather than failing closed during the same-tenure conflict re-check before signing - (File: `stacks-signer/src/v0/signer.rs`)

### Summary
The Symfony/HttpFoundation issue (CVE-2015-2309) trusted a client-controlled value once one required verification step happened to be inconclusive, instead of treating the ambiguous case as untrusted. The stacks-signer has the same "assume trust when we can't verify" pattern in the pre-commit-threshold same-tenure conflict re-check performed immediately before a signature is produced: when the stacks-node cannot answer `get_tenure_tip`, the code treats the still-live conflicting block as if it were confirmed absent, and proceeds to sign a second, conflicting block in the same tenure at the same or higher height.

### Finding Description
`handle_block_pre_commit` in `stacks-signer/src/v0/signer.rs` is the single place a signature is produced, after tallying ≥70% pre-commit weight [1](#0-0) . Before signing, it re-checks for signed conflicts at the same height in *any* tenure via `get_signed_conflicts`/`conflict_still_blocks` [2](#0-1) , and separately re-checks same-tenure conflicts using the node's `get_tenure_tip`: [3](#0-2) 

If `conflicts` contains an entry for the *same tenure* whose reorg was not sanctioned, the code asks the node for the tenure's canonical tip height. If the RPC succeeds and the tip is already at/above the proposed height, it refuses to sign. But if the RPC call fails (`Err(e)`), the code only logs a warning ("Treating the tenure as unconfirmed") and falls through — it does **not** return early, and reaches the signing path (`mark_locally_accepted` → `handle_block_signature` → broadcast acceptance) a few lines later [4](#0-3) .

This is the exact "guard that only covers one tenure, and only runs once at proposal time otherwise" case flagged by the design notes: the `DuplicateBlockFound` check in `check_proposal`/`validate_tenure_change_payload` only runs at proposal arrival, not again at pre-commit time, so the own-tenure branch here is documented as the sole backstop against a second, conflicting block in the same tenure crossing the pre-commit threshold later [5](#0-4) . By failing open (treating an inconclusive node answer as "not yet confirmed, sign it") instead of failing closed, this backstop is defeated whenever the RPC call to the signer's own node fails or times out at the critical moment — which can happen from ordinary node load, restart, or a slow response, not just an adversarial condition.

### Impact Explanation
This breaks the "one-per-tenure/height, no equivocation" equality the pre-commit recheck exists to enforce: the signer can end up placing its signature (`signed_self`) on two different blocks in the same tenure at conflicting heights, both of which can be aggregated by the network toward the 70% signing threshold. That is a `Critical` outcome per the stated impact classes — a signer signing a conflicting block — achievable by a single miner re-proposing a competing tenure-start/continuation block and having it reach the pre-commit threshold while the signer's `get_tenure_tip` call to its own node happens to fail.

### Likelihood Explanation
No majority of signers, no other signer's key, and no StackerDB-transport exploit is required — only: (1) a single miner (or gossip of an earlier block) creating a same-tenure conflicting proposal that reaches the pre-commit threshold, and (2) the signer's `StacksClient::get_tenure_tip` call erroring at that moment (timeout, transient node error, node restart, or non-adversarial load). Because this depends on an incidental RPC failure rather than a guaranteed attacker-controlled trigger, likelihood is moderate rather than certain, but the fail-open behavior is deterministic once the RPC error occurs.

### Recommendation
Change the `Err(e)` branch of the `get_tenure_tip` call in the same-tenure conflict re-check to fail closed (refuse to sign, matching the "wrongly signing cannot be taken back" principle used elsewhere in this same function, e.g. in `conflict_still_blocks`) rather than treating the tenure as "unconfirmed" and proceeding to sign.

### Proof of Concept
1. Miner proposes block `A` in tenure `T` at height `h`; it crosses the pre-commit threshold and the signer signs it (`signed_self` set, entry recorded via `get_signed_conflicts`).
2. Miner (or a replay of an earlier pre-commit) proposes conflicting block `B` in the same tenure `T` at height `h` (or higher); `B` also gathers ≥70% pre-commit weight from the signer set.
3. At the moment `handle_block_pre_commit` runs the same-tenure conflict check for `B` (`stacks-signer/src/v0/signer.rs:1432-1456`), the signer's call to its own node's `get_tenure_tip` fails (e.g., node timeout/restart).
4. Per the `Err(e)` branch, the signer logs a warning and does not return; execution proceeds to `mark_locally_accepted`/`handle_block_signature`, producing a second signature over `B`, conflicting with the already-signed `A` in the same tenure at the same height.

Note: I was not able to fully verify whether an additional, unseen guard elsewhere (outside the excerpts retrieved) closes this gap before broadcast, since the index has size limits; a Devin session with full repository access would be needed to confirm there is no other check between this fallthrough and the signature broadcast.

### Citations

**File:** docs/signer-flows.md (L229-236)
```markdown
## 5. Pre-commit threshold → signature

The only place the signer produces a block signature by counting votes.
Pre-commits from peers (and our own) accumulate; at ≥70% weight the signer
decides whether to follow through. Between validation and threshold, we may have
signed a _different_ block at the same height, possibly in another tenure, so
the world must be re-checked before the signature leaves the box.

```

**File:** docs/signer-flows.md (L274-286)
```markdown
Order matters here: the chainstate re-check runs first and produces an explicit
(sticky) rejection when the block now conflicts with a signed one. The conflict
guard behind it is the silent backstop for what that re-check cannot see, and
silence keeps the door open to sign later once the conflict goes stale. Two
blind spots make the guard necessary:

- the re-check only ever looks at _one_ tenure (a tenure-change block's parent,
  or any other block's own), so a signed sibling at the same height in a third
  tenure is invisible to it;
- the `DuplicateBlockFound` check that would catch a second block in the same
  tenure lives in `check_proposal` and runs only at proposal arrival, never
  again. A block that crosses the pre-commit threshold minutes later has no
  other guard, which is what the own-tenure branch above covers.
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

**File:** stacks-signer/src/v0/signer.rs (L1466-1479)
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
    }
```
