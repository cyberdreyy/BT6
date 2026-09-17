### Title
Owner-controlled instant multiplier override in `updateMultiplier` can retroactively invalidate a scheduled ERC-8056 rebase that unprivileged holders already relied on - ([File: crates/common/precompiles/src/b20_asset/logic/v2.rs])

### Summary
The `B20Asset` V2 precompile logic exposes two OPERATOR_ROLE-gated multiplier setters: `update_ui_multiplier` (schedules a future rebase at `effective_at`) and `update_multiplier` ("instantaneous failsafe" that writes a new multiplier immediately and silently clears/cancels any live pending schedule). Because `effective_multiplier()` is read lazily at the time any holder-facing call executes (`balanceOfUI`, `totalSupplyUI`, `toUIAmount`/`fromUIAmount`, `scaledBalanceOf`), an unprivileged holder's transaction that is built and broadcast expecting the previously-announced scheduled multiplier can land in a block ordered after an operator's `updateMultiplier` call, receiving a different multiplier than the one the holder observed/relied on when constructing the transaction — mirroring the Loot.sol pattern where an owner-controlled parameter mutation (`updateVestingDuration`) retroactively changes economic terms applied to a user's already-queued transaction.

### Finding Description
`update_ui_multiplier` lets an operator schedule a future multiplier change and publish `effectiveAt`/`newUIMultiplier` on-chain, which downstream integrators and holders use to plan redemptions, transfers-by-UI-amount, or accounting around the announced rebase date. [1](#0-0) 

However, `update_multiplier` is documented and implemented as an "instantaneous failsafe" that, in the same operator-controlled call, immediately overwrites the current multiplier and unconditionally clears any pending scheduled update — even one that is still live and that holders have already priced into pending transactions — emitting a `UIMultiplierUpdateCancelled` only as a side effect, not as a precondition: [2](#0-1) 

All holder-visible economic reads (`balanceOfUI`, `totalSupplyUI`, `toUIAmount`/`fromUIAmount`, `scaledBalanceOf`) resolve the multiplier lazily at execution time via `effective_multiplier`, not at the time the holder observed state off-chain: [3](#0-2) 

This is structurally identical to the Loot.sol root cause: an owner-controlled setter (`updateVestingDuration` / here, `updateMultiplier`) can be included in a later block than a user's already-broadcast transaction, and the value that transaction ultimately observes/acts on (vesting duration vs. multiplier) is computed at execution time rather than pinned to the value the user saw when they signed. The Loot.sol fix proposed — snapshot the parameter into the user-facing struct/computation at the time of creation — is the same missing safeguard here: there is no mechanism to bind a holder-observed multiplier to a specific transaction, and no timelock or minimum-notice guarantee prevents `updateMultiplier` from instantly superseding a `updateUIMultiplier` schedule that holders were already relying on.

### Impact Explanation
An operator (even if "trusted" per the original report's framing) can, via ordinary transaction ordering/timing rather than malice, cause holders' pending transactions that depend on `toUIAmount`/`fromUIAmount`/`balanceOfUI`/`totalSupplyUI` to execute against an unexpected multiplier value, causing unintended value transfer (users moving more or less of the underlying raw balance than intended, or receiving/reporting a different scaled balance than expected). Because `update_multiplier` explicitly bypasses the scheduled, previously-announced `effectiveAt` mechanism instead of requiring cancellation to go through the same notice period, holders lose the guarantee the ERC-8056 scheduled-update surface is designed to provide, and any funds effectively moved/miscalculated during that window cannot be recovered after the fact — a direct parallel to the "funds a user can never recover in full" impact called out in the source report. This qualifies as Medium since it depends on operator-role transaction ordering rather than an anonymous unprivileged exploit path, but is reachable by any unprivileged holder whose in-flight transaction depends on the multiplier value.

### Likelihood Explanation
Likelihood is low-to-medium: it requires the operator to invoke the "instant failsafe" `updateMultiplier` while a scheduled `updateUIMultiplier` is still live, in the same window a holder has an in-flight, multiplier-dependent transaction. This is analogous to the original report's own admission that the scenario is "low-likelihood" but has real, irreversible impact when it occurs, since `update_multiplier` is explicitly retained as a "dialable" deprecated function alongside the canonical scheduled setter, so both code paths remain concurrently reachable. [4](#0-3) 

### Recommendation
Require that `update_multiplier` cannot silently supersede a live pending scheduled update unless it is first explicitly cancelled via `cancel_ui_multiplier_update` (making the cancellation an atomic precondition rather than an implicit side effect), or enforce a minimum notice/timelock before any override takes effect, mirroring the Loot.sol fix of pinning the parameter holders relied on (here, the announced multiplier/effectiveAt) rather than allowing a later privileged transaction to retroactively change it for already-in-flight user transactions.

### Proof of Concept
1. Operator calls `updateUIMultiplier(newMultiplier=2x, effectiveAt=T+1000)`, publishing the scheduled rebase on-chain. [5](#0-4) 
2. A holder observes `newUIMultiplier()`/`effectiveAt()` and constructs/broadcasts a transaction relying on the 2x multiplier (e.g., a `fromUIAmount`-derived transfer sized for the announced post-rebase value).
3. Before the holder's transaction is mined, the operator calls `updateMultiplier(instantValue)` in an earlier-ordered transaction; this immediately overwrites the multiplier and silently clears/cancels the pending 2x schedule. [6](#0-5) 
4. The holder's transaction now executes against `instantValue` instead of the 2x the holder priced in, producing an unexpected and irreversible scaled-balance/transfer outcome — the same "unexpected slashing"-class loss described in the source report, applied to Base's B20 asset precompile's scheduled-multiplier surface.

### Citations

**File:** crates/common/precompiles/src/b20_asset/logic/v2.rs (L719-768)
```rust
    // --- Asset-specific mutations ---

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

**File:** crates/common/precompiles/src/b20_asset/logic/v2.rs (L928-968)
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

    fn ui_multiplier(&self, token: &B20AssetToken<S, A>) -> Result<U256> {
        self.multiplier(token)
    }

    fn new_ui_multiplier(&self, token: &B20AssetToken<S, A>) -> Result<U256> {
        let now = token.accounting().timestamp()?;
        let effective_at = token.accounting().pending_effective_at()?;
        if U256::from(effective_at) > now {
            return Ok(U256::from(token.accounting().pending_multiplier()?));
        }
        self.multiplier(token)
    }

    fn effective_at(&self, token: &B20AssetToken<S, A>) -> Result<U256> {
        Ok(U256::from(token.accounting().pending_effective_at()?))
    }

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
