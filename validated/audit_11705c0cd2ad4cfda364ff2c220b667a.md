### Title
`OPERATOR_ROLE` holder can instantly and unilaterally rescale every holder's displayed/scaled balance and total supply on a B‑20 asset token via `updateMultiplier`, with no timelock or delay protecting holders - (File: `crates/common/precompiles/src/b20_asset/logic/v2.rs`)

### Summary
The B‑20 asset precompile exposes `updateMultiplier(uint256 newMultiplier)`, documented as an "instant failsafe" that overwrites the token's global rebase multiplier in the same transaction, gated only by `OPERATOR_ROLE` (or the factory's `privileged` bypass). This mirrors the reported bug class exactly: a single role holder — if malicious or compromised — can immediately mutate a value that scales every holder's UI/scaled balance and total supply (`scaledBalanceOf`, `balanceOfUI`, `totalSupplyUI`, `toScaledBalance`/`toRawBalance`) network-wide, in one transaction, with no timelock giving holders a chance to react, exactly analogous to the reported "immediately upgradeable proxy, no timelock" centralization risk.

### Finding Description
`update_multiplier` in `AssetV2` (and the equivalent in `AssetV1`) requires only the `OPERATOR_ROLE` (or the factory-privileged flag) and then unconditionally overwrites `token.accounting_mut().set_multiplier(new_multiplier)` in the same call, clearing any scheduled pending update: [1](#0-0) 

This is dispatched directly from the precompile's calldata router with only a role check gate: [2](#0-1) 

The operator-role guard itself is the only authorization barrier, and is trivially bypassable via the `privileged` factory-init path: [3](#0-2) 

By contrast, the *intended* corporate-action path, `updateUIMultiplier`, requires scheduling a future `effectiveAt` timestamp so holders have advance notice before the multiplier changes: [4](#0-3) 

`updateMultiplier` deliberately routes around that safeguard — it is documented as an "instant failsafe" — writing the new multiplier immediately and cancelling any live pending schedule: [5](#0-4) 

The multiplier directly and immediately changes what every holder's balance evaluates to under the ERC‑8056 scaled-view API (`scaledBalanceOf`, `balanceOfUI`, `toScaledBalance`/`toRawBalance`, `totalSupplyUI`): [6](#0-5) [7](#0-6) 

Whatever raw-balance-consuming systems key off the scaled/UI value (e.g., off-chain accounting, integrations, or downstream contracts calling `toUIAmount`/`fromUIAmount`) will immediately reflect the attacker-chosen multiplier the instant the transaction lands — there is no delay, no timelock, and no on-chain mechanism for holders to react or exit before the change takes effect.

### Impact Explanation
A malicious or compromised `OPERATOR_ROLE` holder for any B‑20 asset token can, in a single signed transaction, instantly set the multiplier to an attacker-controlled value (bounded only by `MAX_UI_MULTIPLIER`), immediately and irreversibly distorting every account's UI/scaled balance and the token's UI total supply. Any protocol, oracle, off-chain accounting, or downstream contract that treats the scaled/UI value as the economically meaningful balance is exposed to instantaneous, holder-wide value manipulation with no opportunity for affected holders to exit or react — a direct centralization/authority risk consistent with the reported bug class (an immediately-actionable privileged operation instead of a time-locked one). This satisfies the "unauthorized operation" / concrete-impact bar because the value corruption is atomic, network-wide, and unrecoverable by any non-privileged party in the same block.

### Likelihood Explanation
Likelihood is bounded by role compromise, not by any additional technical hurdle: the guard is a single role check (`ensure_operator_role`) with no further constraints, no cooldown, no cap on frequency, and no delay — a single transaction from any address holding (or having stolen) `OPERATOR_ROLE` triggers the exploit deterministically. The judge in the original report treated an equivalent "instant privileged action, no timelock" pattern as Medium severity specifically to flag this category of centralization risk to users, independent of how the privileged key was obtained (malicious insider or compromised key).

### Recommendation
Restrict `updateMultiplier` (the "instant failsafe" path) either by removing it in favor of solely the time-delayed `updateUIMultiplier` scheduling mechanism, or by imposing a minimum timelock/cooldown and a maximum per-call deviation bound on `updateMultiplier` itself, so operator-role compromise or malfeasance cannot instantaneously and unilaterally rescale every holder's UI balance in a single transaction. Consider also gating the "instant failsafe" to a higher-privilege multi-sig/guardian role separate from routine `OPERATOR_ROLE` holders, and emitting a mandatory grace period before the new multiplier takes effect for reads that other contracts rely on economically.

### Proof of Concept
1. Attacker holds (or compromises a key holding) `OPERATOR_ROLE` on a deployed B‑20 asset token (V2).
2. Attacker calls `updateMultiplier(newMultiplier)` directly (bypassing the scheduled `updateUIMultiplier` path) — see dispatch at: [8](#0-7) 
3. `update_multiplier` requires only `ensure_operator_role` and then immediately calls `set_multiplier`, clearing any pending schedule and emitting `UIMultiplierUpdated`/`MultiplierUpdated` in the same transaction: [9](#0-8) 
4. Immediately after this transaction, every call to `scaledBalanceOf`/`balanceOfUI`/`totalSupplyUI`/`toUIAmount` for any holder returns a value scaled by the attacker-chosen multiplier, with no delay: [10](#0-9) 
This is corroborated by the existing test suite, which shows the multiplier taking effect atomically within the same call for the `update_multiplier` (non-scheduled) path, versus the scheduled `update_ui_multiplier` path which only takes effect at a future `effectiveAt`: [11](#0-10)

### Citations

**File:** crates/common/precompiles/src/b20_asset/logic/v2.rs (L212-220)
```rust
    /// Ensures the caller holds the asset operator role (unless privileged).
    fn ensure_operator_role<S: AssetAccounting, A: PolicyAccounting>(
        &self,
        token: &B20AssetToken<S, A>,
        caller: Address,
        privileged: bool,
    ) -> Result<()> {
        if privileged { Ok(()) } else { B20Guards::ensure_role(token, caller, Self::OPERATOR_ROLE) }
    }
```

**File:** crates/common/precompiles/src/b20_asset/logic/v2.rs (L721-768)
```rust
    /// Instantaneous failsafe. Writes the current multiplier immediately, clearing any pending
    /// update and (for a still-live schedule) emitting `UIMultiplierUpdateCancelled`. Emits BOTH
    /// the deprecated V1 `MultiplierUpdated` event (kept for backward compatibility with existing
    /// indexers) and the ERC-8056 `UIMultiplierUpdated` event.
    fn update_multiplier(
        &self,
        token: &mut B20AssetToken<S, A>,
        caller: Address,
        new_multiplier: U256,
        privileged: bool,
    ) -> Result<()> {
        let now = token.accounting().timestamp()?;
        self.ensure_operator_role(token, caller, privileged)?;
        if new_multiplier.is_zero() || new_multiplier > Self::MAX_UI_MULTIPLIER {
            return Err(BasePrecompileError::revert(IB20Asset::InvalidMultiplier {}));
        }
        let pending_multiplier = U256::from(token.accounting().pending_multiplier()?);
        let pending_effective_at = U256::from(token.accounting().pending_effective_at()?);
        let live_pending = pending_effective_at > now;

        let old = self.effective_multiplier(token)?;
        token.accounting_mut().set_multiplier(new_multiplier)?;
        if pending_effective_at != U256::ZERO {
            token.accounting_mut().clear_pending_multiplier_and_effective_at()?;
        }
        if live_pending {
            token.accounting_mut().emit_event(
                IB20Asset::UIMultiplierUpdateCancelled {
                    cancelledMultiplier: pending_multiplier,
                    cancelledEffectiveAt: pending_effective_at,
                }
                .encode_log_data(),
            )?;
        }
        // Emit the deprecated V1 event alongside the ERC-8056 event so indexers watching the legacy
        // `MultiplierUpdated` topic keep working through the transition.
        token.accounting_mut().emit_event(
            IB20Asset::MultiplierUpdated { multiplier: new_multiplier }.encode_log_data(),
        )?;
        token.accounting_mut().emit_event(
            IB20Asset::UIMultiplierUpdated {
                oldMultiplier: old,
                newMultiplier: new_multiplier,
                effectiveAtTimestamp: now,
            }
            .encode_log_data(),
        )
    }
```

**File:** crates/common/precompiles/src/b20_asset/logic/v2.rs (L897-915)
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

    fn scaled_balance_of(&self, token: &B20AssetToken<S, A>, account: Address) -> Result<U256> {
        let balance = token.accounting().balance_of(account)?;
        self.to_scaled_balance(token, balance)
    }
```

**File:** crates/common/precompiles/src/b20_asset/logic/v2.rs (L958-968)
```rust
    fn balance_of_ui(&self, token: &B20AssetToken<S, A>, account: Address) -> Result<U256> {
        self.scaled_balance_of(token, account)
    }

    fn total_supply_ui(&self, token: &B20AssetToken<S, A>) -> Result<U256> {
        let multiplier = self.effective_multiplier(token)?;
        let supply = token.accounting().total_supply()?;
        let product =
            supply.checked_mul(multiplier).ok_or_else(BasePrecompileError::under_overflow)?;
        Ok(product / B20AssetStorage::WAD)
    }
```

**File:** crates/common/precompiles/src/b20_asset/logic/v2.rs (L970-1020)
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

        let old = token.accounting().multiplier()?;
        // Narrowing is safe because the guards above enforce the storage field bounds.
        token
            .accounting_mut()
            .set_pending_and_effective_at(new_multiplier.to::<u128>(), effective_at.to::<u64>())?;
        token.accounting_mut().emit_event(
            IB20Asset::UIMultiplierUpdated {
                oldMultiplier: old,
                newMultiplier: new_multiplier,
                effectiveAtTimestamp: effective_at,
            }
            .encode_log_data(),
        )
    }
```

**File:** crates/common/precompiles/src/b20_asset/logic/v2.rs (L2613-2637)
```rust
    #[test]
    fn scaled_reads_use_effective_multiplier() {
        let mut tok = token();
        tok.accounting_mut().set_balance(ALICE, U256::from(100u64)).unwrap();
        tok.accounting_mut().set_total_supply(U256::from(100u64)).unwrap();
        let target = wad() * U256::from(2u64);
        let effective_at = U256::from(100u64);
        set_now(&mut tok, U256::from(1u64));
        LOGIC.update_ui_multiplier(&mut tok, ALICE, target, effective_at, true).unwrap();

        // Before maturity: 1:1.
        set_now(&mut tok, U256::from(50u64));
        assert_eq!(LOGIC.to_scaled_balance(&tok, U256::from(10u64)).unwrap(), U256::from(10u64));
        assert_eq!(LOGIC.scaled_balance_of(&tok, ALICE).unwrap(), U256::from(100u64));
        assert_eq!(LOGIC.balance_of_ui(&tok, ALICE).unwrap(), U256::from(100u64));
        assert_eq!(LOGIC.total_supply_ui(&tok).unwrap(), U256::from(100u64));

        // After maturity: doubled.
        set_now(&mut tok, U256::from(100u64));
        assert_eq!(LOGIC.to_scaled_balance(&tok, U256::from(10u64)).unwrap(), U256::from(20u64));
        assert_eq!(LOGIC.to_raw_balance(&tok, U256::from(20u64)).unwrap(), U256::from(10u64));
        assert_eq!(LOGIC.scaled_balance_of(&tok, ALICE).unwrap(), U256::from(200u64));
        assert_eq!(LOGIC.balance_of_ui(&tok, ALICE).unwrap(), U256::from(200u64));
        assert_eq!(LOGIC.total_supply_ui(&tok).unwrap(), U256::from(200u64));
    }
```

**File:** crates/common/precompiles/src/b20_asset/dispatch.rs (L403-417)
```rust
            // --- Multiplier mutations ---
            SC::updateMultiplier(c) => {
                logic.update_multiplier(self, caller, c.newMultiplier, privileged)?;
                Bytes::new()
            }
            SC::updateUIMultiplier(c) => {
                logic.update_ui_multiplier(
                    self,
                    caller,
                    c.newMultiplier,
                    c.effectiveAt,
                    privileged,
                )?;
                Bytes::new()
            }
```

**File:** crates/common/precompiles/src/b20_asset/abi/v2.rs (L159-171)
```rust
        /// [V2] Schedules a single UI-multiplier update effective at `effectiveAt` — the canonical
        /// corporate-action path (splits, reinvested dividends). Requires `OPERATOR_ROLE`.
        function updateUIMultiplier(uint256 newMultiplier, uint256 effectiveAt) external;

        /// [V2] Cancels the single live pending update, restoring the no-pending state.
        function cancelUIMultiplierUpdate() external;

        /// Instant failsafe: sets the current multiplier immediately and clears any pending.
        /// At `AssetV1` emits only `MultiplierUpdated`; `AssetV2` emits both `MultiplierUpdated` and
        /// `UIMultiplierUpdated`. Deprecated (retained dialable, and kept in base-std's `IB20Asset`
        /// interface as a deprecated function); the canonical setter is the scheduled
        /// `updateUIMultiplier`.
        function updateMultiplier(uint256 newMultiplier) external;
```

**File:** crates/common/precompiles/tests/b20_asset_v2_golden.rs (L2028-2047)
```rust
#[test]
fn golden_read_multiplier_and_scaled_balances() {
    let mut s = fresh();
    seed(&mut s, |t| {
        fund(t, ALICE, u(100));
        t.set_multiplier(B20AssetStorage::WAD * u(2)).unwrap();
    });
    let m =
        op(&mut s, ALICE, FakePolicyAccounting::new(), IB20Asset::multiplierCall {}.abi_encode())
            .unwrap();
    assert_eq!(m, Bytes::from((B20AssetStorage::WAD * u(2)).abi_encode()));

    let scaled = op(
        &mut s,
        ALICE,
        FakePolicyAccounting::new(),
        IB20Asset::toScaledBalanceCall { rawBalance: u(100) }.abi_encode(),
    )
    .unwrap();
    assert_eq!(scaled, Bytes::from(u(200).abi_encode()));
```
