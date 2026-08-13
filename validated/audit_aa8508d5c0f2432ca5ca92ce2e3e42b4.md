### Title
Permissionless bad-debt socialization dilutes depositors who join after the loss occurred, with no on-chain signal of pending bank bad debt - (`File: programs/marginfi/src/instructions/marginfi_group/handle_bankruptcy.rs`)

### Summary
The Karak report describes a class of bug where a protocol-level loss event (a slash) is *recorded* at one point in time but only *applied* to the pool's share price at a later point, and depositors who enter the pool during that gap are unfairly diluted, with no way to detect the pending loss beforehand. marginfi has a structurally identical gap around bad-debt socialization: a `MarginfiAccount` can be bankrupt (bad debt outstanding) for an unbounded period before anyone calls `lending_pool_handle_bankruptcy`, and when that call finally executes, `Bank::socialize_loss` reduces `asset_share_value` proportionally against *all* `total_asset_shares` present at execution time — including shares from users who deposited into the bank after the bad debt was created but before the socialization was finalized.

### Finding Description
`lending_pool_handle_bankruptcy` computes `bad_debt` for the target account and, if the insurance fund cannot fully cover it, calls `bank.socialize_loss(socialized_loss)`, which reduces `asset_share_value` for the whole bank: [1](#0-0) 

`socialize_loss` spreads the loss over `total_asset_shares` at the moment the instruction runs, not at the moment the bad debt was created: [2](#0-1) 

When `PERMISSIONLESS_BAD_DEBT_SETTLEMENT_FLAG` is set on a bank, `handle_bankruptcy` is callable by anyone, and the bank must merely be non-paused (`validate_bank_state(&bank, InstructionKind::FailsInPausedState)`): [3](#0-2) 

Nothing in the deposit path checks whether the bank has an outstanding bankrupt account awaiting settlement, and nothing in the `Bank` account exposes a "pending bad debt" indicator. The protocol's own documentation confirms the delay is real and can be arbitrarily long: bankruptcy handling is a separate, later step after liquidation, and as of the time of writing had "never been executed in the main pool," implying bad debt can sit unresolved on a bank for extended periods: [4](#0-3) 

This is the same root cause pattern as the Karak finding: a "slash" (loss event) is determined at time T0 (when a user's liabilities exceed their assets, i.e., after liquidation zeroes their collateral), but is only applied to the shared pool's exchange rate at time T1 = call to `handle_bankruptcy`, which can be much later. Any user who deposits into the bank between T0 and T1 is included in `total_asset_shares` at T1 and is charged a proportional share of a loss that predates their deposit, exactly mirroring the unfairly-slashed depositor scenario in the report. There is also no getter/warning mechanism analogous to what Karak added ("a getter to determine if a vault's queued for slashing") — a marginfi depositor has no on-chain way to know a bank is carrying unresolved bad debt before depositing.

### Impact Explanation
A depositor who deposits into a bank with outstanding, unsettled bad debt receives shares at the current (pre-socialization) `asset_share_value`. When `handle_bankruptcy` is subsequently (and, for flagged banks, permissionlessly) called, `socialize_loss` lowers `asset_share_value` for the entire pool, so the new depositor's shares are devalued for a loss event that occurred before they had any exposure. In the extreme (super-bankruptcy) case, `asset_share_value` is driven to zero and the bank is killed, wiping out the new depositor's funds entirely: [5](#0-4) 
This is a durable, financially concrete misvaluation/value-redirection affecting any unprivileged depositor, not merely a theoretical concern — the protocol's own bankruptcy guide documents that socialization is proportional to whoever holds shares "among all remaining depositors" at settlement time.

### Likelihood Explanation
The trigger conditions are realistic and already anticipated by the protocol: liquidators lagging, illiquid collateral, or sharp price moves are explicitly listed as causes of bankruptcy in `BANKRUPTCY.md`, and the settlement step is described as a distinct, later admin/permissionless action rather than atomic with the loss. Any bank with `PERMISSIONLESS_BAD_DEBT_SETTLEMENT_FLAG` set allows any unprivileged party to trigger the socialization at a time of their choosing, and deposits into that bank are not blocked or flagged while bad debt is outstanding, so an ordinary user (or an attacker front-running/timing socialization to affect a competitor's newly opened position) can realistically be caught in this window without any indication of risk.

### Recommendation
- Track outstanding unsettled bad debt per bank (e.g., a running total or a flag) and expose it via a getter so integrators/UIs can warn depositors before they deposit into a bank carrying unresolved bad debt.
- Consider disallowing/rate-limiting new deposits into a bank while it has known, unsettled bankrupt positions, or require `handle_bankruptcy` to be settled promptly (e.g., permissionless callers incentivized, or forced settlement bundled with liquidation) so the window between loss creation and socialization is minimized.
- At minimum, document and surface (on-chain, via bank cache or a dedicated field) that a bank has pending bad debt so third-party depositors and integrators can make an informed choice, mirroring Karak's mitigation of exposing a "queued for slash" getter.

### Proof of Concept
1. Bank B has a borrower whose collateral has been fully liquidated, leaving bad debt (`bank.get_liability_amount(...) > 0`), as verified via `check_account_bankrupt` — this state can persist indefinitely since nothing forces immediate settlement.
2. `PERMISSIONLESS_BAD_DEBT_SETTLEMENT_FLAG` is set on B (or an admin simply hasn't called `handle_bankruptcy` yet).
3. User U deposits into B via the normal deposit flow. `total_asset_shares` increases; `asset_share_value` is unaffected by the still-unresolved bad debt.
4. Anyone (or the admin) calls `lending_pool_handle_bankruptcy` for the bankrupt account on B. `bank.socialize_loss(socialized_loss)` executes as shown in `programs/marginfi/src/state/bank.rs:852-886`, recomputing `asset_share_value` using `total_asset_shares`, which now includes U's newly minted shares.
5. U's shares are devalued by a proportional amount of the socialized loss even though U had no exposure to the bank when the bad debt was created, and had no way to detect the pending bad debt before depositing.

### Citations

**File:** programs/marginfi/src/instructions/marginfi_group/handle_bankruptcy.rs (L52-67)
```rust
    let maybe_bank_mint = {
        let bank = bank_loader.load()?;
        let group = marginfi_group_loader.load()?;
        let signer = ctx.accounts.signer.key();
        let is_admin_or_risk_admin = signer == group.risk_admin || signer == group.admin;
        let permissionless_bad_debt_settlement =
            bank.get_flag(PERMISSIONLESS_BAD_DEBT_SETTLEMENT_FLAG);

        if permissionless_bad_debt_settlement {
            // if permissionless, users can bankrupt reduce-only or operational banks
            validate_bank_state(&bank, InstructionKind::FailsInPausedState)?;
        } else {
            // admin can bankrupt banks in any state
            validate_bank_state(&bank, InstructionKind::Unrestricted)?;
            check!(is_admin_or_risk_admin, MarginfiError::Unauthorized);
        }
```

**File:** programs/marginfi/src/instructions/marginfi_group/handle_bankruptcy.rs (L130-190)
```rust
    let (covered_by_insurance, socialized_loss) = {
        let available_insurance_fund: I80F48 = maybe_bank_mint
            .as_ref()
            .map(|mint| {
                utils::calculate_post_fee_spl_deposit_amount(
                    mint.to_account_info(),
                    insurance_vault.amount,
                    clock.epoch,
                )
            })
            .transpose()?
            .unwrap_or(insurance_vault.amount)
            .into();

        let covered_by_insurance = min(bad_debt, available_insurance_fund);
        let socialized_loss = max(bad_debt - covered_by_insurance, I80F48::ZERO);

        (covered_by_insurance, socialized_loss)
    };

    // Cover bad debt with insurance funds.
    let covered_by_insurance_rounded_up: u64 = covered_by_insurance
        .checked_ceil()
        .ok_or_else(math_error!())?
        .checked_to_num()
        .ok_or_else(math_error!())?;
    debug!(
        "covered_by_insurance_rounded_up: {}; socialized loss {}",
        covered_by_insurance_rounded_up,
        socialized_loss.to_num::<f64>()
    );

    let insurance_coverage_deposit_pre_fee = maybe_bank_mint
        .as_ref()
        .map(|mint| {
            utils::calculate_pre_fee_spl_deposit_amount(
                mint.to_account_info(),
                covered_by_insurance_rounded_up,
                clock.epoch,
            )
        })
        .transpose()?
        .unwrap_or(covered_by_insurance_rounded_up);

    bank.withdraw_spl_transfer(
        insurance_coverage_deposit_pre_fee,
        ctx.accounts.insurance_vault.to_account_info(),
        ctx.accounts.liquidity_vault.to_account_info(),
        ctx.accounts.insurance_vault_authority.to_account_info(),
        maybe_bank_mint.as_ref(),
        token_program.to_account_info(),
        bank_signer!(
            BankVaultType::Insurance,
            bank_loader.key(),
            bank.insurance_vault_authority_bump
        ),
        ctx.remaining_accounts,
    )?;

    // Socialize bad debt among depositors.
    let kill_bank = bank.socialize_loss(socialized_loss)?;
```

**File:** programs/marginfi/src/instructions/marginfi_group/handle_bankruptcy.rs (L205-211)
```rust
    marginfi_account.set_flag(ACCOUNT_DISABLED, true);
    marginfi_account.indexer_flags.has_been_bankrupted = 1;
    marginfi_account.last_update = clock.unix_timestamp as u64;
    if kill_bank {
        msg!("bank had debt exceeding liabilities and has been killed");
        bank.config.operational_state = BankOperationalState::KilledByBankruptcy;
    }
```

**File:** programs/marginfi/src/state/bank.rs (L852-886)
```rust
    /// Socialize a loss of `loss_amount` among depositors, the `total_deposit_shares` stays the
    /// same, but total value of deposits is reduced by `loss_amount`;
    ///
    /// In cases where assets < liabilities, the asset share value will be set to zero, but cannot
    /// go negative. Effectively, depositors forfeit their entire deposit AND all earned interest in
    /// this case.
    fn socialize_loss(&mut self, loss_amount: I80F48) -> MarginfiResult<bool> {
        let mut kill_bank = false;
        let total_asset_shares: I80F48 = self.total_asset_shares.into();
        let old_asset_share_value: I80F48 = self.asset_share_value.into();

        // Compute total "old" value of shares
        let total_value: I80F48 = total_asset_shares
            .checked_mul(old_asset_share_value)
            .ok_or_else(math_error!())?;

        // Subtract loss, clamping at zero (i.e. assets < liabilities, the bank is wiped out)
        if total_value <= loss_amount {
            self.asset_share_value = I80F48::ZERO.into();
            // This state is irrecoverable, the bank is dead.
            kill_bank = true;
        } else {
            // otherwise subtract then redistribute
            let new_share_value: I80F48 = (total_value - loss_amount)
                .checked_div(total_asset_shares)
                .ok_or_else(math_error!())?;
            self.asset_share_value = new_share_value.into();
            // Sanity check: should be unreachable.
            if new_share_value == I80F48::ZERO {
                kill_bank = true;
            }
        }

        Ok(kill_bank)
    }
```

**File:** guides/RISK_AND_LIQUIDATORS/BANKRUPTCY.md (L27-58)
```markdown
## Discharging a Bankruptcy

First, liquidators consume all the remaining assets that the user has. If the user has A dollars in
assets and B dollars in liabilities (in equity value, i.e. excluding any weights), we know that B >
A. After liquidation is complete, A_new = 0, and B_new = B - A + X, where X is the liquidation
premium and insurance.

Run `collect_bank_fees` before beginning the next step so the insurance fund is fully capitalized.

Next, the group administrator runs `handle_bankruptcy` on the user. For banks where
`PERMISSIONLESS_BAD_DEBT_SETTLEMENT_FLAG` is enabled, anyone can do this. This will perform the
following logic:

* If bank's insurance fund > liabilities, then the insurance fund is used to repay the user's liability.
* If the bank's insurance fund is not sufficient, the remainder will be covered by taking liquidity
  out of the bank, reducing the asset share value. This socializes the loss to all remaining
  depositors.
* If the bank's insurance fund and liquidity are not sufficient (super-bankruptcy), the bank is
  killed. The asset share value is set to zero, wiping out all holdings for all other depositors.
  This state is irrecoverable, and the bank is permanently disabled.

## FAQ

### What Happens if it Doesn't Run?

If Bankruptcy isn't executed on a bankrupt user, then remaining depositors can never withdraw the
whole balance in the bank. The last few depositors who try to withdraw will find there are not
enough funds - proportional to the liabilities held by bankrupt users.

### When Does This Matter?

Ideally, never. As of November 2025, bankruptcy has never been executed in the main pool.
```
