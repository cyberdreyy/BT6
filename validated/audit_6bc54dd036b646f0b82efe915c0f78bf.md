## Finding

Marginfi's `Balance` struct tracks unclaimed reward-like accruals in `emissions_outstanding` [1](#0-0) , but `Balance::close()` unconditionally overwrites the entire balance slot — including `emissions_outstanding` — with a fresh `empty_deactivated()` value, silently zeroing any unclaimed emissions instead of paying them out or blocking the close.

### Title
Closing/withdrawing a balance discards `emissions_outstanding` without payout - ([File: programs/marginfi/src/state/marginfi_account.rs])

### Summary
`lending_account_close_balance`, `withdraw_all`, and `repay_all` all end by calling `balance.close()`, which resets the `Balance` struct to `Balance::empty_deactivated()`. This wipes `emissions_outstanding` to zero with no check, no claim, and no error, even though a dedicated error variant `MarginfiError::CannotCloseOutstandingEmissions` exists in the codebase suggesting this exact scenario was meant to be guarded against.

### Finding Description
`Balance::close()` is implemented as a full struct overwrite:
```
fn close(&mut self) -> MarginfiResult {
    *self = Self::empty_deactivated();
    Ok(())
}
``` [2](#0-1) 

`empty_deactivated()` sets `emissions_outstanding` back to `I80F48::ZERO` unconditionally: [3](#0-2) 

This `close()` is invoked from three user-callable paths without any prior check or claim of `emissions_outstanding`:
- `close_balance()` (used by the permissionless `lending_account_close_balance` instruction), which only checks that asset/liability amounts are zero, never emissions: [4](#0-3) 
- `withdraw_all()`, called from `lending_account_withdraw` when `withdraw_all=true`: [5](#0-4) 
- `repay_all()`, called from `lending_account_repay` when `repay_all=true`: [6](#0-5) 

Notably, `MarginfiError::CannotCloseOutstandingEmissions` (error code 6033) is defined and mapped in the error-code table, but a repo-wide search shows it is never actually raised (`err!`/`check!`) anywhere in the instruction logic — only referenced in `errors.rs`'s definition/match arm and in a JS test-utility error-lookup table. [7](#0-6)  This strongly suggests the intended guard against closing a balance with outstanding emissions was never wired into `close_balance`, `withdraw_all`, or `repay_all`.

This directly parallels the reported VotingEscrow bug class: destroying/resetting the position that entitles the user to a pending reward, without settling that reward first, permanently forfeits it because there is no longer a live balance to reference for a subsequent claim.

### Impact Explanation
A user who withdraws their full position (`withdraw_all`), repays their full liability (`repay_all`), or closes a zero-balance slot (`lending_account_close_balance`) while `emissions_outstanding` is nonzero will have that accrued emissions amount silently deleted. Since emissions are normally delivered via an off-chain/on-chain airdrop process keyed off account state, and the `Balance` row is reset to `Pubkey::default()`/inactive, there is no on-chain trace left of the forfeited amount for that position. This is a direct, unprivileged, financial loss to depositors/borrowers participating in emissions campaigns.

### Likelihood Explanation
This requires no special conditions beyond normal use: any user who has accrued emissions on a position and then withdraws/repays in full or closes an already-zero balance will trigger the loss. `withdraw_all`/`repay_all` are common, ordinary user flows (e.g., exiting a position entirely), making this readily reachable, not a contrived edge case.

### Recommendation
Before calling `balance.close()` in `close_balance()`, `withdraw_all()`, and `repay_all()`, check that `emissions_outstanding` is zero (or below dust threshold) and return `MarginfiError::CannotCloseOutstandingEmissions` if not — wiring up the already-defined but currently unused error — or alternatively settle/flush `emissions_outstanding` into a claimable/paid-out state before resetting the balance.

### Proof of Concept
Conceptual trace (no code execution performed, based on static review):
1. User deposits into a bank participating in an emissions campaign; over time `balance.emissions_outstanding` accrues to a nonzero value (tracked per `Balance`, see field at [8](#0-7) ).
2. User calls `lending_account_withdraw` with `withdraw_all = true`.
3. `withdraw_all()` checks only asset/liability amounts, then calls `balance.close()`, which resets `emissions_outstanding` to zero: [9](#0-8) 
4. The instruction succeeds; the previously accrued `emissions_outstanding` value is gone from account state, with no error and no compensating credit to the user.

I was unable to find any emissions-settlement/claim call inserted in these code paths, nor any place where `CannotCloseOutstandingEmissions` is actually raised, so I'm confident the guard is missing based on static analysis; I did not run the test suite to confirm at runtime.

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

**File:** programs/marginfi/src/state/marginfi_account.rs (L1485-1489)
```rust
    fn close(&mut self) -> MarginfiResult {
        *self = Self::empty_deactivated();

        Ok(())
    }
```

**File:** programs/marginfi/src/state/marginfi_account.rs (L1627-1648)
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
```

**File:** programs/marginfi/src/state/marginfi_account.rs (L1685-1705)
```rust
    pub fn repay_all(&mut self, in_receivership: bool) -> MarginfiResult<(u64, I80F48)> {
        let balance = &mut self.balance;
        let bank = &mut self.bank;

        let total_liability_shares: I80F48 = balance.liability_shares.into();
        let current_liability_amount = bank.get_liability_amount(total_liability_shares)?;
        let current_asset_amount = bank.get_asset_amount(balance.asset_shares.into())?;

        debug!("Repaying all: {}", current_liability_amount,);

        check!(
            current_liability_amount.is_positive_with_tolerance(ZERO_AMOUNT_THRESHOLD),
            MarginfiError::NoLiabilityFound
        );

        check!(
            current_asset_amount.is_zero_with_tolerance(ZERO_AMOUNT_THRESHOLD),
            MarginfiError::NoLiabilityFound
        );

        balance.close()?;
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
