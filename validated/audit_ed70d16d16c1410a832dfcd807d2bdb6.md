Reading the code carefully, I can trace the exact execution path.

The code path is unambiguous. Here is the exact trace:

**When `query_range` reaches 1 and -32005 is still returned:**

- Line 173: `new_range = (1u64 / 2).max(1) = 0.max(1) = 1`
- Line 174: `new_range == query_range` → `true` → logs the error message *"Retrying with standard backoff"*
- `query_range` is **not updated** (the `else` branch at line 191 is skipped)
- Execution falls through unconditionally to lines 195–196: `more_blocks = true; continue;`
- The "standard backoff retry" block at lines 199–206 is **never reached** — it is only reachable when the error does **not** match the -32005 pattern
- `start_block` is never incremented (line 241 is never reached)
- Next iteration: `more_blocks = true` → skips the `changed().await` wait → queries the same `[start_block, start_block]` range → -32005 again → infinite loop

The comment "Retrying with standard backoff" is incorrect. The `continue` at line 196 is unconditional within the entire -32005 branch, making the standard-backoff path dead code for this error class.

---

### Title
Infinite loop in `run_event_listening_task` when a single Ethereum block exceeds the RPC event limit, permanently stalling bridge message processing — (`crates/sui-bridge/src/eth_syncer.rs`)

### Summary

When `get_events_in_range` persistently returns error -32005 for a single-block query (`query_range = 1`), the syncer enters an infinite busy-loop. `start_block` never advances, permanently blocking all subsequent bridge message processing and locking funds for every user whose bridge transaction lands in or after that block.

### Finding Description

`run_event_listening_task` shrinks `query_range` by halving on each -32005 response until it reaches the floor of 1. At that point the guard at line 174 detects `new_range == query_range` and logs *"Retrying with standard backoff"*, but the code unconditionally executes `more_blocks = true; continue` at lines 195–196 regardless of that branch outcome. [1](#0-0) 

The "standard backoff retry" block at lines 199–206 is structurally unreachable for any -32005 error because the `continue` at line 196 exits the match arm before that code is ever evaluated. [2](#0-1) 

Because `start_block` is only incremented at line 241, which is also unreachable during the loop, the syncer is permanently stuck querying the same block. [3](#0-2) 

### Impact Explanation

Every bridge deposit or withdrawal whose Ethereum event falls in the stalled block or any later block is never delivered to the Sui side. The corresponding locked or escrowed funds on Ethereum cannot be released or minted on Sui. This constitutes a **permanent fund lock** matching the High/Medium bounty impact class.

### Likelihood Explanation

The trigger condition is a single Ethereum block containing more bridge-contract log entries than the RPC provider's per-query result cap (commonly 10 000 for Infura/Alchemy). A single bridge user can submit many bridge transactions targeting the same block. The cost is non-trivial Ethereum gas, but the attack is fully permissionless and requires no privileged access. Once triggered, the stall is self-sustaining with no automatic recovery path in the code.

### Recommendation

Replace the `continue` inside the `new_range == query_range` branch with actual exponential backoff (e.g., `tokio::time::sleep`) before retrying, or fall through to the existing `retry_with_max_elapsed_time!` path. A minimal fix:

```rust
if new_range == query_range {
    error!(...);
    // Actually sleep before retrying, do not spin.
    tokio::time::sleep(Duration::from_secs(10)).await;
} else {
    query_range = new_range;
}
more_blocks = true;
continue;
```

Alternatively, after a configurable number of consecutive -32005 failures at `query_range = 1`, the task should emit a fatal alert and pause rather than spin-looping.

### Proof of Concept

1. Mock `get_events_in_range` to always return an error whose `Debug` string contains `"-32005"` for any call where `start_block == end_block` (i.e., a 1-block window).
2. Set `new_finalized_block` to `start_block + 5` so `more_blocks` is initially `true`.
3. Run `run_event_listening_task` for 20 loop iterations (instrument with an atomic counter).
4. Assert that `start_block` has not advanced past its initial value.
5. Assert that the loop counter exceeds 10, confirming the busy-loop rather than a blocking wait.

The loop will spin at CPU speed, `start_block` will remain fixed, and no events will ever be delivered to the channel — matching the described permanent stall.

### Citations

**File:** crates/sui-bridge/src/eth_syncer.rs (L173-196)
```rust
                        let new_range = (query_range / 2).max(1);
                        if new_range == query_range {
                            error!(
                                contract_address=?contract_address,
                                "Block query range is already 1 but RPC still returns -32005 \
                                 for block {}. Retrying with standard backoff.",
                                start_block
                            );
                        } else {
                            warn!(
                                contract_address=?contract_address,
                                "RPC returned -32005 (too many results) for block range {}-{} \
                                 (window={}). Shrinking window to {} blocks and retrying.",
                                start_block,
                                end_block,
                                query_range,
                                new_range,
                            );
                            query_range = new_range;
                        }
                        // Retry immediately with the new (smaller) range; more_blocks stays true
                        // so we don't wait for a new finalized block notification.
                        more_blocks = true;
                        continue;
```

**File:** crates/sui-bridge/src/eth_syncer.rs (L198-206)
```rust
                    // Not a range-overflow error — use standard backoff retry.
                    let Ok(Ok(events)) = retry_with_max_elapsed_time!(
                        eth_client.get_events_in_range(contract_address, start_block, end_block),
                        Duration::from_secs(600)
                    ) else {
                        error!("Failed to get events from eth client after retry");
                        continue;
                    };
                    events
```

**File:** crates/sui-bridge/src/eth_syncer.rs (L241-241)
```rust
            start_block = end_block + 1;
```
