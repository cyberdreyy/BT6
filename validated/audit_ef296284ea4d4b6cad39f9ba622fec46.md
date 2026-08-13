## Title
Permissionless `lending_pool_emissions_deposit` lets a same-block depositor steal pro-rata reward value from existing depositors before it is distributed - (File: `programs/marginfi/src/instructions/marginfi_group/configure_bank.rs`)

### Summary
The reported Autonomint bug class is: a reward pool value (`usdaToAbondRatioLiq`) is distributed pro-rata to the *current* token supply at the moment of computation, with no accounting for which holders were actually present when the reward was earned. This lets a user mint the reward-bearing token right before the value is realized, steal a slice of it, and immediately exit — even via flashloan.

Marginfi has a structurally identical mechanism: `lending_pool_emissions_deposit` bumps `bank.asset_share_value` by dividing the newly-inflated `total_assets` by `total_asset_shares` **at the instant the instruction runs**, with no snapshot of who held shares before the deposit landed [1](#0-0) . The instruction is explicitly permissionless [2](#0-1)  and is also documented as such in the changelog: `lending_pool_emissions_deposit(amount) (permissionless) — deposit same-bank emissions directly into the liquidity vault, raising asset_share_value` [3](#0-2) .

### Finding Description
`asset_share_value` is the canonical share-price mechanism marginfi uses for deposits: shares are minted/burned at the current `asset_share_value`, and any value added to `total_assets` while `total_asset_shares` stays fixed raises the share price for whoever holds shares at that moment [4](#0-3) . This is fine for organic interest accrual (which happens gradually over time and is unpredictable), but `lending_pool_emissions_deposit` allows *anyone* to inject an arbitrary, discrete lump-sum of value into the vault in a single instruction, instantly and predictably repricing `asset_share_value` for the current share supply — this is functionally identical to Autonomint's `usdaToAbondRatioLiq`, which repriced the abond/USDa exchange rate for whoever held abond at redemption time regardless of whether they contributed to the liquidation event that produced the yield.

There is no lockup, vesting, or minimum holding period between deposit and withdrawal in the bank [5](#0-4) , and deposit/withdraw are not restricted from occurring in the same transaction as `lending_pool_emissions_deposit` (which is a separate, standalone, permissionless instruction rather than part of the flashloan-gated flow). An attacker can therefore:
1. Deposit a large amount into the target bank (mints shares at the pre-emissions `asset_share_value`).
2. In the same transaction (or immediately after), call `lending_pool_emissions_deposit` themselves (or simply front-run someone else's call to it) to raise `asset_share_value` for the whole share pool.
3. Withdraw immediately, realizing a share of the injected emissions proportional to their newly-minted shares — value that legitimate long-term depositors who bore the bank's risk did not "earn" any differently than the attacker, but which the attacker captured for zero holding-period risk.

This directly mirrors the H-22 root cause: "the function does not take into account previous [holders] when calculating the amount ... gained... other users can mint [shares] to steal yield."

### Impact Explanation
Existing bank depositors have their share of injected emissions value diluted by an opportunistic, capital-only (no real economic exposure/time) depositor who deposits and withdraws around the `lending_pool_emissions_deposit` call. Since the instruction is permissionless and can be called by the same actor performing the deposit/withdraw sandwich, the attacker can guarantee the timing rather than relying on someone else's transaction, making this deterministically exploitable rather than merely opportunistic. This causes a direct redirection of value away from legitimate long-duration depositors and toward an attacker with no holding-period risk, satisfying "exploitable misvaluation" / "value redirection" criteria.

### Likelihood Explanation
High: the instruction is permissionless, requires only a normal deposit/withdraw plus one emissions-deposit call (all supported ordinary user-facing instructions), needs no privileged role, and the attacker can self-trigger the "reward" event to control timing exactly, rather than depending on external actors' unpredictable calls.

### Recommendation
Either (a) restrict `lending_pool_emissions_deposit` to a trusted/permissioned party (e.g. emissions admin) so timing cannot be attacker-controlled, and/or (b) distribute injected emissions gradually (e.g. streamed over time via the existing accrual mechanism) rather than as an instantaneous share-price bump, and/or (c) require a minimum holding period / cooldown between deposit and withdrawal for a bank so a single-transaction deposit→reward→withdraw cannot be executed.

### Proof of Concept
No PoC was provided in the source report; conceptually the exploit is: `deposit(bank, X)` → `lending_pool_emissions_deposit(bank, reward)` → `withdraw(bank, all)`, all attacker-initiated and orderable within the attacker's control since `lending_pool_emissions_deposit` has no signer/authority restriction beyond `depositor: Signer` funding the transfer [6](#0-5) .

### Citations

**File:** programs/marginfi/src/instructions/marginfi_group/configure_bank.rs (L84-90)
```rust
/// Permissionlessly deposit same-mint emissions directly into the bank liquidity vault,
/// increasing depositor value through asset share value.
pub fn lending_pool_emissions_deposit(
    ctx: Context<LendingPoolEmissionsDeposit>,
    amount: u64,
) -> MarginfiResult {
    if amount == 0 {
```

**File:** programs/marginfi/src/instructions/marginfi_group/configure_bank.rs (L111-146)
```rust
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

**File:** programs/marginfi/src/instructions/marginfi_group/configure_bank.rs (L158-192)
```rust
#[derive(Accounts)]
pub struct LendingPoolEmissionsDeposit<'info> {
    #[account(
        constraint = (
            !group.load()?.is_protocol_paused()
        ) @ MarginfiError::ProtocolPaused
    )]
    pub group: AccountLoader<'info, MarginfiGroup>,

    #[account(
        mut,
        has_one = group @ MarginfiError::InvalidGroup,
        has_one = mint @ MarginfiError::InvalidEmissionsMint,
        has_one = liquidity_vault @ MarginfiError::InvalidLiquidityVault,
        constraint = is_marginfi_asset_tag(bank.load()?.config.asset_tag)
            @ MarginfiError::WrongAssetTagForStandardInstructions,
    )]
    pub bank: AccountLoader<'info, Bank>,

    pub mint: InterfaceAccount<'info, Mint>,

    /// NOTE: This is a TokenAccount, spl transfer will validate it.
    ///
    /// CHECK: Account provided only for funding rewards
    #[account(mut)]
    pub emissions_funding_account: UncheckedAccount<'info>,

    #[account(mut)]
    pub depositor: Signer<'info>,

    #[account(mut)]
    pub liquidity_vault: Box<InterfaceAccount<'info, TokenAccount>>,

    pub token_program: Interface<'info, TokenInterface>,
}
```

**File:** patch-note-drafts/patch-notes-0.1.9.md (L151-155)
```markdown
### Emissions

