### Title
Miner-triggered "activity" reset lets a single stalled miner block fallback to a valid tenure indefinitely - (File: `stacks-signer/src/chainstate/mod.rs`, `stacks-signer/src/signerdb.rs`)

### Summary
A single active miner (a "one-slot miner" in scope terms — no majority of signers needed) can indefinitely suppress the signer's miner-inactivity timeout by repeatedly submitting invalid/rejected reorg-attempt block proposals. Each such rejected proposal still records "miner activity," resetting the clock the signer uses to decide whether to fall back to a prior, valid tenure. This mirrors the oracle bug's root cause: an update that should only be trusted once verified/legitimate instead resets a lock/timer that a single malicious actor can trigger on demand, permanently defeating the safety fallback that depends on that timer expiring.

### Finding Description
`check_latest_block_in_tenure` (used at proposal time, at validate-ok, and at signing) determines whether a proposal confirms the expected tip of a tenure via `get_tenure_last_block_info`, which only considers *signed* blocks (`get_last_signed_block`) as vetoing a proposal [1](#0-0) . When a proposal fails this "signed tip" check but there is no fresher pre-committed block at or above that height, the failing reorg attempt is explicitly *not* treated as a veto — instead it is recorded as "miner activity" via `update_last_activity_time`, as long as it occurs within `reorg_attempts_activity_timeout` [2](#0-1) .

`update_last_activity_time` is a simple upsert with no validation that the "activity" corresponds to real progress — it fires on any qualifying (even ultimately-rejected) reorg-attempt proposal: [3](#0-2) 

This timestamp feeds `is_timed_out` (v1/v2), which is the sole gate deciding whether the signer treats the current miner's tenure as stalled and falls back to `make_miner_state(prior sortition)` [4](#0-3) ; the state-machine flow documents this fallback path explicitly gated on "no signed block, and inactive past block_proposal_timeout" [5](#0-4) .

Because a currently-active miner (a single actor, requiring no cooperation from other signers) can author and gossip an unlimited stream of non-canonical/invalid reorg-attempt block proposals at will, and each such proposal — even though rejected — resets `last_activity_time`, the miner can keep `is_timed_out` perpetually false. This is structurally identical to the oracle bug: a value/timer that is supposed to reflect "genuine, validated progress" is instead updatable by an adversarial party through actions that are themselves rejected, and once "reset," the protection it guards (fallback to a legitimate view) cannot fire until the next window — except here the attacker can trivially always land inside the window, so the fallback effectively never fires.

### Impact Explanation
This is a liveness wedge: signers become stuck waiting on a miner that never produces a valid, signable block, and the fallback mechanism designed to route around a stalled/malicious miner (`FALL --> make_miner_state(prior sortition)`) is defeated by the very party it is meant to protect against. This matches the in-scope High-impact category — "a signer wedged into never signing valid blocks" — since as long as the active miner keeps emitting failing reorg-attempt proposals faster than `reorg_attempts_activity_timeout`/`block_proposal_timeout`, no signer transitions its local state machine to the prior, still-valid miner, and tenure progress halts.

### Likelihood Explanation
Likelihood is high in the sense that the trigger requires only the current miner's own signing key and the ability to gossip proposals — no majority of signers, no other signer's key, and no auth token — satisfying the "one-slot miner (plus gossip)" threat model in scope. The main uncertainty is the exact numeric relationship between `reorg_attempts_activity_timeout` and `block_proposal_timeout`/`tenure_last_block_proposal_timeout` in current configs, which determines how tight the miner's proposal cadence must be to keep resetting the clock before it expires; I was not able to pull the full `chainstate/v1.rs`/`v2.rs` `is_timed_out` bodies or `check_latest_block_in_tenure` source in this pass to confirm there is no additional cap on the number of "activity resets" per tenure.

### Recommendation
Do not count a rejected/failing reorg-attempt proposal as unconditional "activity" for timeout purposes indefinitely; e.g., cap the number of activity resets granted to a non-progressing miner per tenure, or require the reset to be corroborated by a distinct signer or a validated (not merely proposed) event, so a single miner cannot single-handedly and perpetually suppress the inactivity fallback.

### Proof of Concept
Conceptual sequence (I could not extract a runnable test harness from the index given the size limits on retrieved file contents for `chainstate/v1.rs`/`v2.rs`/`signer.rs`'s full proposal-handling bodies):
1. Miner M is the active miner for tenure T but has not produced a block confirming the prior signed tip (e.g., intentionally building on a stale parent).
2. M repeatedly gossips new `BlockProposal`s that fail `check_latest_block_in_tenure`'s "signed tip" check but qualify as reorg attempts within `reorg_attempts_activity_timeout`.
3. Each such proposal causes signers to call `update_last_activity_time` for T [3](#0-2) , resetting the clock `is_timed_out` reads.
4. As long as M's proposal cadence beats `block_proposal_timeout`, `is_timed_out` never returns true, so `bitcoin_block_arrival`'s fallback to the prior sortition's miner is never taken [5](#0-4) .
5. No signer ever adopts a valid miner view for T; the network stalls on M despite M never producing a valid, canonical block.

### Citations

**File:** docs/signer-flows.md (L391-398)
```markdown
`check_latest_block_in_tenure` answers "does this block confirm the tip we
expect?" and it runs in three places: at proposal arrival (inside
`check_proposal`), at validate-ok, and at the moment of signing. _Which_ tenure
it is asked about depends on the block: a tenure-change block is checked against
its **parent** tenure, every other block against its **own**. Never both. The
pivotal helper is `get_tenure_last_block_info`, which considers only blocks that
carry a signature (`get_last_signed_block`): a pre-commit never vetoes anything,
it only counts as miner activity.
```

**File:** docs/signer-flows.md (L406-411)
```markdown
    SAME --> CLB["check_latest_block_in_tenure(tenure_id)"]
    CLB --> LSB{"fresh SIGNED tip in that tenure?<br/>get_tenure_last_block_info =<br/>get_last_signed_block + freshness from<br/>the last signature time<br/>(tenure_last_block_proposal_timeout)"}
    LSB -- "yes, and proposal not higher" --> RA["fails the check<br/>(a reorg attempt within<br/>reorg_attempts_activity_timeout still<br/>counts as miner activity:<br/>update_last_activity_time)"]:::bad
    LSB -- "no signed tip, or proposal higher" --> CARVE{"fresh PRE-COMMITTED block<br/>at ≥ this height?<br/>get_last_accepted_block"}
    CARVE -- yes --> ACT["count miner activity only —<br/>a pre-commit never vetoes<br/>update_last_activity_time"]
    CARVE -- no --> NODE
```

**File:** docs/signer-flows.md (L466-468)
```markdown
    PEND -- no --> TO{"current tenure timed out?<br/>check_miner_inactivity →<br/>v1/v2 SortitionState::is_timed_out"}
    TO -- "signed a block in tenure?<br/>has_signed_block_in_tenure" --> NEVER(["never times out —<br/>we committed a signature"])
    TO -- "no signed block, and inactive<br/>past block_proposal_timeout" --> FALL["fall back to prior tenure:<br/>make_miner_state(prior sortition)"]
```

**File:** stacks-signer/src/signerdb.rs (L2248-2257)
```rust
    /// Update the tenure (identified by consensus_hash) last activity timestamp
    pub fn update_last_activity_time(
        &mut self,
        tenure: &ConsensusHash,
        last_activity_time: u64,
    ) -> Result<(), DBError> {
        debug!("Updating last activity for tenure"; "consensus_hash" => %tenure, "last_activity_time" => last_activity_time);
        self.db.execute("INSERT OR REPLACE INTO tenure_activity (consensus_hash, last_activity_time) VALUES (?1, ?2)", params![tenure, u64_to_sql(last_activity_time)?])?;
        Ok(())
    }
```

**File:** stacks-signer/src/chainstate/mod.rs (L616-639)
```rust
    /// Check if the tenure identified by the ConsensusHash is timed out
    pub fn is_timed_out(
        version: &SortitionStateVersion,
        consensus_hash: &ConsensusHash,
        signer_db: &SignerDb,
        local_address: &StacksAddress,
        proposal_config: &ProposalEvalConfig,
        eval: &GlobalStateEvaluator,
    ) -> Result<bool, SignerChainstateError> {
        match version {
            SortitionStateVersion::V1 => SortitionStateV1::is_timed_out(
                consensus_hash,
                signer_db,
                proposal_config.block_proposal_timeout,
            ),
            SortitionStateVersion::V2 => SortitionStateV2::is_timed_out(
                consensus_hash,
                signer_db,
                eval,
                local_address,
                proposal_config.block_proposal_timeout,
            ),
        }
    }
```
