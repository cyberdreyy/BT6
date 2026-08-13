### Title
Front-runnable share-price inflation via `lending_pool_emissions_deposit()` allows depositors to steal a portion of injected emissions - (File: `programs/marginfi/src/instructions/marginfi_group/configure_bank.rs`)

### Summary
`lending_pool_emissions_deposit` is a **permissionless** instruction that transfers a lump sum of tokens directly into a bank's `liquidity_vault` and immediately raises `asset_share_value` for all existing depositors in one shot [1](#0-0) . This is functionally the same pattern as the reported `BathPair.sol#rebalancePair()` issue: an external actor injects funds into a shared pool, causing a discrete jump in price-per-share, which can be front-run by depositing right before the injection and withdrawing right after.

### Finding Description
The instruction computes the new `asset_share_value` as `(total_assets + amount) / total_asset_shares`, applying the entire deposited `amount` across the *current* `total_asset_shares` at the moment the transaction executes [2](#0-1) . Anyone can construct and submit this transaction (it is explicitly documented and coded as permissionless — see the doc comment "Permissionlessly deposit same-mint emissions directly into the bank liquidity vault, increasing depositor value through asset share value" and the corresponding entrypoint comment in `lib.rs`) [3](#0-2) [4](#0-3) .

Because the transaction (and its resulting `amount`) is visible in the mempool/leader schedule before landing, an attacker can:
1. Observe a pending `lending_pool_emissions_deposit(amount)` call for a given bank.
2. Front-run it with a large `lending_account_deposit` into the same bank, minting shares at the pre-injection `asset_share_value`.
3. Let the emissions-deposit transaction land, which raises `asset_share_value` proportionally across *all* `total_asset_shares`, including the attacker's freshly minted shares.
4. Immediately withdraw via `lending_account_withdraw`, capturing a pro-rata share of the injected `amount` without contributing any real capital/time to the pool.

This mirrors the root cause in the referenced report: a lump-sum injection into a shared value pool causes an instantaneous share-price jump that is not gated by any time-weighting, minimum holding period, or streaming/vesting mechanism — unlike organic interest accrual in `bank.accrue_interest()`, which is a continuous, time-proportional function of `time_delta` and thus not similarly exploitable [5](#0-4) .

Ordinary deposit/withdraw instructions do call `accrue_interest` first, but that only accounts for time-based interest — it does not protect against or throttle a same-block lump-sum injection like `lending_pool_emissions_deposit`. Standard `lending_account_deposit`/`lending_account_withdraw` paths are unmodified and readily available to any depositor to exploit this window [6](#0-5) [7](#0-6) .

### Impact Explanation
Any funds injected via `lending_pool_emissions_deposit` (e.g., protocol-sponsored reward top-ups) can be partially or substantially captured by an opportunistic large depositor who has no real economic stake in the bank beyond a single-block deposit/withdraw pair, diluting the actual intended beneficiaries (long-term/organic depositors). The larger the attacker's flash deposit relative to existing `total_asset_shares`, the larger the fraction of the emissions they can siphon — in the limit, an attacker depositing an amount much larger than the existing pool can capture close to 100% of the injected emissions. This constitutes value redirection with direct financial effect on legitimate depositors and the emissions sponsor.

### Likelihood Explanation
The instruction is explicitly permissionless (any `depositor: Signer` can call it), so its calldata, including the target bank and injected `amount`, is publicly visible before confirmation, standard on Solana. Any user or MEV searcher can watch for pending `LendingPoolEmissionsDeposit` transactions and race a deposit before them and a withdrawal after using the standard `lending_account_deposit`/`lending_account_withdraw` instructions, which are open to any signer. No special privileges are required, and the attack is a single flash-loan-style deposit/withdraw sequence (potentially within one or two slots), making this readily exploitable whenever emissions deposits of meaningful size are made.

### Recommendation
Do not apply lump-sum injected value instantaneously and proportionally to the current share supply. Options:
- Stream/vest the injected `amount` into `asset_share_value` over time (similar to how interest accrues continuously via `time_delta` in `accrue_interest`) rather than applying it atomically in a single instruction.
- Require the emissions deposit to only affect shares that existed for some minimum bonding period, or snapshot eligible shares prior to the deposit.
- Add a cooldown/timelock between a large deposit and a subsequent withdrawal for the same account within the same bank (e.g., disallow same-slot or short-window deposit+withdraw sequences).
- Alternatively, restrict `lending_pool_emissions_deposit` to a scheduled/admin-gated cadence with pre-announced timing, and/or cap per-call amount relative to `total_asset_shares` to bound the maximum extractable value per front-run.

### Proof of Concept
1. Given a USDC bank with `total_asset_shares` corresponding to `100,000` USDC of total assets, `asset_share_value = 1.0`.
2. A benefactor prepares to call `lending_pool_emissions_deposit(amount = 10,000)` to top up rewards for depositors, per [8](#0-7) .
3. Attacker observes the pending transaction and front-runs it with `lending_account_deposit` of `100,000` USDC, doubling `total_asset_shares` to represent `200,000` USDC of total value at the pre-injection `asset_share_value`.
4. The emissions-deposit transaction lands: `updated_total_assets = 200,000 + 10,000 = 210,000`; `asset_share_value` is recomputed as `210,000 / total_asset_shares`, per [9](#0-8) , raising the attacker's shares' value proportionally.
5. Attacker immediately calls `lending_account_withdraw` (or `withdraw_all`) to redeem `~105,000` USDC — half of the injected `10,000` USDC emissions — for a net profit of `~5,000` USDC in about one block, without any prior deposit history or organic risk exposure, per the withdraw path in [10](#0-9) .

### Citations

**File:** programs/marginfi/src/instructions/marginfi_group/configure_bank.rs (L84-146)
```rust
/// Permissionlessly deposit same-mint emissions directly into the bank liquidity vault,
/// increasing depositor value through asset share value.
pub fn lending_pool_emissions_deposit(
    ctx: Context<LendingPoolEmissionsDeposit>,
    amount: u64,
) -> MarginfiResult {
    if amount == 0 {
        return Ok(());
    }

    let clock = Clock::get()?;
    let mut bank = ctx.accounts.bank.load_mut()?;
    let group = ctx.accounts.group.load()?;

    utils::validate_bank_state(&bank, utils::InstructionKind::FailsIfPausedOrReduceState)?;

    // Reject mints with non-zero transfer fees or active transfer hooks.
    let mint_ai = ctx.accounts.mint.to_account_info();
    check!(
        !utils::nonzero_fee(mint_ai.clone(), clock.epoch)?,
        MarginfiError::InvalidTransfer
    );
    check!(
        !utils::has_transfer_hook(mint_ai)?,
        MarginfiError::InvalidTransfer
    );

    let total_asset_shares = I80F48::from(bank.total_asset_shares);
    check!(
        total_asset_shares > I80F48::ZERO,
        MarginfiError::EmissionsUpdateError
    );

    bank.accrue_interest(
        clock.unix_timestamp,
        &group,
        #[cfg(not(feature = "client"))]
        ctx.accounts.bank.key(),
    )?;

    transfer_checked(
        CpiContext::new(
            ctx.accounts.token_program.key(),
            TransferChecked {
                from: ctx.accounts.emissions_funding_account.to_account_info(),
                to: ctx.accounts.liquidity_vault.to_account_info(),
                authority: ctx.accounts.depositor.to_account_info(),
                mint: ctx.accounts.mint.to_account_info(),
            },
        ),
        amount,
        ctx.accounts.mint.decimals,
    )?;

    let total_assets = bank.get_asset_amount(total_asset_shares)?;
    let updated_total_assets = total_assets
        .checked_add(I80F48::from_num(amount))
        .ok_or_else(math_error!())?;

    bank.asset_share_value = updated_total_assets
        .checked_div(total_asset_shares)
        .ok_or_else(math_error!())?
        .into();
```

**File:** programs/marginfi/src/lib.rs (L209-216)
```rust
    /// (permissionless) Deposit same-bank emissions directly into liquidity vault and increase
    /// depositors' value via `asset_share_value`.
    pub fn lending_pool_emissions_deposit(
        ctx: Context<LendingPoolEmissionsDeposit>,
        amount: u64,
    ) -> MarginfiResult {
        marginfi_group::lending_pool_emissions_deposit(ctx, amount)
    }
```

**File:** programs/marginfi/src/state/bank.rs (L520-574)
```rust
        let time_delta: u64 = (current_timestamp - self.last_update).try_into().unwrap();
        if time_delta == 0 {
            return Ok(());
        }

        let total_assets = self.get_asset_amount(self.total_asset_shares.into())?;
        let total_liabilities = self.get_liability_amount(self.total_liability_shares.into())?;

        self.last_update = current_timestamp;

        if (total_assets == I80F48::ZERO) || (total_liabilities == I80F48::ZERO) {
            #[cfg(not(feature = "client"))]
            emit!(LendingPoolBankAccrueInterestEvent {
                header: GroupEventHeader {
                    marginfi_group: self.group,
                    signer: None
                },
                bank,
                mint: self.mint,
                delta: time_delta,
                fees_collected: 0.,
                insurance_collected: 0.,
            });

            return Ok(());
        }
        let ir_calc = self
            .config
            .interest_rate_config
            .create_interest_rate_calculator(group);

        let InterestRateStateChanges {
            new_asset_share_value: asset_share_value,
            new_liability_share_value: liability_share_value,
            insurance_fees_collected,
            group_fees_collected,
            protocol_fees_collected,
        } = calc_interest_rate_accrual_state_changes(
            time_delta,
            total_assets,
            total_liabilities,
            &ir_calc,
            self.asset_share_value.into(),
            self.liability_share_value.into(),
        )?;

        debug!("deposit share value: {}\nliability share value: {}\nfees collected: {}\ninsurance collected: {}",
            asset_share_value, liability_share_value, group_fees_collected, insurance_fees_collected);

        self.cache.accumulated_since_last_update = asset_share_value
            .checked_sub(I80F48::from(self.asset_share_value))
            .and_then(|v| v.checked_mul(I80F48::from(self.total_asset_shares)))
            .ok_or_else(math_error!())?
            .into();
        self.cache.interest_accumulated_for = time_delta.min(u32::MAX as u64) as u32;
```

**File:** programs/marginfi/src/instructions/marginfi_account/deposit.rs (L27-30)
```rust
/// 1. Accrue interest
/// 2. Create the user's bank account for the asset deposited if it does not exist yet
/// 3. Record asset increase in the bank account
/// 4. Transfer funds from the signer's token account to the bank's liquidity vault
```

**File:** programs/marginfi/src/instructions/marginfi_account/withdraw.rs (L38-49)
```rust
/// 1. Accrue interest
/// 2. Find the user's existing bank account for the asset withdrawn
/// 3. Record asset decrease in the bank account
/// 4. Transfer funds from the bank's liquidity vault to the signer's token account
/// 5. Verify that the user account is in a healthy state
///
/// Will error if there is no existing asset <=> borrowing is not allowed.
pub fn lending_account_withdraw<'info>(
    mut ctx: Context<'info, LendingAccountWithdraw<'info>>,
    amount: u64,
    withdraw_all: Option<bool>,
) -> MarginfiResult {
```

**File:** programs/marginfi/src/instructions/marginfi_account/withdraw.rs (L98-131)
```rust
        bank.accrue_interest(
            clock.unix_timestamp,
            &group,
            #[cfg(not(feature = "client"))]
            bank_loader.key(),
        )?;

        let liquidity_vault_authority_bump = bank.liquidity_vault_authority_bump;

        let in_receivership = marginfi_account.get_flag(ACCOUNT_IN_RECEIVERSHIP);
        let lending_account = &mut marginfi_account.lending_account;
        let mut bank_account =
            BankAccountWrapper::find(&bank_loader.key(), &mut bank, lending_account)?;

        let (amount_pre_fee, share_amount) = if withdraw_all {
            // Note: In liquidation, we still want this passed on the books
            bank_account.withdraw_all(in_receivership)?
        } else {
            let amount_pre_fee = maybe_bank_mint
                .as_ref()
                .map(|mint| {
                    utils::calculate_pre_fee_spl_deposit_amount(
                        mint.to_account_info(),
                        amount,
                        clock.epoch,
                    )
                })
                .transpose()?
                .unwrap_or(amount);

            let share_amount = bank_account.withdraw(I80F48::from_num(amount_pre_fee))?;

            (amount_pre_fee, share_amount)
        };
```
