### Title
B20Asset's instant `updateMultiplier` reward mechanism can be arbitraged via sandwich attacks against uniform balance rescaling - (File: `crates/common/precompiles/src/b20_asset/logic/v1.rs`, `crates/common/precompiles/src/b20_asset/logic/v2.rs`)

### Summary
The `B20Asset` precompile token exposes a multiplier that scales every holder's raw balance into a "UI" (displayed/economic) balance via `toScaledBalance`/`balanceOfUI`/`scaledBalanceOf` [1](#0-0) . The multiplier can be bumped **instantly** and unconditionally for the whole supply through `updateMultiplier`, which the ABI itself documents as an "Instant failsafe: sets the current multiplier immediately" [2](#0-1) , and this instant-update entry point remains dispatched in the V2 (Cobalt) token alongside the scheduled ERC-8056 path [3](#0-2) . Because ordinary `transfer`/`transferFrom` on this precompile is unprivileged, instantaneous, and moves the *entire* economic value represented by the raw balance (no vesting, no time-weighting, no snapshot delay) [4](#0-3) , an unprivileged actor who observes the operator's `updateMultiplier` transaction in the mempool can front-run it with a large `transferFrom`/acquisition of raw balance, let the multiplier bump land, and immediately back-run with a transfer/exit — capturing the value increase attributable to *every unit* of raw balance without having held it through the period the multiplier bump was meant to compensate. This is structurally the same root cause as the `BunniToken` referrer-score bug: a value/reward attribute (there: referrer score; here: multiplier-scaled balance) is (a) tied 1:1 to an instantly-transferable balance and (b) mutated by a single, mempool-visible privileged transaction, letting an attacker sandwich that transaction to extract disproportionate rewards in one block.

