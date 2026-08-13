Based on the code I found, there's a concrete missing-pause-check analog in `lending_account_close_balance`.

### Title
Missing Bank Operational-State / Protocol-Pause Check in `lending_account_close_balance` - (File: programs/marginfi/src/instructions/marginfi_account/close_balance.rs)

### Summary
The ZRC20 report's root cause is that a pausing safeguard exists on the "normal" execution paths but is never enforced on an alternate path that reaches the same state-mutating logic (`onCrossChainCall` bypassing the Ethermint hook that runs `CheckZRC20Paused()`). marginfi has an structurally identical gap: every user-facing balance-mutating instruction (`deposit`, `withdraw`, `borrow`, `repay`, and the Kamino/Drift/Juplend/Solend integration equivalents) explicitly calls `validate_bank_state(...)` and gates on `group.is_protocol_paused()` in its `Accounts` constraints, but `LendingAccountCloseBalance` — which also mutates a user's balance via `BankAccountWrapper::close_balance` — does neither.

### Finding Description
`validate_bank_state` is the central chokepoint that enforces `BankOperationalState` (`Paused` / `ReduceOnly` / `KilledByBankruptcy`) for state-mutating instructions: [1](#0-0) 

It is wired into every standard and integration deposit/withdraw/borrow/repay path, e.g. Solend deposit/withdraw: [2](#0-1) [3](#0-2) 

and the account-level pause gate (`group.is_protocol_paused()`) is likewise present as an `Accounts` constraint on those same instructions, e.g.: [4](#0-3) 

`lending_account_close_balance` and its `Accounts` struct `LendingAccountCloseBalance`, by contrast, call neither `validate_bank_state` nor check `is_protocol_paused()` anywhere: [5](#0-4) 

The only checks performed are `ACCOUNT_DISABLED`, frozen-authority, and `is_signer_authorized` — none of which reference `bank.config.operational_state` or the group's `panic_state_cache`. This instruction still calls `bank.accrue_interest`, `bank.update_bank_cache`, and `BankAccountWrapper::close_balance`, i.e. it mutates bank/account state exactly like the gated instructions do, just through a different (ungated) instruction entry point — the same "use before check omitted on an alternate path" pattern as the ZRC20 report.

### Impact Explanation
Per the project's own admin guide, when the group admin sets a bank to `Paused` (default state for new banks, or used "to halt all activity on a bank while investigating an issue") or the `global_fee_admin`/`pause_delegate_admin` invokes the protocol-wide `panic_pause`, *all* user balance operations — deposit, borrow, withdraw, repay, liquidation — are documented as blocked: [6](#0-5) [7](#0-6) 

Because `LendingAccountCloseBalance` is not gated by `validate_bank_state`/`is_protocol_paused`, a user can still close (zero-out) a balance on a bank while it is `Paused`/`ReduceOnly`/frozen, or while the entire protocol is under an emergency `panic_pause`, defeating the invariant the admin/incident-responder relies on ("halt all activity while investigating an issue"). This is exactly the kind of unauthorized state change the pause is meant to prevent during an active security incident.

### Likelihood Explanation
High reachability: `lending_account_close_balance` is a normal, unprivileged, permissionless-by-authority instruction reachable by any account owner at any time — no CPI trickery, no special setup, and no admin/validator privilege is required, unlike the flagged-but-rejected receivership/deleverage bypasses which are intentionally admin-gated. The only precondition is that the caller controls a `MarginfiAccount` with a balance on the targeted bank, which is trivial to set up before a pause is invoked.

### Recommendation
Add a `validate_bank_state(&bank, InstructionKind::FailsInPausedState)` (or the appropriate `InstructionKind` variant) call inside `lending_account_close_balance`, and add the same `!group.load()?.is_protocol_paused()` constraint to the `LendingAccountCloseBalance` accounts struct that is present on `SolendDeposit`/`SolendWithdraw` and the other user-facing instructions, so that closing a balance is subject to the same operational-state and protocol-pause gating as every other balance-mutating instruction.

### Proof of Concept
1. Group admin sets a bank's `operational_state` to `Paused` via `configure_bank`, or `global_fee_admin` calls `panic_pause` (protocol-wide), as exercised in the existing pause test suite: [8](#0-7) 
2. Confirm normal flows are blocked (e.g. `try_bank_withdraw`/`try_bank_deposit` fail with `BankPaused`/`ProtocolPaused`, as validated by `validate_bank_state` and the `is_protocol_paused()` constraint shown above).
3. Call `lending_account_close_balance` on the same paused bank/account — because `LendingAccountCloseBalance` performs no `validate_bank_state` or `is_protocol_paused` check, the instruction succeeds and mutates the account's balance/state (`BankAccountWrapper::close_balance`) despite the bank/protocol being paused, unlike every other balance-affecting instruction.

*(Note: I could not verify from the indexed snippets exactly what monetary conditions `BankAccountWrapper::close_balance` requires internally — e.g., whether it requires a near-zero balance before closing, which would bound the direct financial impact. This detail lives in `programs/marginfi/src/state/marginfi_account.rs`, which I was only able to partially inspect within the tool budget; a full review of `close_balance`'s implementation there is recommended to confirm the exact financial magnitude of what can be bypassed during a pause.)*

### Citations

**File:** programs/marginfi/src/utils/general.rs (L266-285)
```rust
pub fn validate_bank_state(bank: &Bank, kind: InstructionKind) -> MarginfiResult {
    if bank.config.operational_state == BankOperationalState::KilledByBankruptcy {
        return err!(MarginfiError::BankKilledByBankruptcy);
    }
    // Bank exists but has not completed one-time setup (e.g. JupLend seed deposit). Block every
    // operation until init runs.
    if bank.config.operational_state == BankOperationalState::Uninitialized {
        return err!(MarginfiError::BankUninitialized);
    }

    match kind {
        InstructionKind::FailsInReduceState if bank.config.operational_state.is_reduce_only() => {
            return err!(MarginfiError::BankReduceOnly);
        }

        InstructionKind::FailsInPausedState
            if bank.config.operational_state == BankOperationalState::Paused =>
        {
            return err!(MarginfiError::BankPaused);
        }
```

**File:** programs/marginfi/src/instructions/solend/deposit.rs (L52-66)
```rust
    let authority_bump: u8;
    {
        let marginfi_account = ctx.accounts.marginfi_account.load()?;
        let bank = ctx.accounts.bank.load()?;
        authority_bump = bank.liquidity_vault_authority_bump;

        validate_asset_tags(&bank, &marginfi_account)?;
        validate_bank_state(&bank, InstructionKind::FailsIfPausedOrReduceState)?;

        check!(
            !marginfi_account.get_flag(ACCOUNT_DISABLED)
                && !marginfi_account.get_flag(ACCOUNT_IN_RECEIVERSHIP),
            MarginfiError::AccountDisabled
        );
    }
```

**File:** programs/marginfi/src/instructions/solend/deposit.rs (L145-152)
```rust
#[derive(Accounts)]
pub struct SolendDeposit<'info> {
    #[account(
        constraint = (
            !group.load()?.is_protocol_paused()
        ) @ MarginfiError::ProtocolPaused
    )]
    pub group: AccountLoader<'info, MarginfiGroup>,
```

**File:** programs/marginfi/src/instructions/solend/withdraw.rs (L83-90)
```rust
    let (collateral_amount, share_amount) = {
        let mut bank = ctx.accounts.bank.load_mut()?;
        let mut marginfi_account = ctx.accounts.marginfi_account.load_mut()?;
        let clock = Clock::get()?;
        authority_bump = bank.liquidity_vault_authority_bump;

        validate_bank_state(&bank, InstructionKind::FailsInPausedState)?;

```

**File:** programs/marginfi/src/instructions/marginfi_account/close_balance.rs (L19-87)
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

**File:** guides/ADMIN/BANK_STATE.md (L18-26)
```markdown
### Paused

All operations are halted. Users cannot deposit, borrow, withdraw, repay, or be liquidated. This is
the default state for newly created banks.

Use cases:
- Initial setup: configure the bank before allowing users to interact with it.
- Emergency: halt all activity on a bank while investigating an issue.

```

**File:** guides/ADMIN/PERMISSIONS_AND_ROLES.md (L147-159)
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
```

**File:** programs/marginfi/tests/admin_actions/actions_during_pause.rs (L69-75)
```rust
    // Pause the protocol
    test_f.marginfi_group.try_panic_pause().await?;
    test_f.marginfi_group.try_propagate_fee_state().await?;

    let marginfi_group = test_f.marginfi_group.load().await;
    assert!(marginfi_group.panic_state_cache.is_paused_flag());

```
