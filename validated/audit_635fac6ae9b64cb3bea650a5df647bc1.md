### Title
Non-saturating subtraction in `RewardCycleInfo::is_in_next_prepare_phase` causes panic/DoS on burn-height reorg dip below `first_burnchain_block_height` - (File: stacks-signer/src/runloop.rs)

### Summary
`RewardCycleInfo::is_in_reward_cycle` and `RewardCycleInfo::get_reward_cycle` both guard against `burnchain_block_height < first_burnchain_block_height` with `saturating_sub`, but `is_in_next_prepare_phase` computes `effective_height = burnchain_block_height - self.first_burnchain_block_height` with plain `u64` subtraction. This asymmetry means the same invariant that the sibling functions defend is silently unprotected here.

### Finding Description
The relevant code: [1](#0-0) 

`is_in_reward_cycle` (line 109) and `get_reward_cycle` (line 116) use `burnchain_block_height.saturating_sub(self.first_burnchain_block_height)`, explicitly handling the case where the queried height is behind the cached `RewardCycleInfo.first_burnchain_block_height` (e.g., right after a burnchain reorg delivers a `NewBurnBlock` event for a height below the previously-observed reward-cycle boundary, before the runloop has re-fetched/rotated `current_reward_cycle_info`). `is_in_next_prepare_phase` (line 122) does not apply the same guard and instead performs `burnchain_block_height - self.first_burnchain_block_height` directly.

If `burnchain_block_height < self.first_burnchain_block_height` when this line executes:
- In a debug build, this panics on subtraction overflow (`u64` underflow checks are enabled by default in `debug_assertions`), crashing the signer runloop thread.
- In a release build (no overflow checks), the subtraction wraps to a huge `u64` value, `effective_height % self.reward_cycle_length` becomes an essentially arbitrary `reward_index`, potentially satisfying `reward_index >= (self.reward_cycle_length - self.prepare_phase_block_length)` when it should not.

### Impact Explanation
If reachable, the debug-build panic path is a liveness wedge: the signer runloop stops processing further `NewBurnBlock`/block-proposal events, matching the "signer wedged into never signing valid blocks" High-severity category. The release-build wrap path could flip `is_in_next_prepare_phase`'s result and cause the runloop to prematurely instantiate/deconfigure a `ConfiguredSigner` for the next cycle, which is a stale-reward-set/threshold class of issue also in the High bucket.

### Likelihood Explanation
I could not confirm, within the code read, the exact call sites of `is_in_next_prepare_phase` or trace whether the runloop's `current_reward_cycle_info` is refetched/rotated strictly before this function is ever invoked with a height that could be behind `first_burnchain_block_height` (e.g., whether the burnchain client only ever calls this with monotonically non-decreasing heights relative to the cached `RewardCycleInfo`, or whether a genuine reorg-driven dip in reported height is possible given the attacker model of "one miner slot plus gossip"). Confirming reachability requires locating and reading every caller of `is_in_next_prepare_phase` in the runloop's event-processing path and the code that constructs/refreshes `RewardCycleInfo`, which was not fully completed here.

### Recommendation
For consistency and defense-in-depth, change `is_in_next_prepare_phase` to mirror its siblings:
```rust
let effective_height = burnchain_block_height.saturating_sub(self.first_burnchain_block_height);
```
This closes the asymmetry regardless of whether the current callers can actually trigger it.

### Proof of Concept
```rust
#[test]
fn is_in_next_prepare_phase_underflow() {
    let info = RewardCycleInfo {
        reward_cycle: 5,
        reward_cycle_length: 100,
        prepare_phase_block_length: 10,
        first_burnchain_block_height: 1000,
        last_burnchain_block_height: 1000,
    };
    // burnchain_block_height < first_burnchain_block_height
    let result = info.is_in_next_prepare_phase(999);
    // In debug builds this panics on subtraction overflow before reaching this assert.
    // In release builds, assert the wrapped value produces an incorrect result:
    assert!(!result, "should not be in next prepare phase for a height before cycle start");
}
```

Given that I could not fully verify the caller-side reachability of a sub-`first_burnchain_block_height` height reaching this function under the stated unprivileged, single-miner-slot attacker model, I flag this with reduced confidence rather than full certainty; the code asymmetry itself is confirmed and real.

### Citations

**File:** stacks-signer/src/runloop.rs (L106-128)
```rust
impl RewardCycleInfo {
    /// Check if the provided burnchain block height is part of the reward cycle
    pub const fn is_in_reward_cycle(&self, burnchain_block_height: u64) -> bool {
        let blocks_mined = burnchain_block_height.saturating_sub(self.first_burnchain_block_height);
        let reward_cycle = blocks_mined / self.reward_cycle_length;
        self.reward_cycle == reward_cycle
    }

    /// Get the reward cycle for a specific burnchain block height
    pub const fn get_reward_cycle(&self, burnchain_block_height: u64) -> u64 {
        let blocks_mined = burnchain_block_height.saturating_sub(self.first_burnchain_block_height);
        blocks_mined / self.reward_cycle_length
    }

    /// Check if the provided burnchain block height is in the prepare phase of the next cycle
    pub fn is_in_next_prepare_phase(&self, burnchain_block_height: u64) -> bool {
        let effective_height = burnchain_block_height - self.first_burnchain_block_height;
        let reward_index = effective_height % self.reward_cycle_length;

        reward_index >= (self.reward_cycle_length - self.prepare_phase_block_length)
            && self.get_reward_cycle(burnchain_block_height) == self.reward_cycle
    }
}
```
