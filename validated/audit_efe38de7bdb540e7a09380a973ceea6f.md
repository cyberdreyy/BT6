### Title
Integer overflow panic in `LeaderSchedule::get_leader_upcoming_slots` via crafted `offset` on small/degenerate schedules - ([File: leader-schedule/src/vote_keyed.rs])

### Summary
`get_leader_upcoming_slots` computes `start_offset = start_index + offset / num_slots * size` and then builds a second sub-iterator starting at `start_offset + 1` using plain (non-checked, non-saturating) `usize` arithmetic. For a schedule with very small `num_slots()` (e.g., a single-leader / `repeat = 1` schedule) and an `offset` near `usize::MAX`, `start_offset` itself can reach `usize::MAX`, making the subsequent `start_offset + 1` overflow and panic in overflow-checked builds.

### Finding Description
`LeaderSchedule::get_leader_upcoming_slots` performs: [1](#0-0) 
followed by construction of a `RangeFrom` from `start_offset + 1`: [2](#0-1) 

With a schedule built for a single leader and `repeat = 1` (`num_slots() == 1`, `size == 1`), passing `offset = usize::MAX` yields:
- `offset_in_epoch = offset % num_slots = 0`
- `start_offset = start_index + offset / num_slots * size = 0 + usize::MAX * 1 = usize::MAX`
- The chained iterator then eagerly evaluates `start_offset + 1`, i.e., `usize::MAX + 1`, which overflows `usize` at the moment the function is called (not lazily during iteration), because the addition is a plain arithmetic expression with no `checked_add`/`saturating_add`/`wrapping_add` guard.

The crate only sets `#![allow(clippy::arithmetic_side_effects)]` at the lint level [3](#0-2) , which suppresses the clippy lint but does not change runtime overflow-checking behavior — if the binary is built with `overflow-checks = true` (common in Solana/Agave release profiles for safety), this becomes a real panic.

The `repeat` value is normally `NUM_CONSECUTIVE_LEADER_SLOTS = 4` [4](#0-3) , but `LeaderSchedule::new_from_schedule` and `LeaderSchedule::new` both accept an arbitrary caller-supplied `NonZeroUsize` for `repeat` [5](#0-4) , and the number of stake-weighted slot leaders (`L`, hence `num_slots`) is driven by the number of active vote accounts, which the attacker does not control directly but which can legitimately be very small in low-participation clusters/test networks. Thus a small `num_slots()` combined with `offset` near `usize::MAX` is a state that is reachable given the function's public contract, independent of exact RPC wiring.

### Impact Explanation
If reached through an RPC path that converts a client-supplied slot number into a large `offset` for `get_leader_upcoming_slots` (e.g., `getLeaderSchedule` with an identity filter, as posited in the question), a single unprivileged, low-rate request could crash the validator process (arithmetic overflow panic) — matching the "validator-process crash from one request" bounty category. I was unable to confirm within the indexed portion of this repository the exact RPC handler that maps a client `slot` argument into this `offset` (searches only surfaced the definition in `leader-schedule/src/vote_keyed.rs`, its re-export in `leader-schedule/src/lib.rs`, and a single, unreadable reference in `ledger/src/leader_schedule_cache.rs`); the crate is additionally gated behind `#![cfg(feature = "agave-unstable-api")]` [6](#0-5) , suggesting this vote-keyed schedule implementation may not yet be the one wired into the stable `getLeaderSchedule` RPC surface in this snapshot. This should be verified with full repository/RPC-layer access before treating it as externally reachable today.

### Likelihood Explanation
The arithmetic bug itself is deterministic and trivially reproducible via the public `LeaderSchedule` API with a hand-crafted schedule (single leader, `repeat = 1`, `offset = usize::MAX`) — no special privileges are needed to construct this state in a unit test. Its real-world exploitability depends entirely on (a) whether overflow checks are enabled in the deployed binary, and (b) whether/how an RPC method exposes a caller-controlled `offset` of this magnitude to `get_leader_upcoming_slots`; neither could be conclusively confirmed from the available index.

### Recommendation
Replace the raw arithmetic in `get_leader_upcoming_slots` with checked/saturating operations (e.g., `checked_mul`, `checked_add`, or working in `u128`/`Slot` with explicit modular reduction), and explicitly define behavior for `offset` values that would drive the computed slot number past representable ranges (e.g., clamp/saturate rather than panic), plus add a regression test with `offset = usize::MAX` and minimal (`num_slots = 1`, `repeat = 1`) schedules.

### Proof of Concept
```rust
// leader-schedule/src/vote_keyed.rs (test module)
#[test]
fn test_get_leader_upcoming_slots_offset_overflow() {
    let leader_a = SlotLeader::new_unique();
    let leader_schedule = LeaderSchedule::new_from_schedule(vec![leader_a], NZ_1);
    // num_slots() == 1, size == 1; offset near usize::MAX triggers
    // start_offset == usize::MAX, and building the second range
    // `(start_offset + 1)..` overflows in overflow-checked builds.
    let _ = leader_schedule
        .get_leader_upcoming_slots(&leader_a.id, usize::MAX)
        .take(1)
        .collect::<Vec<_>>();
}
```
Run with `RUSTFLAGS="-C overflow-checks=on"` (or a debug/overflow-checked profile) to observe the `attempt to add with overflow` panic. Expected assertion for a fixed implementation: the call returns without panicking and yields either an empty iterator or a well-defined saturated/wrapped slot sequence.

### Citations

**File:** leader-schedule/src/vote_keyed.rs (L29-66)
```rust
    pub fn new(
        vote_accounts_map: &VoteAccountsHashMap,
        epoch: Epoch,
        len: usize,
        repeat: NonZeroUsize,
    ) -> Self {
        let slot_leader_stakes: Vec<_> = vote_accounts_map
            .iter()
            .filter(|(_pubkey, (stake, _account))| *stake > 0)
            .map(|(&vote_address, (stake, vote_account))| {
                (
                    SlotLeader {
                        vote_address,
                        id: *vote_account.node_pubkey(),
                    },
                    *stake,
                )
            })
            .collect();
        let slot_leaders = stake_weighted_slot_leaders(slot_leader_stakes, epoch, len, repeat);
        Self {
            leader_slots_map: Self::invert_slot_leaders(
                &slot_leaders,
                Some(vote_accounts_map.len()),
            ),
            slot_leaders,
            repeat,
        }
    }

    pub fn new_from_schedule(slot_leaders: Vec<SlotLeader>, repeat: NonZeroUsize) -> Self {
        let leader_slots_map = Self::invert_slot_leaders(&slot_leaders, None);
        Self {
            slot_leaders,
            leader_slots_map,
            repeat,
        }
    }
```

**File:** leader-schedule/src/vote_keyed.rs (L106-119)
```rust
            Some(index) if !index.is_empty() => {
                let size = index.len();
                let offset_in_epoch = offset % num_slots;
                let repeat = self.repeat();
                let offset_chunk = offset_in_epoch / repeat;
                // We don't store repetitions in the schedule, so we need to find the
                // first element representing the latest chunk of `repeat` slots.
                // Also, find out how many slots from the starting chunk we still have
                // to yield.
                let (start_index, offset_in_chunk) = match index.binary_search(&offset_chunk) {
                    Ok(index) => (index, offset_in_epoch % repeat),
                    Err(index) => (index, 0),
                };
                let start_offset = start_index + offset / num_slots * size;
```

**File:** leader-schedule/src/vote_keyed.rs (L130-133)
```rust
                    ((start_offset + 1)..).flat_map(move |k| {
                        (0..repeat)
                            .map(move |j| index[k % size] * repeat + k / size * num_slots + j)
                    }),
```

**File:** leader-schedule/src/lib.rs (L1-4)
```rust
//! Solana leader schedule.

#![cfg(feature = "agave-unstable-api")]
#![allow(clippy::arithmetic_side_effects)]
```

**File:** leader-schedule/src/lib.rs (L20-20)
```rust
pub const NUM_CONSECUTIVE_LEADER_SLOTS: NonZeroUsize = NonZeroUsize::new(4).unwrap();
```
