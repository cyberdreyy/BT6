### Title
Unbounded quadratic replay-set prefix search lets a single signer wedge every other signer's block-validation path - ([File: libsigner/src/v0/signer_state.rs])

### Finding Description
The bug class in the veraPDF advisory is: an attacker-controlled *size/shape* parameter drives an interpreter loop with no upper bound, and the cost of that loop is paid unconditionally by the party parsing the input (`array N`, unguarded `for`). The reachable analog here is `GlobalStateEvaluator::find_majority_prefix_replay_set` in `libsigner/src/v0/signer_state.rs:196-275`, reached from `GlobalStateEvaluator::determine_global_state` (`libsigner/src/v0/signer_state.rs:102-158`).

Each registered signer broadcasts a `StateMachineUpdate` message over StackerDB (gossip) whose `StateMachineUpdateContent::V1`/`V2` variant carries an attacker-controlled `replay_transactions: Vec<StacksTransaction>` field (`libsigner/src/v0/messages.rs`). The only bound on this vector is the overall message size cap `STATE_MACHINE_UPDATE_MAX_SIZE = 2 * 1024 * 1024` (2 MB) (`libsigner/src/v0/messages.rs:66`, enforced at `libsigner/src/v0/messages.rs:980-997`). A signer can therefore fill this vector with many small, distinct, syntactically-valid `StacksTransaction`s (a minimal `StacksTransaction` serializes to well under 200 bytes), giving `replay_transactions.len()` on the order of 10,000+ elements from a single 2 MB message — no other signer's cooperation or majority is required.

Every other signer that ingests this update inserts it into its `GlobalStateEvaluator::address_updates` map (`insert_update`, `libsigner/src/v0/signer_state.rs:161-167`) and, on essentially every subsequent evaluation of `determine_global_state()` (called from `check_block_against_global_state` in `stacks-signer/src/v0/signer.rs`, i.e. on the block-proposal/validation hot path), the replay-set disagreement path is taken whenever no single vector has ≥70% weight:

```
libsigner/src/v0/signer_state.rs:196-275
fn find_majority_prefix_replay_set(...) {
    ...
    let mut candidate_prefix = initial_set.0.clone();      // attacker-controlled length L
    ...
    while !candidate_prefix.is_empty() {
        candidate_prefix.pop();
        for (replay_set, weight) in tx_replay_sets {        // over all N distinct sets
            if replay_set.0.starts_with(&candidate_prefix) { // O(L) per comparison
                ...
            }
        }
        ...
    }
}
```

This is `O(L)` pop iterations, each doing an `O(N)` scan where every comparison itself is `O(L)` (`Vec::starts_with` compares up to `L` `StacksTransaction`s, and `StacksTransaction` equality is a deep structural comparison, not a cheap hash compare). The total cost is `O(N·L²)`. With `L` on the order of 10⁴ (bounded only by the 2 MB message cap) and `N` on the order of the signer-set size, this single crafted message forces every honest signer to repeat an `O(N·L²)` computation on essentially every pass through `check_block_against_global_state`/`determine_global_state` until the signer that sent it either changes its update or is dropped from consideration — exactly the "unbounded/unguarded interpreter loop driven by an attacker-supplied size" pattern from the advisory, except here the "size" is `replay_transactions.len()` instead of a PostScript `array` operand, and the "loop with no bound check" is the `while !candidate_prefix.is_empty()` truncation loop instead of `for`.

### Impact Explanation
This maps to the **High** impact category: *a signer wedged into never signing valid blocks*. Because `determine_global_state()`/`find_majority_prefix_replay_set()` sits directly on `check_block_against_global_state`, which every signer must evaluate for every incoming block proposal, a single malicious (non-majority) signer can force this expensive recomputation on every other signer's event-processing pass. If the induced CPU cost is large enough to consistently blow past the signer's proposal/validation windows (`block_proposal_max_age_secs`, pre-commit timeouts, tenure idle timeouts), affected signers will systematically fail to validate and sign otherwise-valid blocks in time, degrading the 70% agreement liveness guarantee the whole pre-commit/signature protocol depends on. No majority, no key compromise, and no transport-level flooding is needed — a single crafted `StateMachineUpdate` gossip message is sufficient.

### Likelihood Explanation
Likelihood is high for the trigger, moderate for full realization of "High" severity:
- Any registered signer (one is sufficient; no majority) can construct this message; `StateMachineUpdate`/`ReplayTransactionSet` fields are entirely signer-supplied and only checked for overall byte-size (2 MB), never for element count of `replay_transactions`.
- The evaluator has no defensive cap on `tx_replay_sets` entry length before entering the truncation loop, nor any per-call time/iteration budget analogous to what the advisory recommends adding to `CMapFactory.getCMap`.
- Whether the resulting CPU cost is large enough in practice to reliably blow proposal/pre-commit deadlines depends on `N` (signer-set size) and constant-factor costs of `StacksTransaction` comparison/hashing, which I was not able to fully benchmark here — this is the main uncertainty in this finding.

### Recommendation
- Bound `replay_transactions.len()` (and/or total serialized transaction count) independently of the 2 MB byte cap — e.g. a small fixed maximum (tens, not thousands) — and reject/ignore `StateMachineUpdate` content that exceeds it before it ever reaches `GlobalStateEvaluator`.
- In `find_majority_prefix_replay_set`, avoid repeated `O(L)` `starts_with` calls per truncation step; e.g. precompute a hash/prefix-trie of each replay set once, or cap the number of truncation iterations.
- Consider giving `determine_global_state()` an overall time/iteration budget so a pathological replay-set shape degrades gracefully (falls back to the empty/liveness-preserving replay set) instead of imposing unbounded recomputation cost on every peer, mirroring the advisory's proposed wall-clock/operand-budget wrapper around `CMapFactory.getCMap`.

### Proof of Concept
1. As a registered signer (no majority needed), construct a `StateMachineUpdate` (`V1` or `V2` content) whose `replay_transactions` field contains ~10,000 distinct, minimally-sized, individually valid `StacksTransaction`s, keeping total serialized size just under `STATE_MACHINE_UPDATE_MAX_SIZE` (2 MB).
2. Broadcast it over the signer's `StateMachineUpdate` StackerDB slot (`MessageSlotID::StateMachineUpdate`).
3. Every other signer deserializes it (`StateMachineUpdate::consensus_deserialize`, `libsigner/src/v0/messages.rs:989-1012`) and calls `GlobalStateEvaluator::insert_update` for the sender's address.
4. On the next block proposal, each signer's `check_block_against_global_state` invokes `determine_global_state()`, which — since the malicious replay set does not have 70% agreement with any other signer's set — falls into `find_majority_prefix_replay_set`, incurring `O(N·L²)` comparisons against a ~10,000-element vector on every subsequent proposal until the sender changes or is excluded.
5. Measure wall-clock time of `determine_global_state()` under this condition versus a baseline empty/short replay set to confirm the disproportionate cost (this step — actual timing under representative `N` — was not completed in this analysis and should be validated by a background agent with repo access).