- `lending_pool_emissions_deposit(amount)` (permissionless) — deposit same-bank emissions directly
  into the liquidity vault, raising `asset_share_value`.

```

**File:** programs/marginfi/src/state/bank.rs (L237-256)
```rust
    fn get_asset_amount(&self, shares: I80F48) -> MarginfiResult<I80F48> {
        Ok(shares
            .checked_mul(self.asset_share_value.into())
            .ok_or_else(math_error!())?)
    }

    fn get_liability_shares(&self, value: I80F48) -> MarginfiResult<I80F48> {
        Ok(value
            .checked_div(self.liability_share_value.into())
            .ok_or_else(math_error!())?)
    }

    fn get_asset_shares(&self, value: I80F48) -> MarginfiResult<I80F48> {
        if self.asset_share_value == I80F48::ZERO.into() {
            return Ok(I80F48::ZERO);
        }
        Ok(value
            .checked_div(self.asset_share_value.into())
            .ok_or_else(math_error!())?)
    }
```

**File:** programs/marginfi/src/instructions/marginfi_account/flashloan.rs (L1-10)
```rust
// Note: Although flash loans are not explicitly disabled during a protocol pause, they are disabled
// in effect because withdraw/borrow/deposit/repay are all disabled.
use crate::{
    check,
    ix_utils::{
        get_discrim_hash, validate_not_cpi_by_stack_height, validate_not_cpi_with_sysvar, Hashable,
    },
    prelude::*,
    state::marginfi_account::{check_account_init_health, MarginfiAccountImpl},
};
```
