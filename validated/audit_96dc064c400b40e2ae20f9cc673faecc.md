Based on my investigation, I found a concrete analog: `handle_block_proposal` in `stacks-signer/src/v0/signer.rs` inserts a freshly-received, one-slot-miner-controlled `BlockInfo` into `signerdb` via `self.signer_db.insert_block(&block_info)` at the initial proposal path (around line 1717-1718) **without ever calling `check_static_valid_block`** on it. That static check — which guards against `chain_length`, `burn_block_height`, or `reward_cycle` values `>= i64::MAX` before they are written into the sqlite DB via `u64_to_sql()` — is only invoked later, in `handle_block_validate_ok` [1](#0-0) , not on the direct-from-miner proposal path.

### Title
Unvalidated numeric fields in miner-controlled block proposals bypass `check_static_valid_block` before being persisted to SignerDB - (File: `stacks-signer/src/v0/signer.rs`)

### Summary
`handle_block_proposal` builds a `BlockInfo` directly from an attacker/miner-controlled `BlockProposal` and persists it via `signer_db.insert_block()` without calling `check_static_valid_block()`, the guard that exists specifically to prevent overflow of `chain_length`/`burn_block_height`/`reward_cycle` when converted with `u64_to_sql` (`i64` cast) for SQLite storage.

### Finding Description
`BlockInfo::check_static_valid_block` [2](#0-1)  exists precisely because block header/proposal numeric fields are attacker (single miner)-controlled and must be bounded below `i64::MAX` before they're written to SQLite via `u64_to_sql`. This check is exercised in `handle_block_validate_ok`, gating whether an already-inserted `block_info` is trusted further: [1](#0-0) 

However, the *first* time a proposal is seen — in `handle_block_proposal` — the signer constructs `BlockInfo::from(block_proposal.clone())` and, if not rejected by sortition/state checks, unconditionally calls `self.signer_db.insert_block(&block_info)` [3](#0-2)  with no call to `check_static_valid_block` anywhere in that function. A single malicious miner can craft `chain_length` or `burn_block_height` (both plain `u64` fields copied straight off the wire) at or above `i64::MAX`, causing `u64_to_sql` to fail during the `INSERT`. Because `insert_block`'s failure path is `unwrap_or_else(|e| self.handle_insert_block_error(e))`, the actual behavior depends on that handler; regardless, this is the exact "malformed data is stored/attempted-stored without validating that it fits the expected/consensus-critical format first" pattern described in the report: validation exists in the codebase but is applied inconsistently, leaving a window where corrupt/out-of-range consensus-relevant data can reach the persistence layer from a single untrusted proposer.

### Impact Explanation
If `insert_block`/`u64_to_sql` errors are not handled gracefully everywhere they are reached from this un-guarded path (e.g., if `handle_insert_block_error` panics on this class of error, or if a partial/corrupt row is still written), a single one-slot miner could wedge a signer's local processing of that block height, potentially blocking it from ever properly processing/signing subsequent proposals at that reward cycle — a liveness wedge triggered without needing a majority of signers, satisfying the "High" impact bar (signer wedged into never signing valid blocks).

### Likelihood Explanation
Likelihood is **uncertain/low-to-medium**. I could not fully verify at this iteration limit (a) the exact behavior of `handle_insert_block_error` for this error class, or (b) whether `u64_to_sql` truly fails on `i64::MAX`-adjacent values in a way that reaches this uncaught path, versus already being caught by earlier proposal-level checks (e.g., timestamp/age checks I did see, but no explicit chain_length/burn_height bound check) in `handle_block_proposal`. This requires further code reading beyond what tool budget allowed.

### Recommendation
Call `BlockInfo::check_static_valid_block()` immediately after constructing `BlockInfo::from(block_proposal.clone())` in `handle_block_proposal`, before any `insert_block` call, and reject the proposal (send a rejection response, do not store) if the check fails — mirroring how it already gates behavior in `handle_block_validate_ok`. This closes the gap where the very first, most attacker-reachable ingestion point for block data skips the same static validation applied later in the flow. As a longer-term measure, audit all `self.signer_db.insert_block(...)` call sites (`v0/signer.rs`) to ensure each is preceded by `check_static_valid_block`, consistent with the report's broader recommendation to enumerate and document every action a privileged/untrusted upstream party (here, the current tenure's miner) can trigger.

### Proof of Concept
Conceptual (not independently executed due to tool budget):
1. A byzantine miner (the current tenure's sole leader) crafts a `NakamotoBlock` header with `chain_length` or `burn_block_height` set to a value `>= i64::MAX` and sends it as a `BlockProposal` over the miner's StackerDB slot.
2. The signer's `handle_block_proposal` receives it, passes the sortition/state checks (which do not bound these numeric fields), builds `BlockInfo::from(block_proposal)`, and calls `self.signer_db.insert_block(&block_info)` without any prior `check_static_valid_block` call [3](#0-2) .
3. `insert_block` internally uses `u64_to_sql` on these fields; the exact runtime behavior on overflow (silent DB error vs. panic vs. corrupted row) is the unresolved point requiring code-level confirmation of `handle_insert_block_error` and the `INSERT` SQL binding path in `signerdb.rs`.

### Citations

**File:** stacks-signer/src/v0/signer.rs (L1716-1719)
```rust
            // Do not store KNOWN invalid blocks as this could DOS the signer. We only store blocks that are valid or unknown.
            self.signer_db
                .insert_block(&block_info)
                .unwrap_or_else(|e| self.handle_insert_block_error(e));
```

**File:** stacks-signer/src/v0/signer.rs (L1941-1944)
```rust
        if !block_info.check_static_valid_block() {
            debug!("{self}: Block is syntatically invalid; will not store");
            return;
        }
```

**File:** stacks-signer/src/signerdb.rs (L365-380)
```rust
    /// Perform static checks on the BlockInfo and determine if it is syntactically valid.
    /// Specifically, all integer values must be less than i64::MAX, since these values get stored
    /// in the sqlite DB via u64_to_sql()
    pub fn check_static_valid_block(&self) -> bool {
        let max_val = u64::try_from(i64::MAX).expect("infallible");
        if self.block.header.chain_length >= max_val {
            return false;
        }
        if self.burn_block_height >= max_val {
            return false;
        }
        if self.reward_cycle >= max_val {
            return false;
        }
        true
    }
```
