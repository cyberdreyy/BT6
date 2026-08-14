Confirmed: `lending_pool_withdraw_fees`, `lending_pool_withdraw_insurance`, `lending_pool_withdraw_fees_permissionless`, `lending_pool_update_fees_destination_account`, `edit_fee_state`, and `config_group_fee` all mutate sensitive protocol/bank state and move real funds out of program-controlled vaults, but none of them call `emit!` — unlike sibling functions (`lending_pool_collect_bank_fees`, `configure`, `configure_bank`) which do emit events. This is a direct analog to the reported bug class (critical state/fund-moving functions silently skipping event emission).

### Title
Admin fee-withdrawal and fee-state-mutation instructions omit event emissions, breaking off-chain monitoring of fund movements and privilege changes - (File: `programs/marginfi/src/instructions/marginfi_group/collect_bank_fees.rs`, `programs/marginfi/src/instructions/marginfi_group/edit_global_fee.rs`, `programs/marginfi/src/instructions/marginfi_group/config_group_fee.rs`)

### Summary
Several sensitive marginfi instructions that move real token funds out of protocol vaults or change protocol-wide fee/admin configuration do not emit any Anchor event, unlike their sibling instructions which consistently do. This breaks the pattern the rest of the codebase relies on for indexers, keepers, and monitoring tooling to detect fund outflows and privileged configuration changes.

