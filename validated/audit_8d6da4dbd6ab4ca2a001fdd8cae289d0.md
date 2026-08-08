### Title
`VoteStateView::commission()` silently clamps/truncates the true SIMD-185 commission-bps value, causing RPC/decoder misreporting of a validator's real commission - ([File: vote/src/vote_state_view/field_frames.rs])

### Summary
`VoteStateView::commission()` is the field used by unprivileged, read-only paths (RPC `getVoteAccounts`, account decoding for `jsonParsed` vote accounts) to report a validator's commission as a `u8` percentage. When the vote account uses the newer bps-based commission field (SIMD-185, `inflation_rewards_commission_bps`), the view divides the raw `u16` bps value by 100 and then silently `.min(u8::MAX)`-clamps the result instead of erroring or flagging truncation, exactly the same "continue returning a bounded value instead of the true value" pattern flagged in the Chainlink `minAnswer`/`maxAnswer` report.

### Finding Description
`CommissionView::commission_percent()` computes the displayed commission as follows: [1](#0-0) 

```
pub(super) fn commission_percent(&self) -> u8 {
    if !self.frame.use_bps {
        self.buffer[0]
    } else {
        let data = unsafe { *(self.buffer.as_ptr() as *const [u8; 2]) };
        let bps = u16::from_le_bytes(data);
        let percent = (bps / 100).min(u8::MAX as u16);
        percent as u8
    }
}
```

This is exposed through `VoteStateView::commission()`: [2](#0-1) 

The true value is only faithfully represented by `inflation_rewards_commission()` / `commission_bps()`, which returns the full `u16` bps without clamping: [3](#0-2) 

The project's own test explicitly documents the truncation/clamp behavior as intended: [4](#0-3) 

Because a V4 vote account's `inflation_rewards_commission_bps` is a `u16` (range 0–65535, i.e., up to 655.35%), any value above 25500 bps (255%) is silently clamped to the `u8` display value of `255`, and any value not an exact multiple of 100 bps is floor-rounded. Callers that only read `commission()` (the RPC/CLI-facing `u8` percent) — rather than `inflation_rewards_commission()` (the `u16` bps) — get a value that no longer faithfully reflects the underlying, consensus-relevant commission stored on-chain. This is reachable purely by decoding attacker/validator-controlled vote account data supplied over gossip/turbine and stored in accounts-db; no special privilege is required to trigger it, since any vote account owner can set `inflation_rewards_commission_bps` to an out-of-percent-range value and any unprivileged RPC caller/decoder will observe the misreported value.

### Impact Explanation
This falls under the "decoder panic and misreporting" acceptable-impact category: an RPC client (`getVoteAccounts`) or the account-decoder's `jsonParsed` vote-account view can be made to report an incorrect commission for a validator (capped at 255% and rounded down), analogous to the Chainlink oracle continuing to report `minPrice`/`maxPrice` instead of the true value once the real value passes a bound. Downstream consumers (delegators, staking UIs, monitoring tools) that trust the `u8` commission field would be misled about the validator's actual take-rate, potentially causing stakers to delegate based on incorrect information. It does not cause a validator crash, consensus-state mutation, or direct fund loss on its own, since the actual reward-distribution logic in `runtime/src/inflation_rewards/mod.rs` and `programs/vote/src/vote_state/handler.rs` operates on the un-clamped bps value, not on `commission()`.

### Likelihood Explanation
Low but concrete: a commission bps value above 25500 (i.e., >255%) is an edge case, similar in spirit to the "unlikely edge case" acknowledged as still valid-medium in the original Chainlink report's accepted escalation. It requires a vote account to actually set an extreme bps value, which is permitted by the on-chain vote program's commission-setting instruction range (`u16`), so it is reachable without any validator/operator privilege — any RPC caller decoding such an account is affected.

### Recommendation
Have `VoteStateView::commission()` (and any RPC/decoder path that surfaces it) either: (1) return the bps-precision value directly and let API consumers do their own percent conversion, or (2) explicitly signal truncation/clamping (e.g., return `Option<u8>` or include the raw bps alongside the percent) rather than silently rounding/clamping. At minimum, `account-decoder`/`transaction-status` jsonParsed output and `rpc::rpc.rs`'s `getVoteAccounts` should prefer `inflation_rewards_commission()` (bps) as the authoritative field and treat `commission()` as a lossy legacy/display-only convenience, documented as such.

### Proof of Concept
1. Construct a `VoteStateV4` account with `inflation_rewards_commission_bps = 60000` (600%).
2. Serialize it as done in the existing unit test `test_vote_state_view_v4_commission_clamps`: [5](#0-4) 
3. Call `VoteStateView::commission()` on the resulting view — it returns `255` instead of a value reflecting 600%, while `inflation_rewards_commission()` correctly returns `60000`.
4. Any RPC client calling `getVoteAccounts` or decoding the account with `jsonParsed` encoding that surfaces the `u8` commission field receives the misleading `255` value instead of the true rate, without any error or indication that clamping occurred.

### Citations

**File:** vote/src/vote_state_view/field_frames.rs (L320-330)
```rust
impl CommissionView<'_> {
    pub(super) fn commission_percent(&self) -> u8 {
        if !self.frame.use_bps {
            self.buffer[0]
        } else {
            let data = unsafe { *(self.buffer.as_ptr() as *const [u8; 2]) };
            let bps = u16::from_le_bytes(data);
            let percent = (bps / 100).min(u8::MAX as u16);
            percent as u8
        }
    }
```

**File:** vote/src/vote_state_view/field_frames.rs (L332-339)
```rust
    pub(super) fn commission_bps(&self) -> u16 {
        if !self.frame.use_bps {
            100 * self.buffer[0] as u16
        } else {
            let data = unsafe { *(self.buffer.as_ptr() as *const [u8; 2]) };
            u16::from_le_bytes(data)
        }
    }
```

**File:** vote/src/vote_state_view/field_frames.rs (L500-506)
```rust

        // over u8 max
        let frame = CommissionFrame::new_bps();
        let buffer = u16::MAX.to_le_bytes();
        let commission_view = CommissionView::new(frame, &buffer);
        assert_eq!(commission_view.commission_percent(), u8::MAX);
    }
```

**File:** vote/src/vote_state_view.rs (L88-91)
```rust
    pub fn commission(&self) -> u8 {
        self.inflation_rewards_commission_view()
            .commission_percent()
    }
```

**File:** vote/src/vote_state_view.rs (L824-844)
```rust
    #[test]
    fn test_vote_state_view_v4_commission_clamps() {
        // The VoteStateView commission() getter must clamp values
        // > u8::MAX to u8::MAX for V4 accounts with large bps.
        for (bps, expected) in [
            (0u16, 0u8),
            (10_000, 100),
            (25_500, 255),
            (25_600, 255),
            (u16::MAX, 255),
        ] {
            let state = VoteStateV4 {
                inflation_rewards_commission_bps: bps,
                ..VoteStateV4::default()
            };
            let versioned = VoteStateVersions::new_v4(state);
            let buf = Arc::new(bincode::serialize(&versioned).unwrap());
            let view = VoteStateView::try_new(buf).unwrap();
            assert_eq!(view.commission(), expected);
        }
    }
```
