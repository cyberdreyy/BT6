### Title
Unbounded growth of `block_validations_pending` lets a single miner queue unlimited block proposals in the signer, wedging validation submission - ([File: stacks-signer/src/signerdb.rs])

### Summary
`SignerDb::insert_pending_block_validation` (`stacks-signer/src/signerdb.rs:2084-2095`) inserts a row into `block_validations_pending` with no size cap, no per-miner limit, and no deduplication beyond the raw `signer_signature_hash`. This is in sharp contrast to the sibling "pending response" tables (`signer_pending_signature_responses`, `signer_pending_pre_commit_responses`, `signer_pending_rejection_responses`), which explicitly cap and evict at 3 entries per signer (see `add_pending_block_signature_response`, `stacks-signer/src/signerdb.rs:2520-2544`, and the eviction tests at `stacks-signer/src/signerdb.rs:4669-4737`). A single miner can generate an unbounded number of distinct valid-looking block proposals (varying tx sets/timestamps at the same tip) while a node validation is already in flight, driving unbounded growth of this table.

### Finding Description
`handle_block_proposal` (`stacks-signer/src/v0/signer.rs:1574-1727`) computes `signer_signature_hash` from the proposed block and, if the proposal is "not provably invalid" by `check_block_against_state`, either submits it for validation or — if a validation is already outstanding (`self.submitted_block_proposal.is_some()`) — calls:

```
self.signer_db.insert_pending_block_validation(&signer_signature_hash, get_epoch_time_secs())
```
`stacks-signer/src/v0/signer.rs:1709-1713`

`insert_pending_block_validation` (`stacks-signer/src/signerdb.rs:2084-2095`) does a bare `INSERT INTO block_validations_pending (...)` with no cap on row count and no dedup against the miner/proposer. `submit_block_for_validation` (`stacks-signer/src/v0/signer.rs:2585-2645`) has two additional insertion paths that hit the same unbounded table: when the parent block hasn't been processed yet, and when the stacks-node returns HTTP 429 ("too many requests"). Both paths are entirely miner-controlled: a single miner can broadcast many syntactically-distinct block proposals (each producing a unique `signer_signature_hash`, e.g. by varying included transactions or timestamps) at a pace faster than the node can validate them, or can simply keep a validation "outstanding" while it floods more distinct proposals to the same signer over StackerDB (miner → signer path, no majority or extra key needed).

Since only one signature is inserted per unique `signer_signature_hash`, and the block is only "provably invalid" if `check_block_against_state` rejects it outright (bad protocol version, static invalidity, problematic-tx flag, or a sortitions-view check failure) — none of which bound the *number of distinct valid-appearing proposals* a miner can produce — the miner can keep pushing new proposals faster than `check_pending_block_validations` (`stacks-signer/src/v0/signer.rs:2082-2112`) drains them one at a time via `get_and_remove_pending_block_validation` (FIFO, one row per completed validation round-trip). Each accepted, non-provably-invalid proposal also causes a full `BlockInfo` to be persisted via `insert_block` (`stacks-signer/src/v0/signer.rs:1717-1719`), growing the `blocks` table in lockstep with the pending-validation queue.

This is the CWE-770 analog: unbounded resource allocation gated by attacker-controlled input, directly reachable by a lone miner talking to a signer (plus normal StackerDB gossip), without needing a majority of signers, another signer's key, or the auth token.

### Impact Explanation
This maps to the "signer wedged into never signing valid blocks" / liveness class. If the `block_validations_pending` and `blocks` tables grow without bound:
- The signer's SQLite DB grows unbounded on disk, and the drain rate is bounded by the node's per-block validation latency, so a fast enough miner can outpace `check_pending_block_validations`'s one-at-a-time drain forever.
- Because the signer only submits the *next* validation after the current one completes (`self.submitted_block_proposal.is_some()` gate) and processes the queue FIFO by insertion order (`get_and_remove_pending_block_validation` orders `ORDER BY added_time ASC`), an attacker's own flood of proposals will occupy the queue ahead of a legitimate/canonical block's proposal, delaying or effectively starving the signer's ability to validate and sign the real block in a timely manner — a liveness wedge on that signer.
- Sustained growth can exhaust disk or memory resources for the signer process (classic OOM/DoS per the referenced advisory's bug class), degrading or crashing the signer, which also constitutes a liveness failure (signer stops signing anything, valid or not).

This does not by itself create a signature over an invalid/non-canonical block, so it does not reach the "Critical" tier, but it fits the "High: a signer wedged into never signing valid blocks" tier from the rules.

### Likelihood Explanation
High from a capability standpoint: a single miner already has an authenticated channel to broadcast `BlockProposal` messages to the signer set over StackerDB, and needs no signer key, no auth token, and no majority collusion. Producing distinct-but-plausible block proposals (differing in tx selection or timestamp) that are not "provably invalid" via `check_block_against_state` is well within a miner's control, since the miner constructs the block contents.

### Recommendation
Add an explicit cap (with FIFO or LRU eviction) on `block_validations_pending`, mirroring the 3-entry eviction pattern already implemented for `signer_pending_signature_responses`/`signer_pending_pre_commit_responses`/`signer_pending_rejection_responses`. Additionally, consider deduplicating/rate-limiting pending validations per proposing miner or per tenure/height, and pruning stale pending validations that exceed `block_proposal_max_age_secs` before they are eligible for submission.

### Proof of Concept
1. A miner constructs and broadcasts a first `BlockProposal` A to the signer's StackerDB slot; the signer accepts it as "not provably invalid" and calls `submit_block_for_validation`, setting `self.submitted_block_proposal = Some((hash_A, ...))` (`stacks-signer/src/v0/signer.rs:2613-2626`).
2. Before the node's validation response for A returns, the same miner broadcasts many more distinct proposals B, C, D, … (each with a unique `signer_signature_hash`, e.g. by varying an included transfer tx or the block timestamp), all still valid enough to bypass `check_block_against_state`.
3. For each of B, C, D, …, `handle_block_proposal` takes the `submitted_block_proposal.is_some()` branch and calls `self.signer_db.insert_pending_block_validation(&hash, ...)` (`stacks-signer/src/v0/signer.rs:1709-1713`), each producing a fresh, uncapped row in `block_validations_pending`, plus a corresponding `insert_block` row in `blocks`.
4. Because the miner can outpace the node's validation round-trip time indefinitely, both tables grow without bound, and legitimate proposals queued behind the flood are delayed in `check_pending_block_validations`'s FIFO drain (`stacks-signer/src/v0/signer.rs:2082-2112`), demonstrating the liveness wedge / unbounded resource growth.