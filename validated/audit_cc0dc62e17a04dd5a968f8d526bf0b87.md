### Title
Missing monotonicity check on B-20 `multiplier`/`uiMultiplier` allows the analogous "shrinking share price" drain of scaled-balance consumers - ([File: crates/common/precompiles/src/b20_asset/logic/v2.rs])

### Summary
The `IB20Asset` multiplier is the exact structural analog of ibBTC's `pricePerShare`: every scaled-balance conversion (`toScaledBalance`, `toRawBalance`, `scaledBalanceOf`, `balanceOfUI`, `totalSupplyUI`) divides/multiplies raw balances by this single value, just as `WrappedIbbtcEth.balanceToShares` divided transfer amounts by `pricePerShare`. Like the original finding, neither the instant `updateMultiplier` failsafe nor the scheduled `updateUIMultiplier` path enforces that the multiplier can only increase over time — they only validate non-zero / bounds, not directionality.

### Finding Description
`to_scaled_balance`/`to_raw_balance` compute conversions purely from the current effective multiplier: [1](#0-0) 

The lazy "effective multiplier" flips to a scheduled `pending_multiplier` once its `effective_at` matures, with no requirement that the new value exceed the old one: [2](#0-1) 

`update_ui_multiplier` (the canonical, ERC-8056 scheduled setter) only checks for zero, an upper bound (`MAX_UI_MULTIPLIER`), a future `effectiveAt`, and no live overlapping schedule — it never compares `new_multiplier` against the current/old multiplier to reject a decrease: [3](#0-2) 

The instant failsafe `update_multiplier` (V1, inherited by V2 as a deprecated but still dialable function) is even weaker — it only rejects zero: [4](#0-3) 

The interface doc explicitly frames the multiplier as accruing value over time ("corporate-action path (splits, reinvested dividends)"), i.e., a monotonically-increasing accrual value analogous to `pricePerShare`: [5](#0-4) 

Exactly as in the ibBTC report, any consumer that treats `toScaledBalance`/`scaledBalanceOf`/`balanceOfUI`/`totalSupplyUI` as a rebasing, ever-increasing accrual value (e.g., a pool, vault, or accounting system built on top of the B-20 precompile that assumes the multiplier never decreases) has no on-chain guarantee of that assumption, and no path exists to reject a bad/erroneous multiplier update before it is applied and read by dependents.

### Impact Explanation
If the operator role is compromised, buggy, or simply issues an erroneous update (analogous to "a bug or wrong data returning a smaller `pricePerShare` than it really is" in the original report), any downstream consumer computing raw-balance transfer amounts via `toRawBalance`/`toScaledBalance` will get skewed results. A decreased multiplier makes `to_raw_balance = scaledBalance * WAD / multiplier` return a *larger* raw amount for the same nominal scaled balance — the same mechanism that let Curve send more wibbtc than deserved in the original finding. This can misallocate/over-distribute the underlying raw token balance to whoever triggers a conversion-based settlement right after a bad update, i.e., unauthorized value transfer / loss of funds for other holders of the same token.

### Likelihood Explanation
Medium. It requires the multiplier setter (`OPERATOR_ROLE`) to push a decreasing value, whether via a genuine bug, mis-set corporate-action parameters, or a compromised/malicious operator key. Since a B-20 token creator is the one who grants `OPERATOR_ROLE` on their own token (as shown in the deployment/test flow), this is squarely within the actor set explicitly in scope ("B20 token creator"), and there is no code-level safeguard preventing it — the same absence of a sanity check that BadgerDAO confirmed as valid in the referenced report.

### Recommendation
Add a monotonicity invariant to both multiplier setters: `update_multiplier` (instant) and `update_ui_multiplier` (scheduled) should reject any `new_multiplier` (or scheduled pending value) that is less than the current effective multiplier, unless an explicit, separately-gated "corrective decrease" path is intended and documented. At minimum, emit/require an additional confirmation step or a maximum-decrease-per-update bound so that a single erroneous or malicious update cannot silently shrink the multiplier and corrupt every downstream `toRawBalance`/`toScaledBalance` computation.

### Proof of Concept
1. Token creator deploys a B-20 asset (V2) and grants themselves `OPERATOR_ROLE`, per the standard flow: [6](#0-5) 
2. Operator calls `updateMultiplier(newMultiplier)` with a value lower than the current multiplier — this succeeds because `update_multiplier` only checks `new_multiplier.is_zero()`: [7](#0-6) 
3. Any external system holding balances of this token and computing settlement amounts via `toRawBalance`/`toScaledBalance` now computes an inflated raw amount for the same UI/scaled balance, exactly mirroring the Curve-pool drain scenario from the ibBTC report — but no test or code path in the repo asserts/enforces that the new multiplier must be ≥ the old one before accepting the update.

### Citations

**File:** crates/common/precompiles/src/b20_asset/logic/v2.rs (L897-910)
```rust
    fn to_scaled_balance(&self, token: &B20AssetToken<S, A>, balance: U256) -> Result<U256> {
        let multiplier = self.effective_multiplier(token)?;
        let product =
            balance.checked_mul(multiplier).ok_or_else(BasePrecompileError::under_overflow)?;
        Ok(product / B20AssetStorage::WAD)
    }

    fn to_raw_balance(&self, token: &B20AssetToken<S, A>, balance: U256) -> Result<U256> {
        let multiplier = self.effective_multiplier(token)?;
        let product = balance
            .checked_mul(B20AssetStorage::WAD)
            .ok_or_else(BasePrecompileError::under_overflow)?;
        Ok(product / multiplier)
    }
```

**File:** crates/common/precompiles/src/b20_asset/logic/v2.rs (L928-939)
```rust
    fn effective_multiplier(&self, token: &B20AssetToken<S, A>) -> Result<U256> {
        let now = token.accounting().timestamp()?;
        let effective_at = token.accounting().pending_effective_at()?;
        if effective_at != 0 && now >= U256::from(effective_at) {
            let pending = token.accounting().pending_multiplier()?;
            // Both setters reject zero, so a stored pending is never zero. Do not fall back to WAD:
            // the Solidity reference also returns the raw pending value.
            debug_assert!(pending != 0, "matured pending multiplier must be non-zero");
            return Ok(U256::from(pending));
        }
        token.accounting().multiplier()
    }
```

**File:** crates/common/precompiles/src/b20_asset/logic/v2.rs (L970-1005)
```rust
    fn update_ui_multiplier(
        &self,
        token: &mut B20AssetToken<S, A>,
        caller: Address,
        new_multiplier: U256,
        effective_at: U256,
        privileged: bool,
    ) -> Result<()> {
        let now = token.accounting().timestamp()?;
        self.ensure_operator_role(token, caller, privileged)?;
        if new_multiplier.is_zero() || new_multiplier > Self::MAX_UI_MULTIPLIER {
            return Err(BasePrecompileError::revert(IB20Asset::InvalidMultiplier {}));
        }
        if effective_at <= now {
            return Err(BasePrecompileError::revert(IB20Asset::EffectiveAtInPast {
                effectiveAt: effective_at,
            }));
        }
        if effective_at > U256::from(u64::MAX) {
            return Err(BasePrecompileError::revert(IB20Asset::EffectiveAtTooFar {
                effectiveAt: effective_at,
            }));
        }

        let pending_effective_at = token.accounting().pending_effective_at()?;
        // A live pending blocks a new schedule.
        if U256::from(pending_effective_at) > now {
            return Err(BasePrecompileError::revert(IB20Asset::UIMultiplierUpdateExists {
                effectiveAt: U256::from(pending_effective_at),
            }));
        }
        // Fold a matured pending into the current multiplier before overwriting it.
        if pending_effective_at != 0 {
            let matured = U256::from(token.accounting().pending_multiplier()?);
            token.accounting_mut().set_multiplier(matured)?;
        }
```

**File:** crates/common/precompiles/src/b20_asset/logic/v1.rs (L630-645)
```rust
    fn update_multiplier(
        &self,
        token: &mut B20AssetToken<S, A>,
        caller: Address,
        new_multiplier: U256,
        privileged: bool,
    ) -> Result<()> {
        self.ensure_operator_role(token, caller, privileged)?;
        if new_multiplier.is_zero() {
            return Err(BasePrecompileError::revert(IB20Asset::InvalidMultiplier {}));
        }
        token.accounting_mut().set_multiplier(new_multiplier)?;
        token.accounting_mut().emit_event(
            IB20Asset::MultiplierUpdated { multiplier: new_multiplier }.encode_log_data(),
        )
    }
```

**File:** crates/common/precompiles/src/b20_asset/abi/v2.rs (L159-161)
```rust
        /// [V2] Schedules a single UI-multiplier update effective at `effectiveAt` — the canonical
        /// corporate-action path (splits, reinvested dividends). Requires `OPERATOR_ROLE`.
        function updateUIMultiplier(uint256 newMultiplier, uint256 effectiveAt) external;
```

**File:** etc/systems/tests/b20_precompile.rs (L374-398)
```rust
    b20.send_call(
        token,
        IB20::grantRoleCall { role: operator_role(), account: admin.address() },
        "grant B-20 operator role",
    )
    .await?;
    b20.send_call(
        token,
        IB20::grantRoleCall { role: B20TokenRole::Metadata.id(), account: admin.address() },
        "grant B-20 metadata role",
    )
    .await?;
    b20.send_call(
        token,
        IB20::grantRoleCall { role: B20TokenRole::Mint.id(), account: admin.address() },
        "grant B-20 mint role",
    )
    .await?;

    b20.send_call(
        token,
        IB20Asset::updateMultiplierCall { newMultiplier: UPDATED_MULTIPLIER },
        "update B-20 asset multiplier",
    )
    .await?;
```
