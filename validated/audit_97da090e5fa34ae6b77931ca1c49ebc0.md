### Title
Potential division-by-zero panic when `flashblocks_per_block` rounds down to zero - ([File: crates/builder/core/src/flashblocks/payload.rs])

### Summary
The reported Sherlock issue is a Solidity integer-division-truncation bug: `timePassed / stepSize` rounds to zero when `timePassed < stepSize`, silently breaking downstream pricing math. The closest reachable analog in this Base repository is the flashblocks-per-block calculation in the block builder, where an analogous integer division can truncate to zero and is then used as a divisor later in the same code path.

### Finding Description
`BuilderConfig::flashblocks_per_block` computes the number of flashblocks per block as `block_time.as_millis() / flashblocks_interval.as_millis()`, an integer division that truncates to `0` whenever `flashblocks_interval > block_time` (or when timing drift causes `calculate_flashblocks` to derive a small `flashblocks_per_block`). [1](#0-0) 

In `crates/builder/core/src/flashblocks/payload.rs`, the code detects the `flashblocks_per_block == 0` case and only **logs an error** (via `record_flashblocks_metrics`) rather than unconditionally short-circuiting for all callers: [2](#0-1) 

Shortly after, the value is used directly as a divisor to compute per-flashblock gas and DA budgets: [3](#0-2) 

In Rust, integer division by zero (`x / 0`) panics at runtime (unlike Solidity, which would simply revert, but here it's on the block-builder hot path). If `skip_flashblocks_building` is not set for a request where `flashblocks_per_block` truncates to `0` (e.g., `no_tx_pool` is false, and the guard on line 372 does not cause an early return before line 404), this would panic the builder task and produce a node halt rather than a corrupted price.

### Impact Explanation
If reachable, a panic in the block-building hot path constitutes a node/service halt on the sequencer/builder — the block-building thread would crash, preventing block production until restarted. This is analogous in bug class (integer truncation to zero feeding a later divisor) to the referenced audit finding, though the consequence in Rust (a panic) differs from the Solidity case (stuck/incorrect economic value).

### Likelihood Explanation
I could **not fully verify** whether the `flashblocks_per_block == 0` branch at line 372 actually returns early before reaching the division at line 404, because the file read to confirm the intervening control flow (lines 300–412) failed before I could inspect it, and I have no further tool calls available. Based on the visible excerpt, the `if flashblocks_per_block == 0 && !ctx.attributes().no_tx_pool` block only logs and records metrics — it does not visibly `return`, and the `if skip_flashblocks_building { ... return Ok(payload); }` check appears to be a **separate** flag whose relationship to `flashblocks_per_block == 0` is not confirmed from the visible code. This is a genuine gap in my verification.

### Recommendation
Confirm (via full read of `crates/builder/core/src/flashblocks/payload.rs` around lines 300–412) whether `skip_flashblocks_building` is guaranteed to be `true` whenever `flashblocks_per_block == 0`. If not, add an explicit guard to return/skip flashblock-based building (falling back to a single non-flashblocks build) whenever `flashblocks_per_block() == 0`, before any division by it occurs, mirroring the recommendation in the original report to explicitly guard against the zero-step case rather than relying on log-only detection.

### Proof of Concept
Not able to construct a concrete reproduction without confirming the control-flow gap noted above (need to verify whether `skip_flashblocks_building` truly gates out the zero case). Recommend a Devin/engineering follow-up to trace the full function (`build_block_flashblocks` or equivalent) in `crates/builder/core/src/flashblocks/payload.rs` to determine if `block_time < flashblocks_interval` (a valid, operator-configurable state per `BuilderConfig`) can reach the `ctx.block_gas_limit() / flashblocks_per_block` division with `flashblocks_per_block == 0`.

### Citations

**File:** crates/builder/core/src/config.rs (L91-98)
```rust
impl BuilderConfig {
    /// Returns the number of flashblocks per block.
    pub const fn flashblocks_per_block(&self) -> u64 {
        if self.block_time.as_millis() == 0 {
            return 0;
        }
        (self.block_time.as_millis() / self.flashblocks_interval.as_millis()) as u64
    }
```

**File:** crates/builder/core/src/flashblocks/payload.rs (L371-386)
```rust
        // fcu just arrived late, not syncing
        if flashblocks_per_block == 0 && !ctx.attributes().no_tx_pool {
            error!(
                target: "payload_builder",
                message = "FCU arrived too late or system clock are unsynced, building 0 flashblocks",
                timestamp,
            );

            self.record_flashblocks_metrics(
                &ctx,
                &info,
                flashblocks_per_block,
                &span,
                "FCU arrived too late or system clock are unsynced, building 0 flashblocks",
            );
        }
```

**File:** crates/builder/core/src/flashblocks/payload.rs (L404-411)
```rust
        let gas_per_batch = ctx.block_gas_limit() / flashblocks_per_block;
        let da_per_batch = ctx
            .builder_config
            .da_config
            .max_da_block_size()
            .map(|da_limit| da_limit / flashblocks_per_block);
        let da_footprint_per_batch =
            info.da_footprint_scalar.map(|_| ctx.block_gas_limit() / flashblocks_per_block);
```