### Finding Description
`lending_pool_withdraw_fees` moves `amount` tokens from `fee_vault` to an admin-chosen `dst_token_account` but has no `emit!` call: [1](#0-0) 

Similarly, `lending_pool_withdraw_insurance` drains the `insurance_vault` with no event: [2](#0-1) 

And the permissionless variant `lending_pool_withdraw_fees_permissionless`, which anyone can call to send funds to `bank.fees_destination_account`, also emits nothing: [3](#0-2) 

`lending_pool_update_fees_destination_account`, which lets the admin silently redirect where all future permissionless fee withdrawals go, only logs via `msg!` and does not emit a structured event: [4](#0-3) 

At the protocol level, `edit_fee_state` (the `global_fee_admin`-only handler behind `edit_global_fee_state`) can change the `global_fee_admin`, `global_fee_wallet`, and every fee-rate parameter, but the entire function only uses `msg!` logging and never calls `emit!`: [5](#0-4) 

`config_group_fee`, which the `global_fee_admin` can call to toggle `PROGRAM_FEES_ENABLED` for any group without that group's admin consent, likewise has no `emit!`: [6](#0-5) 

By contrast, the permissionless `lending_pool_collect_bank_fees` (which only moves fees between internal protocol vaults, not to external destinations) does emit `LendingPoolBankCollectFeesEvent`: [7](#0-6) 

and `lending_pool_configure_bank` / `marginfi_group_configure` both emit events for their respective changes: [8](#0-7) [9](#0-8) 

This confirms an inconsistent pattern: the instructions that actually move money to external, admin-controlled destinations (fees, insurance, permissionless fee sweep) — arguably the most important ones to audit — are exactly the ones missing events.

### Impact Explanation
No funds can be stolen purely by this omission (all these functions retain their existing authority checks, e.g. `has_one = admin @ MarginfiError::Unauthorized` on `LendingPoolWithdrawFees`/`LendingPoolWithdrawInsurance`, and `has_one = global_fee_admin` on `EditFeeState`). The impact is an indirect but real integrity/operations issue: off-chain indexers, monitoring dashboards, and automated alerting that rely on emitted events (the pattern `parseMarginfiEvents` used throughout the test suite) cannot detect or alert on (a) admin fee/insurance withdrawals, (b) redirection of the permissionless fee destination account, or (c) changes to the global fee admin, global fee wallet, or fee-rate parameters — all changes with direct financial or governance consequences. This weakens auditability/detection of a compromised or malicious admin key and delays incident response, which is the same bug class described in the referenced report.

### Likelihood Explanation
These are normal, expected admin-operational code paths (fee collection is documented as a routine flow in `guides/ADMIN/COLLECTING_FEES.md`), so the missing events are always reachable whenever an admin performs these standard operations — likelihood of the gap manifesting is effectively certain during ordinary protocol operation, though its severity depends on operators' reliance on on-chain events versus other monitoring.

### Recommendation
Add `emit!` calls analogous to `LendingPoolBankCollectFeesEvent` for `lending_pool_withdraw_fees`, `lending_pool_withdraw_insurance`, and `lending_pool_withdraw_fees_permissionless` (including amount, mint, bank, and destination), for `lending_pool_update_fees_destination_account` (old/new destination), and add a dedicated event in `edit_fee_state` and `config_group_fee` capturing which fields changed and their new values, mirroring `MarginfiGroupConfigureEvent`/`LendingPoolBankConfigureEvent`.

### Proof of Concept
1. As `global_fee_admin`, call `edit_global_fee_state` to change `global_fee_wallet` to an attacker-controlled or unexpected address.
2. As group `admin`, call `lending_pool_update_fees_destination_account` to redirect `fees_destination_account`, then call `lending_pool_withdraw_fees_permissionless`.
3. Inspect on-chain transaction logs / `parseMarginfiEvents` output (as used in `tests/utils/group-instructions.ts` and the spec files): no `emit!`-based event is produced for either step, so an off-chain monitor watching only program events (rather than raw instruction data/account diffs) will miss both the fee-wallet takeover and the fund redirection until it observes downstream balance changes.

### Citations

**File:** programs/marginfi/src/instructions/marginfi_group/collect_bank_fees.rs (L165-176)
```rust
    emit!(LendingPoolBankCollectFeesEvent {
        header: GroupEventHeader {
            marginfi_group: ctx.accounts.group.key(),
            signer: None
        },
        bank: ctx.accounts.bank.key(),
        mint: liquidity_vault.mint,
        insurance_fees_collected: insurance_fee_transfer_amount.to_num::<f64>(),
        insurance_fees_outstanding: new_outstanding_insurance_fees.to_num::<f64>(),
        group_fees_collected: group_fee_transfer_amount.to_num::<f64>(),
        group_fees_outstanding: new_outstanding_group_fees.to_num::<f64>(),
    });
```

**File:** programs/marginfi/src/instructions/marginfi_group/collect_bank_fees.rs (L252-285)
```rust
pub fn lending_pool_withdraw_fees<'info>(
    mut ctx: Context<'info, LendingPoolWithdrawFees<'info>>,
    amount: u64,
) -> MarginfiResult {
    let LendingPoolWithdrawFees {
        bank: bank_loader,
        fee_vault,
        fee_vault_authority,
        dst_token_account,
        token_program,
        ..
    } = ctx.accounts;

    let bank = bank_loader.load()?;
    let maybe_bank_mint =
        utils::maybe_take_bank_mint(&mut ctx.remaining_accounts, &bank, token_program.key)?;

    bank.withdraw_spl_transfer(
        amount,
        fee_vault.to_account_info(),
        dst_token_account.to_account_info(),
        fee_vault_authority.to_account_info(),
        maybe_bank_mint.as_ref(),
        token_program.to_account_info(),
        bank_signer!(
            BankVaultType::Fee,
            bank_loader.key(),
            bank.fee_vault_authority_bump
        ),
        ctx.remaining_accounts,
    )?;

    Ok(())
}
```

**File:** programs/marginfi/src/instructions/marginfi_group/collect_bank_fees.rs (L331-364)
```rust
pub fn lending_pool_withdraw_insurance<'info>(
    mut ctx: Context<'info, LendingPoolWithdrawInsurance<'info>>,
    amount: u64,
) -> MarginfiResult {
    let LendingPoolWithdrawInsurance {
        bank: bank_loader,
        insurance_vault,
        insurance_vault_authority,
        dst_token_account,
        token_program,
        ..
    } = ctx.accounts;

    let bank = bank_loader.load()?;
    let maybe_bank_mint =
        utils::maybe_take_bank_mint(&mut ctx.remaining_accounts, &bank, token_program.key)?;

    bank.withdraw_spl_transfer(
        amount,
        insurance_vault.to_account_info(),
        dst_token_account.to_account_info(),
        insurance_vault_authority.to_account_info(),
        maybe_bank_mint.as_ref(),
        token_program.to_account_info(),
        bank_signer!(
            BankVaultType::Insurance,
            bank_loader.key(),
            bank.insurance_vault_authority_bump
        ),
        ctx.remaining_accounts,
    )?;

    Ok(())
}
```

**File:** programs/marginfi/src/instructions/marginfi_group/collect_bank_fees.rs (L411-423)
```rust
/// Fees will be withdrawn to fees_destination_account
pub fn lending_pool_update_fees_destination_account<'info>(
    ctx: Context<'info, LendingPoolUpdateFeesDestinationAccount<'info>>,
) -> MarginfiResult {
    let mut bank = ctx.accounts.bank.load_mut()?;

    let old_dst = bank.fees_destination_account;
    let new_dst = ctx.accounts.destination_account.key();
    bank.fees_destination_account = new_dst;
    msg!("fees_destination_account: {:?} was: {:?}", new_dst, old_dst);

    Ok(())
}
```

**File:** programs/marginfi/src/instructions/marginfi_group/collect_bank_fees.rs (L451-489)
```rust
pub fn lending_pool_withdraw_fees_permissionless<'info>(
    mut ctx: Context<'info, LendingPoolWithdrawFeesPermissionless<'info>>,
    amount: u64,
) -> MarginfiResult {
    let LendingPoolWithdrawFeesPermissionless {
        bank: bank_loader,
        fee_vault,
        fee_vault_authority,
        fees_destination_account,
        token_program,
        ..
    } = ctx.accounts;

    let bank = bank_loader.load()?;

    // Withdraw all if there aren't enough funds to facilitate the withdraw as requested.
    let amount = u64::min(amount, fee_vault.amount);
    let fees_token_program = &token_program.key();

    let maybe_bank_mint =
        utils::maybe_take_bank_mint(&mut ctx.remaining_accounts, &bank, fees_token_program)?;

    bank.withdraw_spl_transfer(
        amount,
        fee_vault.to_account_info(),
        fees_destination_account.to_account_info(),
        fee_vault_authority.to_account_info(),
        maybe_bank_mint.as_ref(),
        token_program.to_account_info(),
        bank_signer!(
            BankVaultType::Fee,
            bank_loader.key(),
            bank.fee_vault_authority_bump
        ),
        ctx.remaining_accounts,
    )?;

    Ok(())
}
```

**File:** programs/marginfi/src/instructions/marginfi_group/edit_global_fee.rs (L10-106)
```rust
pub fn edit_fee_state(
    ctx: Context<EditFeeState>,
    admin: Option<Pubkey>,
    fee_wallet: Option<Pubkey>,
    bank_init_flat_sol_fee: Option<u32>,
    liquidation_flat_sol_fee: Option<u32>,
    order_init_flat_sol_fee: Option<u32>,
    program_fee_fixed: Option<WrappedI80F48>,
    program_fee_rate: Option<WrappedI80F48>,
    liquidation_max_fee: Option<WrappedI80F48>,
    order_execution_max_fee: Option<WrappedI80F48>,
    pause_delegate_admin: Option<Pubkey>,
) -> Result<()> {
    let mut fee_state = ctx.accounts.fee_state.load_mut()?;
    if let Some(admin) = admin {
        msg!(
            "Updating global_fee_admin: {:?} -> {:?}",
            fee_state.global_fee_admin,
            admin
        );
        fee_state.global_fee_admin = admin;
    }
    if let Some(fee_wallet) = fee_wallet {
        msg!(
            "Updating global_fee_wallet: {:?} -> {:?}",
            fee_state.global_fee_wallet,
            fee_wallet
        );
        fee_state.global_fee_wallet = fee_wallet;
    }
    if let Some(bank_init_flat_sol_fee) = bank_init_flat_sol_fee {
        msg!(
            "Updating bank_init_flat_sol_fee: {:?} -> {:?}",
            fee_state.bank_init_flat_sol_fee,
            bank_init_flat_sol_fee
        );
        fee_state.bank_init_flat_sol_fee = bank_init_flat_sol_fee;
    }
    if let Some(program_fee_fixed) = program_fee_fixed {
        let old_f64: f64 = wrapped_i80f48_to_f64(fee_state.program_fee_fixed);
        let new_f64: f64 = wrapped_i80f48_to_f64(program_fee_fixed);
        msg!("Updating program_fee_fixed: {:?} -> {:?}", old_f64, new_f64);
        fee_state.program_fee_fixed = program_fee_fixed;
    }
    if let Some(program_fee_rate) = program_fee_rate {
        let old_f64: f64 = wrapped_i80f48_to_f64(fee_state.program_fee_rate);
        let new_f64: f64 = wrapped_i80f48_to_f64(program_fee_rate);
        msg!("Updating program_fee_rate: {:?} -> {:?}", old_f64, new_f64);
        fee_state.program_fee_rate = program_fee_rate;
    }
    if let Some(liquidation_max_fee) = liquidation_max_fee {
        let old_f64: f64 = wrapped_i80f48_to_f64(fee_state.liquidation_max_fee);
        let new_f64: f64 = wrapped_i80f48_to_f64(liquidation_max_fee);
        msg!(
            "Updating liquidation_max_fee: {:?} -> {:?}",
            old_f64,
            new_f64
        );
        fee_state.liquidation_max_fee = liquidation_max_fee;
    }
    if let Some(liquidation_flat_sol_fee) = liquidation_flat_sol_fee {
        msg!(
            "Updating liquidation_flat_sol_fee: {:?} -> {:?}",
            fee_state.liquidation_flat_sol_fee,
            liquidation_flat_sol_fee
        );
        fee_state.liquidation_flat_sol_fee = liquidation_flat_sol_fee;
    }
    if let Some(order_execution_max_fee) = order_execution_max_fee {
        let old_f64: f64 = wrapped_i80f48_to_f64(fee_state.order_execution_max_fee);
        let new_f64: f64 = wrapped_i80f48_to_f64(order_execution_max_fee);
        msg!(
            "Updating order_execution_max_fee: {:?} -> {:?}",
            old_f64,
            new_f64
        );
        fee_state.order_execution_max_fee = order_execution_max_fee;
    }
    if let Some(order_init_flat_sol_fee) = order_init_flat_sol_fee {
        msg!(
            "Updating order_init_flat_sol_fee: {:?} -> {:?}",
            fee_state.order_init_flat_sol_fee,
            order_init_flat_sol_fee
        );
        fee_state.order_init_flat_sol_fee = order_init_flat_sol_fee;
    }
    if let Some(pause_delegate_admin) = pause_delegate_admin {
        msg!(
            "Updating pause_delegate_admin: {:?} -> {:?}",
            fee_state.pause_delegate_admin,
            pause_delegate_admin
        );
        fee_state.pause_delegate_admin = pause_delegate_admin;
    }

    Ok(())
}
```

**File:** programs/marginfi/src/instructions/marginfi_group/config_group_fee.rs (L25-41)
```rust
pub fn config_group_fee(ctx: Context<ConfigGroupFee>, enable_program_fee: bool) -> MarginfiResult {
    let mut marginfi_group = ctx.accounts.marginfi_group.load_mut()?;
    let flag_before = marginfi_group.group_flags;

    marginfi_group.set_program_fee_enabled(enable_program_fee);

    msg!(
        "flag set to: {:?} was {:?}",
        marginfi_group.group_flags,
        flag_before
    );

    let clock = Clock::get()?;
    marginfi_group.fee_state_cache.last_update = clock.unix_timestamp;

    Ok(())
}
```

**File:** programs/marginfi/src/instructions/marginfi_group/configure_bank.rs (L54-62)
```rust
        emit!(LendingPoolBankConfigureEvent {
            header: GroupEventHeader {
                marginfi_group: ctx.accounts.group.key(),
                signer: Some(*ctx.accounts.admin.key)
            },
            bank: ctx.accounts.bank.key(),
            mint: bank.mint,
            config: bank_config,
        });
```

**File:** programs/marginfi/src/instructions/marginfi_group/configure.rs (L103-110)
```rust
    emit!(MarginfiGroupConfigureEvent {
        header: GroupEventHeader {
            marginfi_group: ctx.accounts.marginfi_group.key(),
            signer: Some(*ctx.accounts.admin.key)
        },
        admin: new_admin,
        flags: marginfi_group.group_flags
    });
```
