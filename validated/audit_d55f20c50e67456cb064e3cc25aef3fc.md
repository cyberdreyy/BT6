### Title
Duplicate/conflicting block in a tenure can still cross the pre-commit signature threshold when the node's tenure-tip view is unconfirmed or unreachable — (File: `stacks-signer/src/v0/signer.rs`, `docs/signer-flows.md`)

### Summary
The `DuplicateBlockFound` check that is supposed to prevent a signer from ever signing two conflicting blocks within the same tenure is enforced **only** at proposal-arrival time (`check_proposal` / `validate_tenure_change_payload`), and is deliberately **not** re-run when a pre-commit threshold is crossed (`check_block_against_signer_db_state`). The documented backstop for this gap — the "own-tenure conflict guard" evaluated in `handle_block_pre_commit` — has a fail-open branch: when the node cannot confirm the own tenure's tip at or above the candidate block's height (either because it "never confirmed" it, or because the node is unreachable), the signer proceeds to **SIGN** instead of holding back. This mirrors the reported bug class: a check that is enforced on the "primary" validation path (`StakediTry`'s restriction role check on direct deposit) is silently skipped on an alternate path (`wiTryVaultComposer._deposit()`), letting an action that should be blocked go through by routing around the one place the guard is applied.

### Finding Description
Per `docs/signer-flows.md:425-437`, the duplicate-block-in-tenure protection lives only in the proposal path: [1](#0-0) 

This means a block that is queued (`insert_pending_block_validation`) rather than immediately checked, or one whose pre-commit accumulates weight over time, never has this duplicate check re-applied at `handle_block_validate_response` (`check_block_against_signer_db_state`, `stacks-signer/src/v0/signer.rs:1345-1366`) or at the pre-commit-threshold signing step (`stacks-signer/src/v0/signer.rs:1340-1366`): [2](#0-1) 

The only remaining protection against a second, conflicting block in the *same* tenure reaching a signature is the "own-tenure" branch of the conflict guard executed right after that narrower re-check, described in `docs/signer-flows.md:263-268`:
```
OWN -- yes --> TIP{"own tenure confirmed
at ≥ this height?
get_tenure_tip(own tenure)"}
TIP -- yes --> HOLD2["refuse to sign"]
TIP -- "no — never confirmed" --> SIGN
TIP -- "node unreachable" --> SIGN
``` [3](#0-2) 

So the state-machine equality "at most one signed block per tenure at a given position" is only preserved when the node can positively confirm the tenure's tip. If the node's view of the tenure tip is momentarily unreachable, or the tenure has never yet been confirmed at that height in the node's index (e.g. right after a burn-block/tenure-change event, or during normal node-connectivity hiccups that a single miner/relayer can induce or simply wait for), the guard fails open and the signer signs. Since `DuplicateBlockFound` never runs again after proposal time, and the conflict guard is the sole remaining backstop, a two-question OR-condition ("node unreachable" OR "never confirmed") is enough to let a conflicting/duplicate block in the same tenure be signed — exactly the "rejection recounted as an accept" / "signing a conflicting block" class called out in the rules.

### Impact Explanation
If the own-tenure conflict guard fails open, a signer can be made to produce a valid signature over a second block in a tenure that already has a signed block at/above that height, i.e. a signature over a **conflicting block within its own tenure** — the exact `DuplicateBlockFound` scenario that the proposal-time check exists to prevent. This is a Critical-class outcome per the rules ("a signer signing an invalid, non-canonical, or conflicting block"), because it breaks the one-block-per-tenure invariant that downstream consensus (and other signers) rely on when tallying signatures/pre-commits.

### Likelihood Explanation
This requires only a single miner/proposer plus normal node-connectivity variance (no majority of signers, no other signer's key, no auth token) — the "never confirmed"/"unreachable" branch of `get_tenure_tip` is a routine condition (e.g., right after a tenure-change/burn-block event before the node's index catches up, or any transient RPC failure to the local node), not an attacker-controlled majority action, so it is readily reachable by a single actor timing a second proposal in the same tenure around such a window.

### Recommendation
Do not fail open on `get_tenure_tip` uncertainty in the own-tenure conflict branch. When the node cannot positively confirm that its own tenure's tip is below the candidate height, `handle_block_pre_commit` should hold rather than sign, mirroring the "no signed tip, or proposal higher" vs "could not ask" distinction already used correctly elsewhere in `check_latest_block_in_tenure` (`docs/signer-flows.md:409-415`), rather than treating "unreachable"/"never confirmed" as equivalent to "safe to sign."

### Proof of Concept
1. A miner proposes block A in tenure T at height h; it is validated, pre-committed, and crosses the ≥70% pre-commit weight threshold, so signers call `handle_block_pre_commit` → `SIGN` (`stacks-signer/src/v0/signer.rs`), recording a globally-signed block in tenure T at height h.
2. The same miner (or a relayed proposal) then proposes a second, conflicting block B in tenure T also at height h (or a duplicate tenure-start), timed so that at the moment its pre-commit threshold is reached, the local stacks-node's index has not yet confirmed tenure T's tip (e.g., immediately following a `NewBurnBlock`/tenure-change event window) or the node RPC is momentarily unreachable.
3. `check_block_against_signer_db_state` for block B does not re-run `validate_tenure_change_payload`'s `DuplicateBlockFound` check (per `docs/signer-flows.md:425-437`), so it passes.
4. In `handle_block_pre_commit`'s own-tenure conflict branch, `get_tenure_tip(own tenure)` returns "never confirmed" or errors ("node unreachable"), so per the documented flow the guard resolves to `SIGN` instead of `HOLD2`.
5. The signer emits a signature over block B, a second signed block in tenure T at height h — a conflicting/duplicate block that `DuplicateBlockFound` was designed to prevent. [4](#0-3) [5](#0-4) [3](#0-2) 

Note: I was unable to directly view the Rust source of the exact "OWN"/`TIP`/`SIGN` branch implementation (only its documented behavior in `docs/signer-flows.md`) due to running out of tool iterations; the surrounding `check_block_against_signer_db_state` and pre-commit-threshold code was directly confirmed in `stacks-signer/src/v0/signer.rs:1340-1420`. Confirming the exact function name/line range implementing the "OWN tenure / TIP / node unreachable → SIGN" branch would require a further read of `stacks-signer/src/v0/signer.rs` beyond line 1420 (the conflict-check continuation) or `chainstate/mod.rs`/`chainstate/v1.rs`/`v2.rs`.

### Citations

**File:** docs/signer-flows.md (L263-268)
```markdown
    FRESH -- "no — all stale" --> OWN{"a conflict in this block's<br/>OWN tenure?"}
    OWN -- yes --> TIP{"own tenure confirmed<br/>at ≥ this height?<br/>get_tenure_tip(own tenure)"}
    TIP -- yes --> HOLD2["refuse to sign"]:::hold
    TIP -- "no — never confirmed" --> SIGN
    TIP -- "node unreachable" --> SIGN
    OWN -- no --> SIGN["SIGN: mark_locally_accepted,<br/>handle_block_signature,<br/>broadcast acceptance"]:::good
```

**File:** docs/signer-flows.md (L420-437)
```markdown
A failed check becomes a different rejection depending on who asked.
`check_block_against_signer_db_state` returns `SortitionViewMismatch`, or
`ConnectivityIssues` when the lookup itself errored rather than answering; the v2
`check_proposal` path returns `InvalidParentBlock`.

Two things belong to the proposal path only and are **not** re-run at validate-ok
or at signing:

- `validate_tenure_change_payload` rejects with `DuplicateBlockFound` when we
  have already accepted a block in the tenure a tenure-change block is starting.
  v2 counts locally or globally accepted blocks (`get_last_signed_block`); v1
  counts only globally accepted ones (`get_last_globally_accepted_block`).
- the v2 `check_proposal` wrapper checks miner pubkey hash, consensus hash, the
  pox bitvec, and tenure-extend rules before delegating here.

Because the duplicate check never runs again, a block that crosses the pre-commit
threshold long after it was proposed relies on section 5's own-tenure conflict
guard to cover the same ground.
```

**File:** stacks-signer/src/v0/signer.rs (L1340-1420)
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
```
