The code path is confirmed. Here is the analysis:

**Exact trace through `run_event_listening_task`:**

1. `query_range` initializes to `ETH_LOG_QUERY_MAX_BLOCK_RANGE = 1000` [1](#0-0) 
2. On each -32005 error, `new_range = (query_range / 2).max(1)` is computed. [2](#0-1) 
3. When `query_range` has already been halved down to 1, `new_range = (1/2).max(1) = 1`, so `new_range == query_range`. The `if` branch at line 174 fires: it logs an error but **does not update `query_range`** (the `query_range = new_range` assignment is exclusively in the `else` branch at line 191). [3](#0-2) 
4. Execution falls through to `more_blocks = true; continue;` with no sleep and no backoff. [4](#0-3) 
5. Because `more_blocks = true`, the loop skips the `last_finalized_block_receiver.changed().await` yield point entirely. [5](#0-4) 
6. `start_block` is only advanced at line 241 (`start_block = end_block + 1`), which is **never reached** in this spin path. [6](#0-5) 

The non-32005 error path correctly uses `retry_with_max_elapsed_time!` with a 600-second cap, but the `new_range == query_range` branch has no equivalent guard. [7](#0-6) 

---

### Title
Missing backoff when `query_range == 1` and RPC returns -32005 causes infinite spin loop and permanent fund lock — (`crates/sui-bridge/src/eth_syncer.rs`)

### Summary
`run_event_listening_task` shrinks `query_range` by halving on each -32005 response, flooring at 1. Once at 1, if the RPC still returns -32005 (because a single block contains more events than the RPC result cap), the code logs an error and immediately `continue`s with `more_blocks = true` and no sleep. `start_block` never advances. The syncer spins indefinitely on the same block, and every EVM deposit from that block onward is permanently unprocessed.

### Finding Description
In `run_event_listening_task` (`eth_syncer.rs` lines 125–243), the -32005 handling at lines 168–196 has two branches:

- **Normal shrink** (`new_range < query_range`): updates `query_range = new_range`, sets `more_blocks = true`, and `continue`s — acceptable because the next iteration queries a strictly smaller window.
- **Already-at-floor** (`new_range == query_range == 1`): logs an error, **does not add any sleep or backoff**, sets `more_blocks = true`, and `continue`s.

In the floor branch the loop body re-executes immediately: `end_block = start_block + 1 - 1 = start_block`, the same single-block query is issued, -32005 is returned again, and the cycle repeats without bound. The only async yield is the awaited RPC call itself; there is no `tokio::time::sleep`, no `retry_with_max_elapsed_time!`, and no channel wait.

### Impact Explanation
Every bridge deposit submitted on Ethereum at or after block N is permanently unprocessed. The bridge's event consumer never receives those events, so the corresponding Sui-side mints or unlocks never occur. Funds are locked indefinitely without any on-chain mechanism to recover them, satisfying the **permanent fund lock** High-impact criterion.

### Likelihood Explanation
A bridge user needs to pack enough bridge-contract events into a single Ethereum block to exceed the RPC provider's per-query log limit (commonly 10,000 logs). This requires spending ETH on gas but is a fully public, permissionless action. Once triggered, the condition is self-sustaining: the historical block's event count cannot decrease, so the syncer never self-recovers without an operator restart against a different RPC endpoint.

### Recommendation
In the `new_range == query_range` branch, add an explicit sleep with exponential backoff before `continue`, mirroring the `retry_with_max_elapsed_time!` pattern used for other errors. For example:

```rust
if new_range == query_range {
    error!(...);
    // NEW: prevent tight spin when already at minimum window
    tokio::time::sleep(Duration::from_secs(backoff_secs)).await;
    backoff_secs = (backoff_secs * 2).min(MAX_BACKOFF_SECS);
    more_blocks = true;
    continue;
}
```

Alternatively, treat this condition identically to the non-32005 error path and route it through `retry_with_max_elapsed_time!`.

### Proof of Concept
1. Mock the RPC so that `get_events_in_range(addr, N, N)` always returns an error string containing `-32005`.
2. Initialize the syncer with `start_block = N` and allow `query_range` to be driven down to 1 by prior -32005 responses.
3. Run the syncer task and poll `start_block` (or the events channel) after 10,000 loop iterations.
4. Assert: `start_block` has not advanced past N, the events channel has received no entry for any block > N, and no sleep was observed between iterations — confirming the infinite spin with permanent fund lock.

### Citations

**File:** crates/sui-bridge/src/eth_syncer.rs (L23-23)
```rust
const ETH_LOG_QUERY_MAX_BLOCK_RANGE: u64 = 1000;
```

**File:** crates/sui-bridge/src/eth_syncer.rs (L140-144)
```rust
            if !more_blocks {
                last_finalized_block_receiver
                    .changed()
                    .await
                    .expect("last_finalized_block channel sender is closed");
```

**File:** crates/sui-bridge/src/eth_syncer.rs (L173-173)
```rust
                        let new_range = (query_range / 2).max(1);
```

**File:** crates/sui-bridge/src/eth_syncer.rs (L174-192)
```rust
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
```

**File:** crates/sui-bridge/src/eth_syncer.rs (L195-196)
```rust
                        more_blocks = true;
                        continue;
```

**File:** crates/sui-bridge/src/eth_syncer.rs (L199-205)
```rust
                    let Ok(Ok(events)) = retry_with_max_elapsed_time!(
                        eth_client.get_events_in_range(contract_address, start_block, end_block),
                        Duration::from_secs(600)
                    ) else {
                        error!("Failed to get events from eth client after retry");
                        continue;
                    };
```

**File:** crates/sui-bridge/src/eth_syncer.rs (L241-241)
```rust
            start_block = end_block + 1;
```
