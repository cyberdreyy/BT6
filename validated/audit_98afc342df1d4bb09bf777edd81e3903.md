### Title
Missing check of Drift User account liquidation/bankruptcy status before CPI withdraw/deposit can permanently freeze a Drift-integrated bank - ([File: programs/marginfi/src/instructions/drift/withdraw.rs])

### Summary
The Liquity report describes a strategy that fails to check the state of an external position (`trove`) before calling operations that only succeed when the position is in one specific state (`active`), causing durable reverts once the external state changes. Marginfi's Drift integration has the same structural gap: the shared, bank-owned Drift `User` account can enter a non-`Active` status (`BeingLiquidated`/`Bankrupt`) on the Drift side, but marginfi never checks this status before issuing withdraw/deposit CPIs to Drift.

### Finding Description
Each mrgn-wrapped Drift bank owns a single Drift `User` account (`integration_acc_2`, `MinimalUser`) controlled by the bank's `liquidity_vault_authority` PDA, shared across all depositors into that bank. [1](#0-0) 

Drift's own `MinimalUser` model exposes a `status` field (`UserStatus::Active/BeingLiquidated/Bankrupt/...`) and even implements a helper `is_being_liquidated()` specifically to detect the non-active analog of the Liquity trove's non-`active` states: [2](#0-1) 

However, `is_being_liquidated()` is never called anywhere in the marginfi program. The `DriftWithdraw` account constraints only validate spot-position layout, reward-account presence, and "bricked by admin deposits" — none of them check `status`: [3](#0-2) 

The `drift_withdraw` and `drift_deposit` handlers proceed directly to CPI (`cpi_drift_withdraw` / `cpi_drift_deposit`) without any pre-check of `integration_acc_2`'s liquidation/bankruptcy status: [4](#0-3) [5](#0-4) 

This exactly mirrors the Liquity bug class: `_deposit()`/`liquidatePosition()` assume the external position is always in the one workable state and never branch on the other lifecycle states, so once the trove (here, the Drift `User` account) transitions out of `Active` — analogous to `closedByLiquidation`/`closedByRedemption` — every subsequent call that depends on the "active" assumption reverts.

### Impact Explanation
If Drift's own risk engine ever puts the bank's shared `User` account into `BeingLiquidated` or `Bankrupt` status (e.g., due to price moves affecting admin-deposited reward balances that are also tracked on this same account, or any other Drift-side health event on this shared account), Drift's native `withdraw`/`deposit` instructions can be expected to reject calls from an account under liquidation, exactly as Liquity's `adjustTrove()` reverts for a non-`active` trove. Because marginfi never checks or handles this status, `drift_withdraw`/`drift_deposit` (and by extension any strategy/harvest-equivalent flow, i.e., normal user withdrawals) would revert unconditionally for every depositor in that bank — not just one user, since this Drift `User` account is shared by the whole bank. This durably freezes withdrawals for all depositors of the affected Drift-wrapped bank until the underlying Drift account exits the liquidation/bankruptcy state, an event outside marginfi's control, exactly matching the "the strategy will become broken and should be manually removed... blocking any withdrawal" impact described in the report, but affecting many users' funds rather than one trove.

### Likelihood Explanation
The likelihood depends on whether Drift's live protocol would ever place a marginfi-owned `User` PDA into `BeingLiquidated`/`Bankrupt` (this can occur if the account, due to admin-deposited reward assets or an unexpected borrow/liability appearing on the account, becomes unhealthy from Drift's own risk-engine perspective). The presence of `validate_not_bricked_by_admin_deposits()` in the same code path shows the team is already aware that Drift-side/administrative events can push this shared account into unusual states outside normal user-driven risk, making a similar liquidation/bankruptcy status transition a plausible, unprivileged-triggerable (via market conditions, not requiring any marginfi-side permission) event. The dead `is_being_liquidated()` helper strongly suggests this check was intended but not wired in, which is a genuine gap rather than a purely theoretical concern.

### Recommendation
Add an explicit check of `integration_acc_2.load()?.is_being_liquidated()` (or equivalent status check) as an account constraint or early guard in both `DriftWithdraw` and `DriftDeposit`, returning a clear, distinct error (e.g., `DriftUserBeingLiquidated`) instead of allowing an uncontrolled CPI revert. More importantly, provide a recovery/monitoring path (permissionless or admin instruction) that can detect this state and either wait for Drift-side resolution or migrate/redeem funds, so that a bank-wide freeze does not become durable and unrecoverable, mirroring how marginfi already handles bank-level `KilledByBankruptcy`/`Paused`/`ReduceOnly` states elsewhere via `validate_bank_state`.

### Proof of Concept
1. A Drift-wrapped marginfi bank's shared `User` account (`integration_acc_2`) accrues balances, including any reward/admin-deposited assets tracked on the same account.
2. Due to a Drift-side event (oracle move, protocol admin action, or reward asset revaluation) the Drift program transitions this `User` account's `status` to `BeingLiquidated` or `Bankrupt`.
3. A depositor calls `drift_withdraw` (or `drift_deposit`) through marginfi. The `DriftWithdraw`/`DriftDeposit` account validation only checks spot-position layout, reward-account presence, and "bricked by admin deposits" — not `status` — so the instruction proceeds to CPI into Drift's native `withdraw`/`deposit`. [6](#0-5) 
4. Drift's native program rejects the CPI because the user account is under liquidation/bankruptcy, causing the entire marginfi transaction to revert.
5. Every depositor of that bank experiences the same revert on every withdraw/deposit attempt until Drift-side liquidation/bankruptcy resolves — a condition marginfi cannot influence or check for — resulting in a durable freeze of that bank's funds for all its depositors.

### Citations

**File:** programs/marginfi/src/instructions/drift/withdraw.rs (L394-410)
```rust
    #[account(
        mut,
        constraint = {
            let user = integration_acc_2.load()?;
            let spot_market = integration_acc_1.load()?;
            user.validate_spot_position(spot_market.market_index).is_ok()
        } @ MarginfiError::DriftInvalidSpotPositions,
        constraint = {
            let user = integration_acc_2.load()?;
            user.validate_reward_accounts(
                drift_reward_spot_market.is_none(),
                drift_reward_spot_market_2.is_none(),
            ).is_ok()
        } @ MarginfiError::DriftMissingRewardAccounts,
        constraint = integration_acc_2.load()?.validate_not_bricked_by_admin_deposits().is_ok() @ MarginfiError::DriftBrickedAccount
    )]
    pub integration_acc_2: AccountLoader<'info, MinimalUser>,
```

**File:** programs/marginfi/src/instructions/drift/withdraw.rs (L489-566)
```rust
    pub fn cpi_drift_withdraw(
        &self,
        market_index: u16,
        amount: u64,
        authority_bump: u8,
    ) -> MarginfiResult {
        let accounts = Withdraw {
            state: self.drift_state.to_account_info(),
            user: self.integration_acc_2.to_account_info(),
            user_stats: self.integration_acc_3.to_account_info(),
            authority: self.liquidity_vault_authority.to_account_info(),
            spot_market_vault: self.drift_spot_market_vault.to_account_info(),
            drift_signer: self.drift_signer.to_account_info(),
            user_token_account: self.liquidity_vault.to_account_info(),
            token_program: self.token_program.to_account_info(),
        };

        let program = self.drift_program.to_account_info();
        let signer_seeds: &[&[&[u8]]] =
            bank_signer!(BankVaultType::Liquidity, self.bank.key(), authority_bump);
        let mut cpi_ctx = CpiContext::new_with_signer(program.key(), accounts, signer_seeds);

        // Construct remaining accounts in the required order for Drift:
        // 1. Oracle accounts (if provided) - main oracle first, then reward oracle
        // 2. Spot market accounts - main spot market first, then reward spot market
        // 3. Token mint (required for Token-2022, harmless to include for regular mints)
        //
        // IMPORTANT: If admin deposits exist in other markets (rewards), you MUST:
        // 1. Include the reward oracle and spot market accounts
        // 2. Harvest the rewards immediately after withdrawal
        // Drift typically only has one reward asset at a time
        let mut remaining_accounts = Vec::new();

        // Add main oracle if provided (not needed if using oracle type QuoteAsset)
        if let Some(oracle) = &self.drift_oracle {
            remaining_accounts.push(oracle.to_account_info());
        }

        // Add first reward oracle if provided (for admin deposits)
        if let Some(reward_oracle) = &self.drift_reward_oracle {
            remaining_accounts.push(reward_oracle.to_account_info());
        }

        // Add second reward oracle if provided (backup for multiple rewards)
        if let Some(reward_oracle_2) = &self.drift_reward_oracle_2 {
            remaining_accounts.push(reward_oracle_2.to_account_info());
        }

        // Always add main spot market account
        remaining_accounts.push(self.integration_acc_1.to_account_info());

        // Add first reward spot market if provided (for admin deposits)
        if let Some(reward_spot_market) = &self.drift_reward_spot_market {
            remaining_accounts.push(reward_spot_market.to_account_info());
        }

        // Add second reward spot market if provided (backup for multiple rewards)
        if let Some(reward_spot_market_2) = &self.drift_reward_spot_market_2 {
            remaining_accounts.push(reward_spot_market_2.to_account_info());
        }

        // Always add main token mint (needed for Token-2022 support)
        remaining_accounts.push(self.mint.to_account_info());

        if let Some(reward_mint) = &self.drift_reward_mint {
            remaining_accounts.push(reward_mint.to_account_info());
        }

        if let Some(reward_mint_2) = &self.drift_reward_mint_2 {
            remaining_accounts.push(reward_mint_2.to_account_info());
        }

        cpi_ctx = cpi_ctx.with_remaining_accounts(remaining_accounts);

        // Call drift withdraw with reduce_only = true (don't allow borrowing)
        withdraw(cpi_ctx, market_index, amount, true)?;
        Ok(())
    }
```

**File:** programs/drift-mocks/src/state.rs (L95-153)
```rust
#[derive(Clone, Copy, Debug, PartialEq, AnchorSerialize, AnchorDeserialize)]
#[borsh(use_discriminant = true)]
#[repr(u8)]
pub enum UserStatus {
    Active = 0,
    BeingLiquidated = 0b00000001,
    Bankrupt = 0b00000010,
    ReduceOnly = 0b00000100,
    AdvancedLp = 0b00001000,
    ProtectedMakerOrders = 0b00010000,
}

unsafe impl Zeroable for UserStatus {}
unsafe impl Pod for UserStatus {}

assert_struct_size!(MinimalUser, 4368);
assert_struct_align!(MinimalUser, 8);
/// Minimal representation of Drift's User account
/// Only includes the fields we actually need
#[account(zero_copy, discriminator = &USER_DISCRIMINATOR)]
#[repr(C)]
pub struct MinimalUser {
    /// The owner/authority of the account
    pub authority: Pubkey,
    /// An addresses that can control the account on the authority's behalf
    pub delegate: Pubkey,
    /// Encoded display name for the account
    pub name: [u8; 32],

    /// The user's spot positions (8 positions)
    pub spot_positions: [SpotPosition; 8],

    /// Skip to the fields we need at the end
    pub _padding1: [u64; 256],
    pub _padding2: [u64; 128],
    pub _padding3: [u64; 64],
    pub _padding4: [u64; 32],
    pub _padding5: [u64; 8],
    pub _padding6: [u64; 2],
    pub _padding7: [u16; 1],

    /// Sub account id for this user account
    pub sub_account_id: u16,

    // Status and flags
    pub status: UserStatus,

    // Final padding to reach exactly 4376 bytes (including discriminator)
    pub _padding8: [u8; 27],
}

impl MinimalUser {
    pub fn is_being_liquidated(&self) -> bool {
        matches!(
            self.status,
            UserStatus::BeingLiquidated | UserStatus::Bankrupt
        )
    }
}
```

**File:** programs/marginfi/src/instructions/drift/deposit.rs (L45-74)
```rust
pub fn drift_deposit(ctx: Context<DriftDeposit>, amount: u64) -> MarginfiResult {
    let authority_bump: u8;
    let market_index: u16;
    {
        let marginfi_account = ctx.accounts.marginfi_account.load()?;
        let bank = ctx.accounts.bank.load()?;
        authority_bump = bank.liquidity_vault_authority_bump;

        validate_asset_tags(&bank, &marginfi_account)?;
        validate_bank_state(&bank, InstructionKind::FailsIfPausedOrReduceState)?;

        let integration_acc_1 = ctx.accounts.integration_acc_1.load()?;
        market_index = integration_acc_1.market_index;
    }

    ctx.accounts.cpi_update_spot_market_cumulative_interest()?;
    let expected_scaled_balance_change = ctx
        .accounts
        .integration_acc_1
        .load()?
        .get_scaled_balance_increment(amount)?;

    let initial_scaled_balance = {
        let integration_acc_2 = ctx.accounts.integration_acc_2.load()?;
        integration_acc_2.get_scaled_balance(market_index)
    };

    ctx.accounts.cpi_transfer_user_to_liquidity_vault(amount)?;
    ctx.accounts
        .cpi_drift_deposit(market_index, amount, authority_bump)?;
```
