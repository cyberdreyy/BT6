## Title
`withdraw_all` / `lending_account_close_balance` erase `emissions_outstanding` without settling them - ([File: programs/marginfi/src/state/marginfi_account.rs])

### Summary
`Balance.emissions_outstanding` tracks unclaimed emissions rewards for a lending/borrowing position [1](#0-0) . Both `withdraw_all` and `close_balance` (invoked by `lending_account_withdraw` with `withdraw_all=true` and by `lending_account_close_balance`) call `balance.close()`, which resets the whole `Balance` struct — including `emissions_outstanding` — back to zero via `empty_deactivated()`, without first paying out or otherwise preserving the accrued emissions. This mirrors the C4 finding in Biconomy's `LiquidityFarming.withdraw()`, where `delete nftInfo[_nftId]` wiped `unpaidRewards` that had already accrued to the user.

### Finding Description
`Balance::empty_deactivated()` zeroes `emissions_outstanding` along with every other field: [2](#0-1) 

`withdraw_all()` calls `balance.close()` (which delegates to this zeroing logic) purely based on asset/liability share checks — it never inspects or settles `emissions_outstanding`: [3](#0-2) 

`close_balance()` (used by the permissionless-authority `lending_account_close_balance` instruction) has the identical pattern — checks only asset/liability amounts, then calls `balance.close()`: [4](#0-3) 

Notably, the error enum already anticipates this exact hazard with `CannotCloseOutstandingEmissions` ("Cannot close balance because of outstanding emissions"): [5](#0-4) 

However, this error variant is never referenced by any `check!`/guard in the instruction or state logic (`grep` across `programs/marginfi/src` finds it only in the errors enum definitions, never invoked) — it appears to be a documented invariant that was never actually wired into `withdraw_all`/`close_balance`. Both entry points (`lending_account_withdraw` with `withdraw_all=true`, and `lending_account_close_balance`) reach `balance.close()` without any prior settlement of `emissions_outstanding`.

### Impact Explanation
Any user with accrued-but-undistributed `emissions_outstanding` on a position who withdraws their full balance (`withdraw_all`) or closes a dust/zero balance via `lending_account_close_balance` will have that reward silently zeroed out with no path to reclaim it — the balance slot is deactivated and its `emissions_outstanding` field is gone. Per the guide, emissions/incentives normally accumulate and later "airdrop" to the account authority [6](#0-5) ; there is no on-chain instruction in scope that reads a closed/inactive `Balance`'s `emissions_outstanding` to still deliver it. This is a durable, financial loss of value the user has already earned — analogous in severity reasoning to the original H-04 finding (assets the user is already entitled to are destroyed by ordinary user-initiated account maintenance, not a hypothetical attack).

### Likelihood Explanation
This requires no adversarial behavior — it is triggered by completely ordinary user actions: withdrawing an entire position (`withdraw_all=true`, the only documented way to close a Balance per the integrator guide [7](#0-6) ) or calling the permissionless `lending_account_close_balance` on a dust position. Any position that has accrued emissions and is then fully withdrawn or dust-closed hits this path.

### Recommendation
Before `balance.close()` zeroes the `Balance`, either:
1. Settle `emissions_outstanding` to the account (e.g., transfer/queue payout, or roll it into a per-account/global outstanding-emissions ledger that survives balance closure), or
2. Enforce the already-defined `CannotCloseOutstandingEmissions` check: require `emissions_outstanding == 0` (within tolerance) before allowing `withdraw_all`/`close_balance` to proceed, forcing users/integrators to claim emissions first.

### Proof of Concept
1. User deposits into a bank with an active emissions campaign; time passes so `emissions_outstanding` accrues on their `Balance` (as tracked in `type-crate/src/types/user_account.rs`).
2. User calls `lending_account_withdraw` with `withdraw_all=true` for that bank.
3. `lending_account_withdraw` → `bank_account.withdraw_all(...)` → `balance.close()` → `Balance` fields reset via `empty_deactivated()`, including `emissions_outstanding` set to 0, with no payout instruction invoked in this call path.
4. The `Balance` slot becomes inactive; the user's previously accrued `emissions_outstanding` value is unrecoverable — there is no subsequent instruction that reads emissions off an inactive/closed balance.

### Citations

**File:** type-crate/src/types/user_account.rs (L299-303)
```rust
    pub liability_shares: WrappedI80F48,
    /// Unclaimed emissions rewards for this position
    pub emissions_outstanding: WrappedI80F48,
    /// Unix timestamp (u64) of the last emissions calculation for this position
    pub last_update: u64,
```

**File:** type-crate/src/types/user_account.rs (L347-360)
```rust
    pub fn empty_deactivated() -> Self {
        Balance {
            active: 0,
            bank_pk: Pubkey::default(),
            bank_asset_tag: ASSET_TAG_DEFAULT,
            tag: 0,
            _pad0: [0; 4],
            asset_shares: WrappedI80F48::from(I80F48::ZERO),
            liability_shares: WrappedI80F48::from(I80F48::ZERO),
            emissions_outstanding: WrappedI80F48::from(I80F48::ZERO),
            last_update: 0,
            _padding: [0; 1],
        }
    }
```

**File:** programs/marginfi/src/state/marginfi_account.rs (L1627-1656)
```rust
    pub fn withdraw_all(&mut self, in_receivership: bool) -> MarginfiResult<(u64, I80F48)> {
        let balance = &mut self.balance;
        let bank = &mut self.bank;

        let total_asset_shares: I80F48 = balance.asset_shares.into();
        let current_asset_amount = bank.get_asset_amount(total_asset_shares)?;
        let current_liability_amount =
            bank.get_liability_amount(balance.liability_shares.into())?;

        debug!("Withdrawing all: {}", current_asset_amount);

        check!(
            current_asset_amount.is_positive_with_tolerance(ZERO_AMOUNT_THRESHOLD),
            MarginfiError::NoAssetFound
        );

        check!(
            current_liability_amount.is_zero_with_tolerance(ZERO_AMOUNT_THRESHOLD),
            MarginfiError::NoAssetFound
        );

        balance.close()?;

        // Only clear the lock when this account is actually in receivership.
        // The lock is bank-level global state, so clearing it unconditionally
        // would affect unrelated accounts sharing the same bank.
        if in_receivership {
            bank.cache.clear_liquidation_price_cache_locked();
        }

```

**File:** programs/marginfi/src/state/marginfi_account.rs (L1739-1767)
```rust
    pub fn close_balance(&mut self, in_receivership: bool) -> MarginfiResult<()> {
        let balance = &mut self.balance;
        let bank = &mut self.bank;

        let current_liability_amount =
            bank.get_liability_amount(balance.liability_shares.into())?;
        let current_asset_amount = bank.get_asset_amount(balance.asset_shares.into())?;

        check!(
            current_liability_amount.is_zero_with_tolerance(ZERO_AMOUNT_THRESHOLD),
            MarginfiError::IllegalBalanceState,
            "Balance has existing debt"
        );

        check!(
            current_asset_amount.is_zero_with_tolerance(ZERO_AMOUNT_THRESHOLD),
            MarginfiError::IllegalBalanceState,
            "Balance has existing assets"
        );

        let asset_shares: I80F48 = balance.asset_shares.into();
        let liability_shares: I80F48 = balance.liability_shares.into();
        // Counters are incremented in `*_balance_internal` when shares cross
        // `ZERO_AMOUNT_THRESHOLD` upward; match that condition so we don't
        // double-decrement positions that already crossed downward earlier.
        let had_assets = asset_shares.is_positive_with_tolerance(ZERO_AMOUNT_THRESHOLD);
        let had_liabs = liability_shares.is_positive_with_tolerance(ZERO_AMOUNT_THRESHOLD);

        balance.close()?;
```

**File:** programs/marginfi/src/errors.rs (L71-72)
```rust
    #[msg("Cannot close balance because of outstanding emissions")] // 6033
    CannotCloseOutstandingEmissions,
```

**File:** guides/USER/EMISSIONS.md (L19-21)
```markdown
Emissions/incentives are delivered by airdrop to the Account's authority, typically on Wednesday, in
no particular order. In the above example, User 1 would get $0.5 + 0.5 * 0.143 * 5 = 1.715$ tokens
and User 2 would get $0.5 + 0.5 + 0.857 * 5 = 5.285$ tokens
```

**File:** guides/DEVELOPERS_INTEGRATORS/GETTING_STARTED_INTEGRATOR.md (L64-76)
```markdown
<details>
<summary> <b>lending_account_withdraw</b> - withdraw from any Bank EXCEPT integrator banks (Kamino, etc)</summary>

- Check `bank.config.asset_tag`, ASSET_TAG_DEFAULT (0) or ASSET_TAG_SOL (1) ASSET_TAG_STAKED (2)
  are allowed with this instruction. Others have their own deposit instruction.
- Requires a Risk Engine check (pass banks and oracles in remaining accounts)
- If group rate limits are enabled, also make sure the withdrawn bank and its oracle account group
  are present in `remaining_accounts`, even if the health check would otherwise not need them.
- `amount` is in native token, in native decimal, e.g. 1 SOL = 1 \* 10^9
- Set `withdraw_all` to "true" to ignore your amount input and withdraw the entire balance. This
is the only way to close a Balance so it no longer appears on your Account, simply withdrawing
by configuring `amount` will always leave the Balance on your account, even with zero shares.
</details>
```
