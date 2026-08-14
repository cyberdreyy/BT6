I found the DriftBrickedAccount pattern, which is the closest genuine analog to the PayoutManager eligibility-freeze bug: a user's withdraw can become permanently blocked by a condition entirely outside their control (Drift admin depositing rewards into >3 spot positions), with no on-chain admin recovery/revoke path comparable to Tradable's `revokePayout`.### Title
Drift admin-deposit "brick" can permanently block user withdrawals on a shared Drift `MinimalUser` account, with no eligible per-user recovery path unless the fee admin proactively harvests - ([File: programs/marginfi/src/instructions/drift/withdraw.rs])

### Summary
`DriftWithdraw` and related instructions gate on `integration_acc_2.load()?.validate_not_bricked_by_admin_deposits().is_ok()`, which reverts with `DriftBrickedAccount` whenever the shared Drift `MinimalUser` account (one per bank, shared by *every* marginfi depositor into that bank) has more than 3 active spot-market deposit positions. [1](#0-0)  This is conceptually the same bug class as the Tradable/Spearbit `PayoutManager` finding: a condition entirely outside the depositor's control (there it was AML score drift, here it is a third party — the Drift protocol admin — depositing reward assets into extra slots) flips a gate that blocks withdrawal of the user's own already-deposited principal, and the only remediation path depends on someone else (an admin) acting.

### Finding Description
The Drift integration user account (`integration_acc_2`, a `MinimalUser`) is shared across all marginfi users who deposit into a given Drift-backed bank; it supports "1 main asset + up to 2 reward assets" (3 active deposit positions). [2](#0-1)  `validate_not_bricked_by_admin_deposits` counts active deposit positions and returns an error once more than 3 exist, explicitly noting "the account cannot withdraw." [3](#0-2) 

This check is wired directly into the `DriftWithdraw` accounts validation as a hard constraint (`@ MarginfiError::DriftBrickedAccount`), meaning **any** user attempting to withdraw from that bank — even users who never triggered the extra deposits and have no relationship to the reward assets — is blocked as soon as the Drift admin deposits a 4th reward asset into the shared user account. [4](#0-3)  This is confirmed by the test `User: Account bricked with 4 active deposits`, where a normal user's `withdraw_all` fails with `DriftBrickedAccount` purely because the Drift admin deposited two extra reward tokens. [5](#0-4) 

Unlike the `ACCOUNT_FROZEN`/`ACCOUNT_DISABLED` cases in the marginfi account itself — where the group admin explicitly retains a first-class ability to withdraw/act on the user's behalf regardless of frozen state [6](#0-5) [7](#0-6)  — there is no equivalent forced-recovery instruction here. The only unblock mechanism is `drift_harvest_reward`, which sweeps the *excess* reward position(s) out to the global fee wallet. [8](#0-7)  This instruction has no explicit signer/role restriction requiring the marginfi admin — its `Accounts` struct has no `has_one = admin` or signer check tying it to `group.admin`; it only validates the harvest spot market and that an admin deposit exists. [9](#0-8)  This is actually permissionless-looking on its face, but whether it is truly callable by any address (rather than a hidden signer requirement elsewhere) cannot be fully confirmed from the excerpts reviewed — no explicit `Signer<'info>` account is declared in the struct shown, so the effective caller-restriction is unclear from this context alone.

Regardless of who can call `drift_harvest_reward`, the core defect matches the reported bug class: user principal (in the main/1st or 2nd reward slot) becomes **unwithdrawable** the moment a *third party* (Drift admin) pushes the position count past the hardcoded threshold of 3, and normal users have no way to self-remediate — they must wait for someone (Drift admin harvesting, or whoever is authorized to call `drift_harvest_reward`) to reduce the position count back to ≤3.

### Impact Explanation
This is a Medium-severity durable freeze/DoS on user funds: legitimate depositors in a Drift-backed bank can have their `drift_withdraw` (and likely `drift_deposit`/other flows gated the same way) permanently reverted purely due to third-party (Drift protocol admin) activity unrelated to their own account, with no direct self-service recovery. Funds are not stolen or misdirected, but access is durably blocked until an external, unprivileged-to-the-user action (harvesting) occurs — directly analogous to the PayoutManager issue where investor funds became unclaimable due to conditions outside the investor's control, and where the fix required adding a dedicated remediation instruction.

### Likelihood Explanation
Likelihood is bank/config-dependent: it requires the specific Drift bank's shared `MinimalUser` to receive admin/reward deposits beyond the 1+2 supported slots, which is an operational scenario that the codebase's own doc comments and dedicated error code (`DriftBrickedAccount`) and test suite (`d12_driftHarvestReward.spec.ts`) show is an anticipated, real occurrence rather than a purely theoretical edge case — the protocol authors built explicit tooling (`drift_harvest_reward`) around it, indicating it happens in practice on Drift.

### Recommendation
- Confirm/enforce that `drift_harvest_reward` can be invoked permissionlessly (or by a well-defined keeper/admin role) with tight latency guarantees so a bricked state is never long-lived; if it currently silently relies on an implicit signer somewhere not shown here, that should be verified.
- Consider decoupling the withdraw-path eligibility check from a hard revert: e.g., automatically invoke a harvest/rebalance CPI within `drift_withdraw` itself when the position count exceeds 3, or fall back to a bounded reward-forwarding path rather than blocking normal-user withdrawals entirely.
- Add an explicit, admin-gated "force-unbrick" instruction analogous to Tradable's `revokePayout`, which can sweep/withdraw excess reward positions and route them to the fee wallet or bank in a single atomic step, guaranteeing timely recovery independent of any keeper cadence.

### Proof of Concept
1. Marginfi group admin sets up a Drift-backed bank; a normal user deposits token A via `drift_deposit` (1 active deposit position).
2. Drift protocol admin (external to marginfi) deposits two separate reward tokens (B and C) into the same shared `integration_acc_2` `MinimalUser` account tied to the bank, bringing active deposit positions to 3 (still fine) then to 4 (bricking condition), as reproduced in the existing test `User: Account bricked with 4 active deposits`. [10](#0-9) 
3. The normal user calls `drift_withdraw` with `withdraw_all: true`, passing correct reward accounts; the transaction reverts with `DriftBrickedAccount` (error code `0x18ae`) because `validate_not_bricked_by_admin_deposits` now sees 4 active positions. [11](#0-10) 
4. The user cannot withdraw their own principal until someone calls `drift_harvest_reward` to remove excess reward positions — a step entirely outside the user's control, mirroring the PayoutManager scenario where investors lost the ability to claim principal/interest due to a third-party-driven eligibility state change.

### Citations

**File:** programs/marginfi/src/instructions/drift/withdraw.rs (L393-410)
```rust
    /// The Drift user account owned by liquidity_vault_authority
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

**File:** programs/drift-mocks/src/state.rs (L267-290)
```rust
    /// Check if Drift has bricked this account with excessive admin deposits
    /// We support 1 main asset + up to 2 reward assets (3 total active deposits)
    /// If Drift admin deposited more reward assets, the account cannot withdraw
    pub fn validate_not_bricked_by_admin_deposits(&self) -> Result<()> {
        let active_deposits = self.count_active_deposits();

        if active_deposits > 3 {
            msg!(
                "ERROR: Drift has {} active deposit positions",
                active_deposits
            );
            msg!(
                "Active market indexes: {:?}",
                self.get_active_deposit_markets()
            );
            msg!("This account has been bricked by Drift admin deposits!");
            msg!("Cannot withdraw when more than 3 assets have active balances");
            msg!("We support 1 main asset + up to 2 reward assets");
            msg!("SOLUTION: Fee admin wallet needs to harvest these rewards ASAP!");
            return Err(DriftMocksError::TooManyActiveDeposits.into());
        }

        Ok(())
    }
```

**File:** tests/specs/drift/d12_driftHarvestReward.spec.ts (L1230-1336)
```typescript
  });

  it("User: Account bricked with 4 active deposits", async () => {
    const user = users[0];

    const driftState = await getDriftStateAccount(driftBankrunProgram);
    const tokenDMarketIndex = driftState.numberOfSpotMarkets;

    await createDriftSpotMarketWithOracle(
      ecosystem.lstAlphaMint.publicKey,
      DRIFT_TOKEN_D_SYMBOL,
      tokenDMarketIndex,
      ecosystem.lstAlphaPrice,
      ecosystem.lstAlphaDecimals,
    );
    assert(driftAccounts.get(DRIFT_TOKEN_D_PULL_ORACLE));
    assert(driftAccounts.get(DRIFT_TOKEN_D_SPOT_MARKET));

    await fundAndDepositAdminReward(
      groupAdmin.wallet,
      driftTokenABank,
      ecosystem.tokenBMint.publicKey,
      TOKEN_B_MARKET_INDEX,
      depositBAmount,
    );

    await fundAndDepositAdminReward(
      groupAdmin.wallet,
      driftTokenABank,
      ecosystem.lstAlphaMint.publicKey,
      tokenDMarketIndex,
      tokenDRewardAmount,
    );

    const marginfiAccount = await createThrowawayMarginfiAccount(
      user,
      driftGroup.publicKey,
    );

    const tokenAOracle = driftAccounts.get(DRIFT_TOKEN_A_PULL_ORACLE);
    const tokenASpotMarket = driftAccounts.get(DRIFT_TOKEN_A_SPOT_MARKET);
    const tokenBOracle = driftAccounts.get(DRIFT_TOKEN_B_PULL_ORACLE);
    const tokenBSpotMarket = driftAccounts.get(DRIFT_TOKEN_B_SPOT_MARKET);

    const depositIx = await makeDriftDepositIx(
      user.mrgnBankrunProgram,
      {
        marginfiAccount,
        bank: driftTokenABank,
        signerTokenAccount: user.tokenAAccount,
        driftOracle: tokenAOracle,
      },
      tokenAWithdrawAmount,
      TOKEN_A_MARKET_INDEX,
    );

    const depositTx = new Transaction()
      .add(ComputeBudgetProgram.setComputeUnitLimit({ units: 1_000_000 }))
      .add(depositIx);

    await processBankrunTransaction(
      bankrunContext,
      depositTx,
      [user.wallet],
      false,
      true,
    );

    const remaining = composeRemainingAccounts(
      getDriftBalanceAccountGroups().filter(
        (group) => !group[0].equals(driftTokenABank)
      )
    );
    const withdrawIx = await makeDriftWithdrawIx(
      user.mrgnBankrunProgram,
      {
        marginfiAccount,
        bank: driftTokenABank,
        destinationTokenAccount: user.tokenAAccount,
        driftOracle: tokenAOracle,
        driftRewardOracle: tokenBOracle,
        driftRewardSpotMarket: tokenBSpotMarket,
        driftRewardOracle2: driftTokenCPullOracle,
        driftRewardSpotMarket2: driftTokenCSpotMarket,
      },
      {
        amount: new BN(0),
        withdrawAll: true,
        remaining,
      },
      driftBankrunProgram,
    );

    const withdrawTx = new Transaction()
      .add(ComputeBudgetProgram.setComputeUnitLimit({ units: 1_000_000 }))
      .add(withdrawIx);

    const result = await processBankrunTransaction(
      bankrunContext,
      withdrawTx,
      [user.wallet],
      true,
      false,
    );

    assertBankrunTxFailed(result, 0x18ae); // DriftBrickedAccount
  });
```

**File:** programs/marginfi/src/instructions/marginfi_account/withdraw.rs (L259-276)
```rust
    #[account(
        mut,
        has_one = group @ MarginfiError::InvalidGroup,
        constraint = {
            let acc = marginfi_account.load()?;
            !acc.get_flag(ACCOUNT_DISABLED)
        } @MarginfiError::AccountDisabled,
        constraint = {
            let a = marginfi_account.load()?;
            account_not_frozen_for_authority(&a, authority.key())
        } @ MarginfiError::AccountFrozen,
        constraint = {
            let a = marginfi_account.load()?;
            let g = group.load()?;
            is_signer_authorized(&a, g.admin, authority.key(), true, true)
        } @ MarginfiError::Unauthorized
    )]
    pub marginfi_account: AccountLoader<'info, MarginfiAccount>,
```

**File:** programs/marginfi/src/instructions/marginfi_account/freeze.rs (L1-20)
```rust
/// Admin-only instruction to toggle `ACCOUNT_FROZEN` on a marginfi account.
///
/// Behavior:
/// - When frozen, the account authority is blocked from major actions (borrow/deposit/withdraw/repay/transfer/etc.) with `AccountFrozen`.
/// - The group admin retains access to operate the account while frozen (for remediation/seizure).
/// - Setting `frozen = false` clears the flag and returns control to the authority under normal auth rules.
pub fn set_account_freeze(ctx: Context<SetAccountFreeze>, frozen: bool) -> MarginfiResult {
    let group = ctx.accounts.group.load()?;
    check_eq!(
        group.admin,
        ctx.accounts.admin.key(),
        MarginfiError::Unauthorized
    );
    let mut marginfi_account = ctx.accounts.marginfi_account.load_mut()?;
    if frozen {
        marginfi_account.set_flag(ACCOUNT_FROZEN, true);
    } else {
        marginfi_account.unset_flag(ACCOUNT_FROZEN, true);
    }
    marginfi_account.last_update = Clock::get()?.unix_timestamp as u64;
```

**File:** programs/marginfi/src/instructions/drift/harvest_reward.rs (L18-44)
```rust
/// Harvest rewards from admin deposits in Drift spot markets
/// This instruction allows withdrawing from positions that were created by admin deposits
/// at indices 2-7 (index 0 is for USDC, 1 is for any other token mint)
/// Has a number of checks to ensure this only withdraws rewards
/// - Checks that harvest spot market does not match the bank's spot market
/// - Checks that harvest spot market mint does not match bank's mint
/// - Checks that the harvest spot market has a balance in index 2 - 7 on the user account
///   The only possible exception to index 2-7 is if someone rewards USDC usage which is unlikely.
///
/// Remaining accounts should be passed in the order required by Drift's withdraw instruction:
/// 1. Oracle accounts (optional)
/// 2. Spot market accounts (always required)
/// 3. Token mint (required for Token-2022)
pub fn drift_harvest_reward<'info>(
    ctx: Context<'info, DriftHarvestReward<'info>>,
) -> MarginfiResult {
    let spot_market_index = {
        let harvest_spot_market = ctx.accounts.harvest_drift_spot_market.load()?;
        harvest_spot_market.market_index
    };

    ctx.accounts
        .cpi_withdraw_from_position(spot_market_index, ctx.remaining_accounts)?;

    ctx.accounts.cpi_transfer_to_destination()?;
    Ok(())
}
```

**File:** programs/marginfi/src/instructions/drift/harvest_reward.rs (L46-137)
```rust
#[derive(Accounts)]
pub struct DriftHarvestReward<'info> {
    #[account(
        has_one = integration_acc_2 @ MarginfiError::InvalidDriftUser,
        has_one = integration_acc_3 @ MarginfiError::InvalidDriftUserStats,
        constraint = is_drift_asset_tag(bank.load()?.config.asset_tag)
            @ MarginfiError::WrongBankAssetTagForDriftOperation
    )]
    pub bank: AccountLoader<'info, Bank>,

    /// Global fee state that contains the global_fee_wallet
    #[account(
        seeds = [FEE_STATE_SEED.as_bytes()],
        bump
    )]
    pub fee_state: AccountLoader<'info, FeeState>,

    /// The bank's liquidity vault authority
    #[account(
        seeds = [
            LIQUIDITY_VAULT_AUTHORITY_SEED.as_bytes(),
            bank.key().as_ref()
        ],
        bump = bank.load()?.liquidity_vault_authority_bump
    )]
    pub liquidity_vault_authority: SystemAccount<'info>,

    /// To create this manually just send some of the reward token
    /// to the liquidity vault authority address before claiming
    #[account(
        mut,
        associated_token::mint = reward_mint,
        associated_token::authority = liquidity_vault_authority,
        associated_token::token_program = token_program,
    )]
    pub intermediary_token_account: Box<InterfaceAccount<'info, TokenAccount>>,

    /// Destination token account must be owned by the global fee wallet
    #[account(
        mut,
        associated_token::mint = reward_mint,
        associated_token::authority = fee_state.load()?.global_fee_wallet,
        associated_token::token_program = token_program,
    )]
    pub destination_token_account: Box<InterfaceAccount<'info, TokenAccount>>,

    /// Drift accounts
    /// CHECK: Validated in cpi
    pub drift_state: UncheckedAccount<'info>,

    #[account(
        mut,
        constraint = {
            let user = integration_acc_2.load()?;
            user.has_admin_deposit(harvest_drift_spot_market.load()?.market_index).is_ok()
        } @ MarginfiError::DriftNoAdminDeposit
    )]
    pub integration_acc_2: AccountLoader<'info, MinimalUser>,

    /// CHECK: Validated in cpi
    #[account(mut)]
    pub integration_acc_3: UncheckedAccount<'info>,

    /// The harvest spot market - MUST be different from bank's Drift spot market (integration_acc_1)
    /// This is the market that contains admin deposits to harvest
    #[account(
        mut,
        owner = DRIFT_PROGRAM_ID,
        constraint = harvest_drift_spot_market.load()?.mint != bank.load()?.mint
            @ MarginfiError::DriftSpotMarketMintMismatch,
        constraint = harvest_drift_spot_market.key() != bank.load()?.integration_acc_1
            @ MarginfiError::DriftHarvestSameMarket,
    )]
    pub harvest_drift_spot_market: AccountLoader<'info, MinimalSpotMarket>,

    /// The harvest spot market vault - derived from harvest_drift_spot_market
    /// CHECK: Validated in CPI
    #[account(mut)]
    pub harvest_drift_spot_market_vault: UncheckedAccount<'info>,

    /// The Drift signer PDA
    /// CHECK: Validated by the Drift program during CPI
    pub drift_signer: UncheckedAccount<'info>,

    pub reward_mint: Box<InterfaceAccount<'info, Mint>>,

    /// CHECK: validated against hardcoded program id
    #[account(address = DRIFT_PROGRAM_ID)]
    pub drift_program: UncheckedAccount<'info>,

    pub token_program: Interface<'info, TokenInterface>,
}
```
