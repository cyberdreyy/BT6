## Analysis: Pending Emissions Rewards Lost on `LendingAccountCloseBalance`

### Title
Unclaimed Emissions Rewards Are Silently Destroyed When Closing a Balance - (File: `programs/marginfi/src/instructions/marginfi_account/close_balance.rs`)

### Summary
The staking-v1 bug destroyed a user's pending BRO reward because the removal path only checked stake-amount and reward being zero before wiping the storage entry, while unrelated bBRO value was never validated. The analogous marginfi path is `lending_account_close_balance` / `BankAccountWrapper::close_balance`, which checks only `asset_shares` and `liability_shares` are zero before invoking `balance.close()`, but never checks `balance.emissions_outstanding` before that value is zeroed out.

### Finding Description
`BankAccountWrapper::close_balance` validates only two conditions before wiping a `Balance` slot: [1](#0-0) 

It then calls `balance.close()?`, which resets the balance to `Balance::empty_deactivated()` — zeroing `asset_shares`, `liability_shares`, and crucially `emissions_outstanding`: [2](#0-1) 

The `Balance` struct itself documents `emissions_outstanding` as "Unclaimed emissions rewards for this position": [3](#0-2) 

The instruction handler that drives this, `lending_account_close_balance`, likewise performs no check on outstanding emissions before calling `bank_account.close_balance(in_receivership)`: [4](#0-3) 

Notably, the codebase defines a dedicated error variant, `CannotCloseOutstandingEmissions` ("Cannot close balance because of outstanding emissions"), implying this exact guard was intended to exist: [5](#0-4) 

However, I could not locate any call site in `close_balance.rs` or `BankAccountWrapper::close_balance` (or `repay_all`, which has the same pattern at lines 1705) that actually raises `CannotCloseOutstandingEmissions` before zeroing the balance. Both `close_balance` and `repay_all` unconditionally call `balance.close()?`, discarding `emissions_outstanding` regardless of its value, mirroring the staking-v1 pattern where the removal condition checked the wrong/insufficient set of fields and let a nonzero user-owned value get erased alongside.

I was not able to fully verify within the available search iterations whether some other instruction (e.g., an emissions-settlement path) is guaranteed to run immediately before `close_balance` in every client flow, or whether `emissions_outstanding` is otherwise settled/paid at withdraw-all/repay-all time. This is the main open uncertainty in this analysis.

### Impact Explanation
If a user accrues emissions on a balance (deposit/borrow with an emissions-enabled bank) and then withdraws/repays their entire principal followed by `lending_account_close_balance` (or `repay_all` reaching zero liability), the position's `emissions_outstanding` shares are wiped without the user ever collecting them and without any accounting adjustment on the bank side (no analogous "route to outstanding fees" logic exists for emissions the way it does for asset/liability dust in the same function). This is a durable, permanent loss of a token-denominated claim that the user rightfully owned — a direct financial-loss analog to the bBRO token loss in the original report.

### Likelihood Explanation
Any user with active emissions on a bank who fully withdraws/repays and then closes the balance (a normal, expected, permissionless-by-authority action) would trigger this path. This does not require special privileges, a race condition, or unusual protocol state — it is a completely standard end-of-position workflow.

### Recommendation
Before calling `balance.close()` in `BankAccountWrapper::close_balance` (and in `repay_all`), check that `balance.emissions_outstanding` is zero (or below a dust tolerance) and return `MarginfiError::CannotCloseOutstandingEmissions` if not — using the error variant that already exists but appears unused for this guard. Alternatively, settle/transfer outstanding emissions to the user (or to an outstanding-emissions accumulator, similar to `collected_insurance_fees_outstanding`) as part of the close path so the value isn't destroyed.

### Proof of Concept
Conceptual sequence (based on code inspection; not independently executed against a live test harness within this session):
1. User deposits into a bank with emissions enabled; emissions accrue into `balance.emissions_outstanding` over time (accrual mechanism found via `emissions_outstanding` field but its updater function was not located in this session).
2. User withdraws the full principal (`withdraw_all`) or repays the full liability (`repay_all`), driving `asset_shares`/`liability_shares` to zero while `emissions_outstanding` remains nonzero.
3. User (or receivership flow) calls `LendingAccountCloseBalance` / `BankAccountWrapper::close_balance`, which only checks `current_liability_amount` and `current_asset_amount` are zero (`close_balance.rs` and `marginfi_account.rs:1743-1757`), then calls `balance.close()`, resetting `emissions_outstanding` to zero (`user_account.rs:347-360`).
4. The user's unclaimed emissions are permanently gone; no error is raised, no compensating credit is issued.

**Caveat:** I could not confirm within this session whether some other mandatory step (a separate emissions-claim instruction always required before close) actually prevents this in practice — the existence of the `CannotCloseOutstandingEmissions` error suggests the protocol authors intended such a guard, but I found no call site enforcing it in the reviewed close/repay paths. A Devin session with full repo/test access should verify emissions accrual/claim mechanics end-to-end (search for the emissions-update function and any client-side/off-chain safeguards) to confirm exploitability before treating this as fully proven.

### Citations

**File:** programs/marginfi/src/state/marginfi_account.rs (L1743-1757)
```rust
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
```

**File:** type-crate/src/types/user_account.rs (L294-302)
```rust
    /// The user's asset (deposit) shares in the bank. Multiply by `bank.asset_share_value` for
    /// the token amount.
    pub asset_shares: WrappedI80F48,
    /// The user's liability (borrow) shares in the bank. Multiply by `bank.liability_share_value`
    /// for the token amount.
    pub liability_shares: WrappedI80F48,
    /// Unclaimed emissions rewards for this position
    pub emissions_outstanding: WrappedI80F48,
    /// Unix timestamp (u64) of the last emissions calculation for this position
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

**File:** programs/marginfi/src/instructions/marginfi_account/close_balance.rs (L19-57)
```rust
pub fn lending_account_close_balance(ctx: Context<LendingAccountCloseBalance>) -> MarginfiResult {
    let LendingAccountCloseBalance {
        marginfi_account,
        bank: bank_loader,
        group: marginfi_group_loader,
        ..
    } = ctx.accounts;

    let mut marginfi_account = marginfi_account.load_mut()?;
    let mut bank = bank_loader.load_mut()?;

    check!(
        !marginfi_account.get_flag(ACCOUNT_DISABLED),
        MarginfiError::AccountDisabled
    );

    let group = &*marginfi_group_loader.load()?;
    bank.accrue_interest(
        Clock::get()?.unix_timestamp,
        group,
        #[cfg(not(feature = "client"))]
        bank_loader.key(),
    )?;

    bank.update_bank_cache(group)?;

    let in_receivership = marginfi_account.get_flag(ACCOUNT_IN_RECEIVERSHIP);
    let lending_account: &mut marginfi_type_crate::types::LendingAccount =
        &mut marginfi_account.lending_account;
    let mut bank_account =
        BankAccountWrapper::find(&bank_loader.key(), &mut bank, lending_account)?;

    bank_account.close_balance(in_receivership)?;
    lending_account.sort_balances();
    marginfi_account.sync_indexer_flags();
    marginfi_account.last_update = Clock::get()?.unix_timestamp as u64;

    Ok(())
}
```

**File:** programs/marginfi/src/errors.rs (L71-72)
```rust
    #[msg("Cannot close balance because of outstanding emissions")] // 6033
    CannotCloseOutstandingEmissions,
```
