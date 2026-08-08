### Title
Inconsistent commission-rounding between `VoteStateView::commission()` and RPC/account-decoder callers causes misreported validator commission - (File: `vote/src/vote_state_view/field_frames.rs`)

### Summary
`VoteStateView::commission()` derives the legacy percent-commission from `inflation_rewards_commission_bps` using floor division (`bps / 100`), while the JSON-RPC `getVoteAccounts` handler and the account-decoder's `parse_vote` (used by `getAccountInfo`/`getProgramAccounts` with `jsonParsed` encoding, and `account_subscribe` pubsub notifications) independently recompute the same field using ceiling division (`bps.div_ceil(100)`), for the exact same on-chain data.

### Finding Description
For V4 vote accounts, once `commission_rate_in_basis_points` is active, the authoritative field is `inflation_rewards_commission_bps` (a `u16`), and the legacy `u8` "percent" `commission` is a derived/informational value. Two independent implementations exist for deriving it:

- `CommissionView::commission_percent()` (used by `VoteStateView::commission()`) floors: `(bps / 100).min(u8::MAX as u16)`. [1](#0-0) 

- The RPC `getVoteAccounts` handler explicitly bypasses `VoteStateView::commission()` and recomputes with ceiling rounding instead: `bps.div_ceil(100).min(u8::MAX as u16) as u8`. [2](#0-1) 

- The account-decoder's `parse_vote` (backing `getAccountInfo`/`getProgramAccounts` with `encoding: "jsonParsed"`, and `accountSubscribe` pubsub notifications) also uses ceiling rounding directly on the raw deserialized struct: `vote_state.inflation_rewards_commission_bps.div_ceil(100).min(u8::MAX as u16) as u8`. [3](#0-2) 

The doc comment on the public `RpcVoteAccountInfo.commission` field even documents the ceiling behavior as the intended semantics: "After activation, this is derived from basis points with: `bps.div_ceil(100).min(255)`." [4](#0-3) 

However, `VoteStateView::commission()` — the general-purpose, zero-copy accessor meant to represent this same value — floors instead, and its own unit tests assert the floor behavior explicitly ("commission() should return bps / 100"). [5](#0-4) [6](#0-5) 

Because `VoteStateView` is the canonical decoder used broadly across the validator (including reward-commission logic when `commission_rate_in_basis_points` is not the active convention: `vote_state.commission() as u16 * 100`), while RPC and jsonParsed account decoding paths use a hand-rolled ceiling calculation, the same underlying `inflation_rewards_commission_bps` value (e.g., 150 bps = 1.5%) will be reported as `1%` via `VoteStateView::commission()`-based consumers but `2%` via `getVoteAccounts`/`getAccountInfo(jsonParsed)`/`accountSubscribe`. This is a decoder misreporting bug: unprivileged RPC/pubsub consumers querying the identical account state receive divergent commission percentages depending solely on which RPC method they call, none of which correctly represents the raw bps.

### Impact Explanation
This is purely a query-triggerable read/decode inconsistency (not a fund-moving bug by itself), but it constitutes account-decoder misreporting: two RPC-facing/pubsub-facing code paths return contradictory results for identical on-chain state. Any client or downstream tooling (delegator dashboards, staking UIs, wallets) that relies on the legacy `commission` percent field from `getVoteAccounts` versus `getAccountInfo`/`accountSubscribe` jsonParsed data can be misled about a validator's actual commission rate whenever `inflation_rewards_commission_bps` is not an exact multiple of 100 (any bps ending in 1-99), which is expected under SIMD-0291 since operators can now set arbitrary basis points.

### Likelihood Explanation
High likelihood of manifesting: any validator setting a non-round-hundred basis-points commission (e.g., 150, 275, 999 bps) will trigger the divergence on every subsequent `getVoteAccounts`, `getAccountInfo`/`getProgramAccounts` (jsonParsed), and `accountSubscribe` call — all single, unprivileged, low-cost JSON-RPC/pubsub requests requiring no special conditions.

### Recommendation
Consolidate the commission percent derivation into a single implementation. Either:
1. Change `CommissionView::commission_percent()` in `vote/src/vote_state_view/field_frames.rs` to use `bps.div_ceil(100)` to match the documented and RPC-facing behavior, and update its callers/tests accordingly; or
2. Change `rpc.rs`'s `get_vote_accounts` and `account-decoder/src/parse_vote.rs` to call `vote_state_view.commission()` / a shared helper instead of re-deriving the value locally.
Either way, ensure all read-only, unprivileged consumers (RPC handlers, pubsub, account decoding) compute the derived percent identically.

### Proof of Concept
1. Create/observe a V4 vote account with `inflation_rewards_commission_bps = 150` (1.5%) while `commission_rate_in_basis_points` feature is active.
2. Call `getVoteAccounts` (or the jsonParsed `getAccountInfo`/`accountSubscribe`) — both report `commission: 2` via `bps.div_ceil(100)`. [2](#0-1) [3](#0-2) 
3. Compare against any code path that calls `VoteStateView::commission()` directly on the same account data (e.g., internal reward-commission legacy conversion path `vote_state.commission() as u16 * 100`) — this returns `1` (100 bps) via floor division. [1](#0-0) [7](#0-6) 
4. The two values (`1` vs `2`) for identical on-chain state confirm the misreporting.

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

**File:** rpc/src/rpc.rs (L1206-1217)
```rust
                    commission: if commission_rate_in_basis_points {
                        // Derive percent from native bps, clamping to u8::MAX.
                        let bps = vote_state_view.inflation_rewards_commission();
                        bps.div_ceil(100).min(u8::MAX as u16) as u8
                    } else {
                        vote_state_view.commission()
                    },
                    inflation_rewards_commission_bps: Some(if commission_rate_in_basis_points {
                        vote_state_view.inflation_rewards_commission()
                    } else {
                        vote_state_view.commission() as u16 * 100
                    }),
```

**File:** account-decoder/src/parse_vote.rs (L33-36)
```rust
        commission: vote_state
            .inflation_rewards_commission_bps
            .div_ceil(100)
            .min(u8::MAX as u16) as u8,
```

**File:** rpc-client-types/src/response.rs (L409-413)
```rust
    /// An 8-bit unsigned integer used as a fraction (commission/100) for
    /// rewards payout. Before SIMD-0291 activation, this is the native
    /// commission value. After activation, this is derived from basis
    /// points with: `bps.div_ceil(100).min(255)`.
    pub commission: u8,
```

**File:** programs/vote/src/vote_state/handler.rs (L1760-1766)
```rust
        for bps in [0, 100, 500, 1_000, 5_000, 10_000] {
            handler.set_inflation_rewards_commission_bps(bps);
            let v4 = handler.as_ref_v4();
            assert_eq!(v4.inflation_rewards_commission_bps, bps);
            // commission() should return bps / 100
            assert_eq!(handler.commission(), (bps / 100) as u8);
        }
```

**File:** vote/src/vote_state_view.rs (L88-91)
```rust
    pub fn commission(&self) -> u8 {
        self.inflation_rewards_commission_view()
            .commission_percent()
    }
```

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L715-724)
```rust
            if commission_rate_in_basis_points {
                vote_state_for_commission.inflation_rewards_commission()
            } else {
                vote_state_for_commission.commission() as u16 * 100
            }
        } else if commission_rate_in_basis_points {
            vote_state.inflation_rewards_commission()
        } else {
            vote_state.commission() as u16 * 100
        };
```