### Finding Description
`B20Asset` maintains a `multiplier` in `AssetAccounting` that converts a raw balance into its UI/scaled value: `toScaledBalance = rawBalance * multiplier / WAD` [5](#0-4) . `updateMultiplier` sets this multiplier **immediately** for the whole token (all holders, all at once) and is explicitly documented as an instant failsafe distinct from the time-delayed ERC-8056 `updateUIMultiplier`/`effectiveAt` path [6](#0-5) , and the dispatcher still routes to it in the current (V2) logic [7](#0-6) , confirmed by the `update_multiplier_persists_and_emits` test which shows the multiplier taking effect the moment the call executes [8](#0-7) .

Transfers on this token move the full raw balance instantly between accounts with no lock-up, snapshot, or time-weighting: `move_balance`/`transfer_inner` simply debit `from` and credit `to` in the same slot the multiplier reads from [9](#0-8) . Any unprivileged holder can therefore:
1. Watch the mempool for the operator's `updateMultiplier`/`updateUIMultiplier` transaction (which, like `BunniToken.distributeReferralRewards`, is a privileged, value-generating state transition).
2. Acquire (mint via a paired DEX, buy, or receive via transfer) a large raw balance immediately before it lands.
3. Let the multiplier update execute, uniformly inflating the UI-value of every raw-balance unit, including the attacker's freshly acquired balance.
4. Immediately transfer/redeem/sell the balance in the same block, capturing the multiplier-driven value gain without ever bearing the economic exposure (e.g., holding period, risk) that the multiplier increase was meant to compensate.

This mirrors the `BunniToken` root cause precisely: a per-address value attribute (`fromReferrer`'s score there; raw balance here) is transferred 1:1 and instantly whenever the token moves, and a single privileged transaction retroactively (or immediately) revalues that attribute for whoever holds it at execution time — with no time-based vesting/release to prevent an attacker from acquiring the position purely to be present for that one transaction.

### Impact Explanation
A sandwiching actor extracts value corresponding to the multiplier bump that should accrue only to genuine long-term holders, effectively diluting/stealing yield from legitimate token holders in a single block. Because the multiplier bump applies to the *entire* supply's balances, the magnitude of the attack scales with how large a raw balance the attacker can transiently acquire, which can be substantial given no anti-flash-loan / balance-duration protection exists on the transfer path. This is a concrete transfer of economic value away from honest holders toward an unprivileged attacker, satisfying "unauthorized operation" / theft-of-funds-equivalent impact — assessed as Medium, consistent with the referenced report's severity rating for the same bug class.

### Likelihood Explanation
Likelihood is Medium: it requires (a) an operator/role holder to call the instant `updateMultiplier` (rather than exclusively using the time-delayed `updateUIMultiplier`/`effectiveAt` schedule), and (b) the token to be transferable/tradeable enough that an attacker can acquire a large balance atomically pre-update and dispose of it post-update within the same block (e.g., via a DEX pool or a cooperating counterparty). Both conditions are realistic operational patterns for a live B20Asset deployment, and the mempool visibility of the operator's transaction (like the referenced report's `distributeReferralRewards`) is the same enabling condition used in the original finding.

### Recommendation
- Prefer the scheduled `updateUIMultiplier`/`effectiveAt` path over the instant `updateMultiplier` failsafe for routine multiplier changes, and restrict/remove the instant path to genuine emergency use only.
- When an instant multiplier update is unavoidable, consider snapshotting eligible balances prior to the block in which the update is applied (e.g., require a minimum holding duration or use a balance recorded at a prior block) so a same-block acquire-then-exit cannot capture the multiplier delta.
- Alternatively, rate-limit or timelock all multiplier increases (even the "instant failsafe") behind a minimum public-notice delay, closing the gap the mempool-visible, single-transaction, whole-supply revaluation currently creates.

### Proof of Concept
1. Operator holds `OPERATOR_ROLE` on a deployed `B20Asset` token and submits `updateMultiplier(newMultiplier)` (or `updateUIMultiplier` with a very near-term `effectiveAt`), which will immediately/soon multiply every balance's UI value via `toScaledBalance` [5](#0-4) .
2. Attacker observes this transaction in the mempool and submits a bundle:
   - Front-run: `transferFrom`/acquire a large raw balance into the attacker's address using the permissionless `transfer_inner`/`move_balance` path [10](#0-9) .
   - Include the operator's `updateMultiplier` call, which instantly revalues the attacker's newly acquired raw balance [7](#0-6) .
   - Back-run: transfer the balance back out / redeem it, realizing the multiplier-driven scaled-value gain in the same block.
3. The attacker nets the multiplier-driven appreciation on a balance held for less than one block, at the expense of holders who were exposed to the position over the period the multiplier change was meant to compensate — the same "same-block acquire, claim mutated reward, exit" structure described in the referenced `BunniToken` finding.

### Citations

**File:** crates/common/precompiles/src/b20_asset/logic/v2.rs (L61-123)
```rust
    /// Balance-moving core of `transfer`/`transferFrom`, without the pause check.
    ///
    /// `from` / `to` are [`NonZeroAddress`]: callers validate zero addresses (and choose the
    /// typed revert) before any policy SLOAD. `policies` carries the sender/receiver ids
    /// pre-read from their shared slot by the caller; `Some` enforces both (unprivileged
    /// path), `None` skips them (factory-privileged path).
    fn transfer_inner<S: AssetAccounting, A: PolicyAccounting>(
        &self,
        token: &mut B20AssetToken<S, A>,
        from: NonZeroAddress,
        to: NonZeroAddress,
        amount: U256,
        policies: Option<&TransferPolicyIds>,
    ) -> Result<()> {
        let from = from.get();
        let to = to.get();
        if let Some(policies) = policies {
            B20Guards::ensure_authorized_by_id(
                token,
                B20PolicyType::TransferSender.id(),
                policies.sender,
                from,
            )?;
            B20Guards::ensure_authorized_by_id(
                token,
                B20PolicyType::TransferReceiver.id(),
                policies.receiver,
                to,
            )?;
        }
        self.move_balance(token, from, to, amount)
    }

    /// Debits `from`, credits `to`, and emits `Transfer(from, to, amount)`.
    ///
    /// The shared balance-move primitive: no policy, allowance, pause, or zero-address checks —
    /// callers apply their own guards first. Both [`Self::transfer_inner`] and the seize path use
    /// this, so seize never has to reuse the policy-bearing `transfer_inner` (nor the factory
    /// privileged bypass).
    fn move_balance<S: AssetAccounting, A: PolicyAccounting>(
        &self,
        token: &mut B20AssetToken<S, A>,
        from: Address,
        to: Address,
        amount: U256,
    ) -> Result<()> {
        let from_balance = token.accounting().balance_of(from)?;
        if from_balance < amount {
            return Err(BasePrecompileError::revert(IB20::InsufficientBalance {
                sender: from,
                balance: from_balance,
                needed: amount,
            }));
        }
        let new_from_balance =
            from_balance.checked_sub(amount).ok_or_else(BasePrecompileError::under_overflow)?;
        token.accounting_mut().set_balance(from, new_from_balance)?;
        let to_balance = token.accounting().balance_of(to)?;
        let new_to_balance =
            to_balance.checked_add(amount).ok_or_else(BasePrecompileError::under_overflow)?;
        token.accounting_mut().set_balance(to, new_to_balance)?;
        token.accounting_mut().emit_event(IB20::Transfer { from, to, amount }.encode_log_data())
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

**File:** crates/common/precompiles/src/b20_asset/abi/v1.rs (L88-93)
```rust
        function scaledBalanceOf(address account) external view returns (uint256);

        /// Instant failsafe: sets the current multiplier immediately.
        /// At `AssetV1` emits `MultiplierUpdated`, which was replaced in `AssetV2` by
        /// `UIMultiplierUpdated`.
        function updateMultiplier(uint256 newMultiplier) external;
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

**File:** crates/common/precompiles/src/b20_asset/logic/v1.rs (L1487-1494)
```rust
    #[test]
    fn update_multiplier_persists_and_emits() {
        let mut tok = token();
        let new_multiplier = B20AssetStorage::WAD * U256::from(3u64);
        LOGIC.update_multiplier(&mut tok, ADMIN, new_multiplier, true).unwrap();
        assert_eq!(tok.accounting().multiplier().unwrap(), new_multiplier);
        assert_eq!(last_event_sig(&tok), IB20Asset::MultiplierUpdated::SIGNATURE_HASH);
    }
```
