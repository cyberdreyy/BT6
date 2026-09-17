### Title
`operator_fee_refund` uses an unchecked `u64` subtraction that can underflow when refunded+remaining gas exceeds the gas limit - ([File: crates/common/evm/src/l1block.rs])

### Summary
`L1BlockInfo::operator_fee_refund` computes the Isthmus operator-fee refund by first computing "gas used" as a raw, unchecked `u64` subtraction: `gas.limit() - (gas.remaining() + gas.refunded() as u64)`. This is the same bug class as the reported USSD `USSDRebalancer` issue: an arithmetic subtraction on values whose relative ordering is not provably guaranteed by the surrounding code, performed without `checked_sub`/`saturating_sub`, on a hot path reachable by any ordinary signed transaction after Isthmus.

### Finding Description [1](#0-0) 

```rust
pub fn operator_fee_refund(&self, gas: &Gas, spec_id: BaseSpecId) -> U256 {
    if !spec_id.is_enabled_in(BaseUpgrade::Isthmus) {
        return U256::ZERO;
    }

    let operator_cost_gas_limit =
        self.operator_fee_charge_inner(U256::from(gas.limit()), spec_id);
    let operator_cost_gas_used = self.operator_fee_charge_inner(
        U256::from(gas.limit() - (gas.remaining() + gas.refunded() as u64)),
        spec_id,
    );

    operator_cost_gas_limit.saturating_sub(operator_cost_gas_used)
}
```

Every other arithmetic combination in this same file and its sibling `crates/common/l1-fees/src/params.rs` is deliberately written with `saturating_sub`/`checked_sub`/`saturating_add`/`saturating_mul` and accompanying comments explaining why the operation is safe (see e.g. `crates/common/evm/src/eip8130.rs` lines 1569-1585, which explicitly says "the subtraction never underflows" and still uses `checked_sub` to enforce it). `operator_fee_refund`, by contrast, performs `gas.limit() - (gas.remaining() + gas.refunded())` as plain `u64` arithmetic with no such guard.

The surrounding codebase's own gas-tracking tests demonstrate that `remaining` and `refunded` are not simple complements of gas spent under all code paths — the EIP-8037 "gas reservoir/spill" mechanism recovers reservoir/spill credit back onto `remaining` on revert/halt (see `crates/common/evm/src/handler.rs` lines 546-584, `test_reservoir_spill_recovered_on_revert`/`test_reservoir_recovered_but_spill_burned_on_halt`), showing that `remaining` can be inflated by spill-credit recovery in ways not obviously bounded relative to `limit - refunded`. Nothing in `operator_fee_refund` re-derives or validates that `gas.remaining() + gas.refunded() <= gas.limit()` before subtracting, unlike the rest of the fee-accounting code in this crate, which treats that invariant as something to be defensively checked rather than assumed.

In Rust, a native (non-wrapping) subtraction that underflows either panics (in debug builds, or any build with overflow-checks enabled — which is common for consensus-critical execution-client binaries) or silently wraps to a huge `u64` value (in release builds without overflow-checks). Either outcome is a direct match for the reported bug class: an unguarded subtraction of two values whose ordering isn't provably enforced, on a path that is reachable by every ordinary transaction (any single signed post-Isthmus transaction that finishes execution and hits fee settlement).

### Impact Explanation
- If built with overflow checks enabled, an underflow here panics inside block execution/fee-settlement, which is a **node halt / DoS** on that code path for every node reproducing the same execution (a chain-wide liveness issue since fee settlement is consensus-critical, not opt-in).
- If built without overflow checks (typical release Rust), the subtraction wraps to a value near `u64::MAX`, producing `operator_cost_gas_used` as an enormous, garbage operator-fee amount. `operator_cost_gas_limit.saturating_sub(operator_cost_gas_used)` then saturates to `0`, meaning the operator-fee refund silently becomes zero even though it should be a nonzero, positive credit back to the caller. That is an **incorrect fee accounting / loss of funds for the transaction sender** (the operator-fee vault effectively over-collects), which, if the wrapped condition is reachable deterministically for a class of transactions, is a systemic mischarge rather than a one-off computation error — and because fee settlement participates in state root computation, any node/version skew in how this arithmetic is compiled (checked vs. wrapping) would produce **different state roots for the same block**, i.e., a chain split.

### Likelihood Explanation
The precondition is that `gas.remaining() + gas.refunded() > gas.limit()` for some post-Isthmus transaction outcome. The exact circumstances under which the EIP-8037 reservoir/spill accounting (visible in this same crate's test suite) can produce such a combination were not something I could fully verify from the available context — I could not trace all call sites that construct the `Gas` object passed into `operator_fee_refund` to conclusively prove or disprove that the invariant always holds. What is verifiable is that (a) the function performs the subtraction with no checked/saturating guard, unlike every comparable computation in the surrounding fee-accounting code, and (b) the codebase's own tests show `remaining`/`refunded`-related fields are manipulated in non-trivial ways (reservoir spill recovery) that make the "obviously safe" assumption non-trivial to justify by inspection alone. Given the deliberate defensive style used everywhere else in this exact file and its sibling `l1-fees` crate, this looks like an overlooked instance of the same problem the surrounding code was written to avoid.

### Recommendation
Replace the raw subtraction with checked/saturating arithmetic consistent with the rest of the file, e.g.:
```rust
let gas_used = gas
    .limit()
    .checked_sub(gas.remaining().saturating_add(gas.refunded() as u64))
    .unwrap_or(0); // or propagate as a hard error, matching eip8130.rs's style
let operator_cost_gas_used = self.operator_fee_charge_inner(U256::from(gas_used), spec_id);
```
and add a comment (or an explicit invariant-violation error, mirroring `crates/common/evm/src/eip8130.rs`'s `"EIP-8130 priority amount underflow"` pattern) documenting why `remaining + refunded <= limit` is expected to hold, so a future violation surfaces as a diagnosable error instead of a panic or a silently wrong (zero) refund.

### Proof of Concept
Not constructible from the available index: reproducing the underflow requires driving revm's `Gas`/`GasTracker` (EIP-8037 reservoir/spill mechanics) into a concrete state where `remaining() + refunded() > limit()` for a transaction that reaches `operator_fee_refund`, which would require running the actual execution engine rather than static code inspection. I was not able to confirm from the indexed source alone whether current call sites can actually produce that state, only that the code does not defend against it the way parallel computations in the same crate do.

### Citations

**File:** crates/common/evm/src/l1block.rs (L238-251)
```rust
    pub fn operator_fee_refund(&self, gas: &Gas, spec_id: BaseSpecId) -> U256 {
        if !spec_id.is_enabled_in(BaseUpgrade::Isthmus) {
            return U256::ZERO;
        }

        let operator_cost_gas_limit =
            self.operator_fee_charge_inner(U256::from(gas.limit()), spec_id);
        let operator_cost_gas_used = self.operator_fee_charge_inner(
            U256::from(gas.limit() - (gas.remaining() + gas.refunded() as u64)),
            spec_id,
        );

        operator_cost_gas_limit.saturating_sub(operator_cost_gas_used)
    }
```
