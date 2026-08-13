Confirmed: `LendingAccountCloseBalance` has no `is_protocol_paused()` constraint on its `group` account, unlike every other user-facing lending instruction (deposit, withdraw, borrow, repay, transfer, order placement, liquidate) which all explicitly gate on `!group.load()?.is_protocol_paused()`.### Title
Missing protocol-pause check on `LendingAccountCloseBalance` allows position-state mutation while the protocol is panic-paused - ([File: programs/marginfi/src/instructions/marginfi_account/close_balance.rs])

### Summary
Every other user-facing lending instruction in marginfi-v2 (deposit, withdraw, borrow, repay, order placement, order execution, liquidation, account transfer, bank-fee collection) explicitly gates its `group` account with `constraint = !group.load()?.is_protocol_paused() @ MarginfiError::ProtocolPaused`. `LendingAccountCloseBalance` is the one exception: its `Accounts` struct loads `group` with no pause constraint at all, so the instruction remains fully callable while `global_fee_admin`/`pause_delegate_admin` has panic-paused the protocol.

### Finding Description
The `LendingAccountCloseBalance` accounts struct omits the pause guard present everywhere else: [1](#0-0) 

Compare this to `LendingAccountDeposit`, `LendingAccountWithdraw`, `LendingAccountBorrow`, `LendingAccountRepay`, `PlaceOrder`, `StartExecuteOrder`, `TransferToNewAccount`, and `LendingAccountLiquidate`, which all carry the constraint: [2](#0-1) [3](#0-2) 

This is the exact structural analog of the Lens `FollowNFT` bug: the "sanctioned" entry point (`unfollow`/`lending_account_deposit`/`withdraw`/`borrow`/`repay`) enforces the pause, but a sibling state-mutating function on the same object (`removeFollower`/`burn` vs. `lending_account_close_balance`) forgets the same guard, letting a caller bypass the intended protocol-wide freeze via a different instruction.

`lending_account_close_balance` does mutate bank/account state even though it requires the balance's asset and liability amounts to be zero: it decrements `bank.lending_position_count` / `bank.borrowing_position_count`, zeroes out dust via `bank.change_asset_shares`/`change_liability_shares`, updates `collected_insurance_fees_outstanding`, calls `bank.accrue_interest`, and can clear the bank's liquidation-price-cache lock when invoked in a receivership context: [4](#0-3) [5](#0-4) 

It is also externally CPI-callable (confirmed by the `kamino-mocks` CPI test harness that invokes `lending_account_close_balance` from an arbitrary external program), meaning any third-party integrator program can trigger this bank-state mutation on behalf of a user during a declared pause: [6](#0-5) 

### Impact Explanation
The permissions/pause documentation states unambiguously that pause is meant to halt "the function of the protocol" for *all* balance-affecting instructions, and lists deposit/borrow/withdraw/repay/order/transfer/liquidation/bankruptcy as the blocked set, with only a narrow, explicitly-enumerated set of admin exceptions (forced deleverage, admin bankruptcy, unpause): [7](#0-6) 

`close_balance` is not in that exception list, yet it remains executable. While the direct dollar value moved by a single `close_balance` call is bounded by dust (sub-`ZERO_AMOUNT_THRESHOLD` residuals), the instruction still: (a) mutates `lending_position_count`/`borrowing_position_count` bank-wide counters that other risk logic and off-chain indexers rely on, (b) frees an "occupied" balance slot on the account, letting a user re-open a new balance in a different bank immediately once unpaused (defeating the intent of freezing account state during the incident window admin is investigating), and (c) can clear the bank's `liquidation_price_cache_locked` flag when combined with a receivership context, altering liquidation-cache invariants that other paused-state guarantees depend on. This matches the judged severity class of the original finding (Medium: "function of the protocol... impacted" due to an action being available when it explicitly should not be) rather than a direct fund-theft bug — the underlying root cause (a missing pause constraint on one lending instruction among many identically-shaped ones) is proven with exact code citations above.

### Likelihood Explanation
This is a low-complexity, permissionless, always-reachable path: any account authority (or any CPI caller, per the kamino-mocks harness) that already has a zero-balance/zero-liability position can call `lending_account_close_balance` at any time, including while the protocol is in a declared `panic_pause`. No special preconditions (unhealthy account, admin key, receivership) are required beyond the balance being empty, which is a normal, common account state. Given the codebase's otherwise exhaustive test coverage of pause behavior for every other lending instruction (`actions_during_pause.rs`, `panic_mode_user_interactions.rs`), the complete absence of an equivalent `close_balance` pause test strongly suggests this is an unintentional gap rather than a deliberately permitted exception.

### Recommendation
Add the same pause constraint used by every sibling lending instruction to `LendingAccountCloseBalance`:
```rust
#[derive(Accounts)]
pub struct LendingAccountCloseBalance<'info> {
    #[account(
        constraint = (!group.load()?.is_protocol_paused()) @ MarginfiError::ProtocolPaused
    )]
    pub group: AccountLoader<'info, MarginfiGroup>,
    ...
}
```
Add a regression test analogous to `normal_withdraw_still_blocked_during_pause` / `normal_repay_still_blocked_during_pause` in `programs/marginfi/tests/admin_actions/actions_during_pause.rs` that asserts `lending_account_close_balance` returns `MarginfiError::ProtocolPaused` while `panic_state_cache.is_paused_flag()` is true, including the CPI path exercised by `kamino-mocks`.

### Proof of Concept
1. Create a `MarginfiAccount`, deposit into a bank, then fully repay/withdraw so the balance's asset and liability shares are both below `ZERO_AMOUNT_THRESHOLD` (a normal, frequent end state, e.g. right after `repay_all`).
2. As `global_fee_admin`/`pause_delegate_admin`, call `panic_pause` and propagate the fee-state cache so `marginfi_group.panic_state_cache.is_paused_flag()` returns true (mirrors setup in `programs/marginfi/tests/admin_actions/actions_during_pause.rs:69-75`).
3. Call `LendingAccountCloseBalance` (via `marginfi::instruction::LendingAccountCloseBalance`, using the same account layout as `try_balance_close` in `test-utils/src/marginfi_account.rs:719-746`) against the emptied balance.
4. Observe the transaction succeeds (no `ProtocolPaused` error), unlike every other lending instruction tested against the same paused state in `normal_withdraw_still_blocked_during_pause`/`normal_repay_still_blocked_during_pause`, and the bank's `lending_position_count`/`borrowing_position_count` are mutated despite the active pause.

### Citations

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

**File:** programs/marginfi/src/instructions/marginfi_account/close_balance.rs (L59-87)
```rust
#[derive(Accounts)]
pub struct LendingAccountCloseBalance<'info> {
    pub group: AccountLoader<'info, MarginfiGroup>,

    #[account(
        mut,
        has_one = group @ MarginfiError::InvalidGroup,
        constraint = {
            let a = marginfi_account.load()?;
            account_not_frozen_for_authority(&a, authority.key())
        } @ MarginfiError::AccountFrozen,
        constraint = {
            let a = marginfi_account.load()?;
            let g = group.load()?;
            is_signer_authorized(&a, g.admin, authority.key(), false, false)
        } @ MarginfiError::Unauthorized
    )]
    pub marginfi_account: AccountLoader<'info, MarginfiAccount>,

    pub authority: Signer<'info>,

    #[account(
        mut,
        has_one = group @ MarginfiError::InvalidGroup,
        constraint = is_marginfi_asset_tag(bank.load()?.config.asset_tag)
            @ MarginfiError::WrongAssetTagForStandardInstructions
    )]
    pub bank: AccountLoader<'info, Bank>,
}
```

**File:** programs/marginfi/src/instructions/marginfi_account/repay.rs (L172-180)
```rust
#[derive(Accounts)]
pub struct LendingAccountRepay<'info> {
    #[account(
        constraint = (
            !group.load()?.is_protocol_paused()
            || marginfi_account.load()?.get_flag(ACCOUNT_IN_DELEVERAGE)
        ) @ MarginfiError::ProtocolPaused
    )]
    pub group: AccountLoader<'info, MarginfiGroup>,
```

**File:** programs/marginfi/src/instructions/marginfi_account/order.rs (L511-517)
```rust
#[derive(Accounts)]
#[instruction(bank_keys: Vec<Pubkey>)]
pub struct PlaceOrder<'info> {
    #[account(
        constraint = (!group.load()?.is_protocol_paused()) @ MarginfiError::ProtocolPaused
    )]
    pub group: AccountLoader<'info, MarginfiGroup>,
```

**File:** programs/marginfi/src/state/marginfi_account.rs (L1737-1799)
```rust
    /// When `in_receivership` is true, clears the bank's liquidation price cache lock
    /// so that banks whose balances are closed mid-liquidation don't stay permanently locked.
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

        if in_receivership {
            bank.cache.clear_liquidation_price_cache_locked();
        }

        // Asset-side dust = real tokens still in the liquidity vault that the
        // user never withdrew. Route to `collected_insurance_fees_outstanding`
        // so vault content stays fully accounted for, mirroring the fractional-
        // remainder handling in `withdraw_all`.
        if current_asset_amount > I80F48::ZERO {
            bank.collected_insurance_fees_outstanding =
                I80F48::from(bank.collected_insurance_fees_outstanding)
                    .checked_add(current_asset_amount)
                    .ok_or_else(math_error!())?
                    .into();
        }

        bank.change_asset_shares(-asset_shares, false)?;
        // Liability-side dust = bad debt the borrower never repaid. Decrementing
        // here makes the loss explicit instead of leaving phantom shares in
        // `total_liability_shares` that would compound interest indefinitely.
        bank.change_liability_shares(-liability_shares, true)?;

        if had_assets {
            bank.decrement_lending_position_count();
        }
        if had_liabs {
            bank.decrement_borrowing_position_count();
        }

        Ok(())
    }
```

**File:** programs/kamino-mocks/src/lib.rs (L35-78)
```rust
/// Custom mock-kamino instruction payload that triggers a CPI into
/// marginfi::lending_account_close_balance.
pub const CPI_CLOSE_BALANCE_IX_DATA: [u8; 8] = *b"CPICLSBL";

fn lending_account_close_balance_discriminator() -> [u8; 8] {
    let mut sighash = [0u8; 8];
    sighash
        .copy_from_slice(&hash("global:lending_account_close_balance".as_bytes()).to_bytes()[..8]);
    sighash
}

fn process_cpi_close_balance(accounts: &[AccountInfo]) -> ProgramResult {
    if accounts.len() < 5 {
        return Err(ProgramError::NotEnoughAccountKeys);
    }

    let group_ai = &accounts[0];
    let marginfi_account_ai = &accounts[1];
    let authority_ai = &accounts[2];
    let bank_ai = &accounts[3];
    let marginfi_program_ai = &accounts[4];

    let ix = Instruction {
        program_id: *marginfi_program_ai.key,
        accounts: vec![
            AccountMeta::new_readonly(*group_ai.key, false),
            AccountMeta::new(*marginfi_account_ai.key, false),
            AccountMeta::new_readonly(*authority_ai.key, true),
            AccountMeta::new(*bank_ai.key, false),
        ],
        data: lending_account_close_balance_discriminator().to_vec(),
    };

    invoke(
        &ix,
        &[
            group_ai.clone(),
            marginfi_account_ai.clone(),
            authority_ai.clone(),
            bank_ai.clone(),
            marginfi_program_ai.clone(),
        ],
    )
}
```

**File:** guides/ADMIN/PERMISSIONS_AND_ROLES.md (L147-176)
```markdown
### Blocked while paused

All normal user flows are disabled:

- Deposit, Borrow, Withdraw, Repay (both native banks and integration banks — Kamino, Drift,
  Juplend, Solend)
- Order placement / order flows
- Account transfer
- Classic liquidation (`LendingAccountLiquidate`)
- Permissionless bank-fee collection
- Permissionless bad-debt settlement (`HandleBankruptcy` when called by a non-admin, even on banks
  with the `PERMISSIONLESS_BAD_DEBT_SETTLEMENT` flag)
- Admin bank configuration changes that route through `LendingPoolConfigureBank`

### Permitted while paused (admin exceptions)

A narrow set of actions remain available so the admin/risk_admin can actually resolve the
incident the pause was called for:

- **Forced deleverage** — `risk_admin` can run the full deleverage flow (`StartLiquidation` in
  deleverage mode, plus the withdraw/repay instructions that execute while
  `ACCOUNT_IN_DELEVERAGE` is set). The pause checks on withdraw/repay (including integration
  withdrawals on Kamino, Drift, Juplend, Solend) are bypassed when the account carries this flag,
  so a deleverage can be completed end-to-end.
- **Handle bankruptcy by admin** — `admin` or `risk_admin` can call `HandleBankruptcy` while
  paused. This is needed because a forced deleverage often terminates in a bankruptcy, and
  blocking bankruptcy would leave the bank in a half-resolved state. Non-admin callers (even on
  banks with `PERMISSIONLESS_BAD_DEBT_SETTLEMENT`) remain blocked until the pause expires.
- **Unpause** — `global_fee_admin` can always end the pause early via `panic_unpause`, and anyone
  can permissionlessly clear an expired pause via `panic_unpause_permissionless`.
```
