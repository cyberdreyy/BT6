### Title
Stale-conflict path in `handle_block_pre_commit` skips canonicity checks for third-tenure siblings, allowing a signer to sign two conflicting blocks at the same height - (File: stacks-signer/src/v0/signer.rs)

### Summary
`handle_block_pre_commit`'s stale-conflict path only re-verifies canonicity for conflicts in the *same* tenure as the newly-committed block, or (for tenure-change blocks) the *parent* tenure. A signed sibling sitting in a genuinely third, unrelated tenure that is still canonically reachable via `get_tenure_tip` is never re-checked once its local `last_endorsed` timestamp crosses `freshness_cutoff`, so the signer will sign a second, conflicting block at the same height purely because a local clock expired, not because the original block died.

### Finding Description
`conflict_still_blocks` (`stacks-signer/src/v0/signer.rs:1137-1206`) is the only place that asks the node ("does `get_tenure_tip` still reach the conflict?", "is its sortition still canonical?") whether a signed conflict is still alive. It is invoked exclusively from the freshness-gated branch in `handle_block_pre_commit`: [1](#0-0) 

Once `conflict.last_endorsed <= freshness_cutoff` (attacker-controlled by simply delaying the re-proposal/gossip of the competing block past `tenure_last_block_proposal_timeout`), this branch never fires and `conflict_still_blocks`/`get_tenure_tip` is never consulted for that conflict. The code then falls to a second, narrower guard: [2](#0-1) 

This second guard only re-checks conflicts whose `consensus_hash == block_info.block.header.consensus_hash` — i.e. conflicts in the proposed block's *own* tenure. The earlier chainstate re-check, `check_block_against_signer_db_state`, has the same blind spot by design: for a tenure-change block it checks only the *parent* tenure's confirmed tip (`check_tenure_change_confirms_parent`), for any other block only its *own* tenure — never a third tenure. This is explicitly documented as a known blind spot: [3](#0-2) [4](#0-3) 

Concretely: two tenures T and C both extend the same parent P (a tie/race between two tenure-change proposals at the same height H, which the docs themselves acknowledge as a case the guard exists for: "the next tenure's tenure-start block conflicts with the current tenure's block at the same height"). Signer signs block A in tenure T first. Attacker (miner of tenure C) delays gossiping/re-proposing block B past `tenure_last_block_proposal_timeout`. When B crosses the pre-commit threshold: the chainstate check for B (a tenure-change block) checks only P's tenure, not T; the fresh-conflict check for A is skipped because A is now "stale" by the local clock; the stale re-check only looks at conflicts in C's own tenure, not T. Nothing ever asks `get_tenure_tip(T)` again, even though A may still be fully canonical there. The signer proceeds to `SIGN`.

The existing test suite confirms this gap is untested: every cross-tenure test (`run_cross_tenure_scenario`) pins the freshness window to `Duration::from_secs(100_000)`, exercising only the FRESH branch (`conflict_still_blocks`); there is no test covering a stale, third-tenure, still-canonical conflict. [5](#0-4) 

### Impact Explanation
This breaks the UNIQUENESS/canonicity safety property: the signer can produce a valid signature over two conflicting blocks at the same height in different tenures, purely as a function of local wall-clock timing rather than the actual state of the chain. Because `tenure_last_block_proposal_timeout` is a shared, roughly-synchronized configuration value, this is not confined to a single victim signer — every honest signer independently reaches the same stale/no-longer-checked state at roughly the same time, so the attack can accumulate the 70% weight needed to actually finalize the conflicting block B, producing a real double-sign/equivocation at the network level. This matches the Critical category: "a signer signing an invalid, non-canonical, or conflicting block."

### Likelihood Explanation
The attacker only needs to win a single miner/sortition slot to produce a competing tenure-change proposal B at the same height as a genuine, already-signed block A, and control the timing of when B's proposal/re-proposal is gossiped so it lands after `tenure_last_block_proposal_timeout` has elapsed for the network's signers. No signer-key compromise, no majority of signers, and no local/host access is required — only crafting a `BlockProposal` and controlling gossip timing, both explicitly within the stated attacker capability. The scenario is repeatable at every height where the attacker can arrange a tied/competing tenure-change race.

### Recommendation
In the stale branch of `handle_block_pre_commit`, do not limit the re-check to conflicts in the proposed block's own tenure. Instead, for every conflict that is stale (or regardless of freshness), still call `conflict_still_blocks` (which is idempotent and asks the node directly) rather than gating that call behind `last_endorsed > freshness_cutoff`. The freshness timestamp should only be used as a performance shortcut to skip the round-trip when a conflict is *provably* dead by other means, never as a substitute for asking the node about canonical reachability.

### Proof of Concept
Extend `run_cross_tenure_scenario` (`stacks-signer/src/v0/tests.rs`) with a short `tenure_last_block_proposal_timeout` (e.g. `Duration::from_secs(1)`, sleeping past it before delivering B's validation) combined with `TenureAFate::Live` or `TenureAFate::SortitionStillCanonical`, and a mocked `get_tenure_tip` for tenure T that still returns block A at height H:

```rust
#[test]
fn stale_cross_tenure_conflict_still_canonical_must_not_be_signed() {
    // A is signed in tenure T. Wait past tenure_last_block_proposal_timeout so A's
    // conflict is "stale" by the local clock, while the mock node still reports
    // get_tenure_tip(T) == A (i.e. A is still fully canonical/live).
    let (info_a, info_b) = run_cross_tenure_scenario_with_timeout(
        Duration::from_secs(1), // short freshness window
        TenureAFate::Live,      // node still serves tenure T's tip at A's height
    );
    assert_a_signed(&info_a);
    assert!(
        info_b.signed_self.is_none(),
        "block B must NOT be signed: A's tenure is still canonically live per get_tenure_tip, \
         even though A's local signature timestamp is stale"
    );
}
```

With the current implementation, this test would fail: `info_b.signed_self` would be `Some(_)`, because the stale branch never calls `conflict_still_blocks`/`get_tenure_tip` for a conflict outside B's own tenure.

### Citations

**File:** stacks-signer/src/v0/signer.rs (L1393-1411)
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

**File:** docs/signer-flows.md (L425-437)
```markdown
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
