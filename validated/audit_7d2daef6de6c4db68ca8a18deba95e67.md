## Analysis

The Moodle CVE describes a check (`forceloginforprofiles`) that is enforced on some request paths but silently skipped on another (`user/view.php`), letting the same "logically protected" resource be reached through the path that forgot to re-apply the gate. The analogous shape in this codebase is a canonicality/legitimacy check that is only ever run once — at proposal time — and is documented as **not being re-run** on the later paths that lead to an actual signature, with those later paths instead falling back to a permissive default when the check they *do* run can't get an answer.

### Title
Signer can sign over a block whose tenure has become non-canonical because canonicality is validated only at proposal time and the signing-time recheck fails open when the tenure lookup errors - (File: `stacks-signer/src/chainstate/mod.rs`)

### Summary
`GlobalStateView::check_proposal` (v2) validates a proposal's `consensus_hash`/miner pubkey-hash against the *currently active* miner/tenure exactly once, at the moment the proposal arrives [1](#0-0) . This is the only place that ties the proposal to a live, still-canonical sortition. Neither `handle_block_validate_ok`'s recheck nor the pre-commit-threshold recheck re-run this function; both instead call `check_block_against_signer_db_state`, which only re-runs `check_latest_block_in_tenure` (tenure-tip confirmation), per the documented flow [2](#0-1) .

`check_latest_block_in_tenure` asks the node for the tenure's tip via `client.get_tenure_tip(tenure_id)`, and if that call errors (e.g. because a Bitcoin fork has just orphaned the sortition for that `consensus_hash`), the function **assumes the proposal is still valid** rather than rejecting it: [3](#0-2) .

### Finding Description
The equality this is supposed to preserve is "approved-parent vs canonical": a block may only be signed while its tenure's sortition is still on the canonical Bitcoin fork. That equality is checked exactly once, in `GlobalStateView::check_proposal`, via the `ConsensusHashMismatch`/`PubkeyHashMismatch` branches, which are derived from the signer's live view of `MinerState::ActiveMiner` at proposal time [4](#0-3) .

Time then passes while the pre-commit round collects ≥70% weight before any signature is produced (docs describe this window explicitly) [5](#0-4) . When the threshold is reached, the only re-validation performed before the actual signature is `check_block_against_signer_db_state`, which the docs explicitly note does **not** re-run the miner-pubkey/consensus-hash/bitvec/tenure-extend checks from `check_proposal` [2](#0-1) . If a burn-chain reorg orphans the tenure during that window, `get_tenure_tip` for the (now nonexistent) consensus hash is expected to fail; `check_latest_block_in_tenure` treats that failure as "assume proposal is higher," i.e. valid, and signing proceeds [3](#0-2) .

Section 5's own-tenure/cross-tenure "signed conflicts" guard (`get_signed_conflicts`, `conflict_still_blocks`) only fires when there is a *competing signed block*; it does nothing to protect against a tenure that was simply orphaned with no rival signature to compare against [6](#0-5) . The docs' own accounting of "what's not re-run" only claims the duplicate-block check is covered by that later guard [7](#0-6)  — it makes no such claim for the pubkey-hash/consensus-hash canonicality check, which is the one that matters here.

### Impact Explanation
If exploitable, this breaks the "approved-parent vs canonical" equality: a signer could produce a valid signature over a block built on a tenure that is no longer part of the canonical Bitcoin fork at signing time, because the only check that ties the proposal to the *live* canonical miner/tenure state is skipped on the signing path, and the substitute check fails open on lookup errors. That maps to the Critical impact class ("a signer signing an invalid, non-canonical, or conflicting block").

### Likelihood Explanation
This requires no majority of signers, no other signer's key, and no auth token — only a naturally occurring (or attacker-timed) burn-chain reorg landing inside the pre-commit-to-signature window, which any one miner/attacker with reorg capability (or an unlucky natural fork) can trigger; the signer-side logic does the rest. I was not able to fully read `check_block_against_signer_db_state`'s complete body (only the high-level flow via docs and partial code) within the available tool budget, so I could not fully rule out an additional guard inside that function that might independently catch orphaned tenures beyond `check_latest_block_in_tenure`. This is a real gap in my verification and should be checked directly in `stacks-signer/src/v0/signer.rs`.

### Recommendation
Re-run the full `check_proposal` canonicality checks (consensus-hash match against the current `MinerState::ActiveMiner`, miner pubkey-hash match) — not just `check_latest_block_in_tenure` — immediately before a signature is emitted in the pre-commit-threshold path, and change the "node unreachable" fallback in `check_latest_block_in_tenure` to refuse to sign (or hold) rather than assume validity when this recheck happens post-threshold rather than at first proposal.

### Proof of Concept
1. Miner M proposes tenure-start block B with `consensus_hash = CH1`; `check_proposal` passes because `CH1` is the current active-miner tenure and the pubkey-hash matches.
2. Block B is submitted to the node and returns `Ok`; signers broadcast pre-commits.
3. While pre-commits accumulate toward the 70% threshold, a Bitcoin reorg orphans the sortition for `CH1`.
4. When the threshold is reached, `handle_block_pre_commit` calls `check_block_against_signer_db_state`, which only calls `check_latest_block_in_tenure(CH1, …)`.
5. `client.get_tenure_tip(CH1)` fails (tenure no longer known/canonical) → the function returns `Ok(true)` ("assume proposal is higher") instead of rejecting.
6. The signer proceeds to `SIGN`, producing a signature over a block whose tenure is no longer canonical, without ever re-checking the consensus-hash/pubkey-hash conditions that `check_proposal` used to gate acceptance.

### Citations

**File:** stacks-signer/src/chainstate/v2.rs (L119-163)
```rust
        let MinerState::ActiveMiner {
            current_miner_pkh,
            tenure_id,
            parent_tenure_id,
            ..
        } = &self.signer_state.current_miner
        else {
            info!(
                "No valid current miner. Considering invalid.";
                "block_height" => block.header.chain_length,
                "signer_signature_hash" => %block.header.signer_signature_hash()
            );
            return Err(RejectReason::InvalidMiner);
        };
        if &block.header.consensus_hash != tenure_id {
            info!("Miner block proposal consensus hash does not match the current miner's tenure id. Considering invalid.";
                "block_height" => block.header.chain_length,
                "signer_signature_hash" => %block.header.signer_signature_hash(),
                "block_consensus_hash" => %block.header.consensus_hash,
                "active_miner_tenure_id" => %tenure_id,
                "active_miner_parent_tenure_id" => %parent_tenure_id,
            );
            return Err(RejectReason::ConsensusHashMismatch {
                actual: block.header.consensus_hash.clone(),
                expected: tenure_id.clone(),
            });
        }
        let Some(miner_pk) = block.header.recover_miner_pk() else {
            warn!("Failed to recover miner pubkey";
                  "signer_signature_hash" => %block.header.signer_signature_hash(),
                  "consensus_hash" => %block.header.consensus_hash);
            return Err(RejectReason::IrrecoverablePubkeyHash);
        };
        let miner_pkh = Hash160::from_data(&miner_pk.to_bytes_compressed());
        if current_miner_pkh != &miner_pkh {
            warn!(
                "Miner block proposal pubkey does not match the winning pubkey hash for its sortition. Considering invalid.";
                "proposed_block_consensus_hash" => %block.header.consensus_hash,
                "signer_signature_hash" => %block.header.signer_signature_hash(),
                "proposed_block_pubkey" => &miner_pk.to_hex(),
                "proposed_block_pubkey_hash" => %miner_pkh,
                "active_miner_pubkey_hash" => %current_miner_pkh,
            );
            return Err(RejectReason::PubkeyHashMismatch);
        }
```

**File:** docs/signer-flows.md (L229-236)
```markdown
## 5. Pre-commit threshold → signature

The only place the signer produces a block signature by counting votes.
Pre-commits from peers (and our own) accumulate; at ≥70% weight the signer
decides whether to follow through. Between validation and threshold, we may have
signed a _different_ block at the same height, possibly in another tenure, so
the world must be re-checked before the signature leaves the box.

```

**File:** docs/signer-flows.md (L248-263)
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
```

**File:** docs/signer-flows.md (L425-433)
```markdown
Two things belong to the proposal path only and are **not** re-run at validate-ok
or at signing:

- `validate_tenure_change_payload` rejects with `DuplicateBlockFound` when we
  have already accepted a block in the tenure a tenure-change block is starting.
  v2 counts locally or globally accepted blocks (`get_last_signed_block`); v1
  counts only globally accepted ones (`get_last_globally_accepted_block`).
- the v2 `check_proposal` wrapper checks miner pubkey hash, consensus hash, the
  pox bitvec, and tenure-extend rules before delegating here.
```

**File:** docs/signer-flows.md (L435-437)
```markdown
Because the duplicate check never runs again, a block that crosses the pre-commit
threshold long after it was proposed relies on section 5's own-tenure conflict
guard to cover the same ground.
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
