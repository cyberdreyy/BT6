## Title
Permissionless `lending_pool_emissions_deposit` allows a tiny first-depositor to inflate `asset_share_value`, freezing/rounding-to-zero later depositors' shares - (File: `programs/marginfi/src/instructions/marginfi_group/configure_bank.rs`)

### Summary
`lending_pool_emissions_deposit` is a permissionless instruction (any `Signer` can call it) that increases `bank.asset_share_value` by dividing `total_assets + amount` by `total_asset_shares`. Because `total_asset_shares` is attacker-controllable (an attacker can be the very first depositor and lock a trivially small amount), the same root cause as the XDEFI `_pointsPerUnit` bug applies: a tiny denominator combined with a permissionless "inject value" call lets an attacker massively inflate the per-share price early in a bank's life.

### Finding Description
`lending_pool_emissions_deposit` recomputes `asset_share_value` as:
```
total_assets = bank.get_asset_amount(total_asset_shares)
updated_total_assets = total_assets + amount
bank.asset_share_value = updated_total_assets / total_asset_shares
``` [1](#0-0) 

The only guard is `total_asset_shares > 0` [2](#0-1) , and the `depositor` account is a bare `Signer` with no admin/authority check — anyone holding the reward mint can call this [3](#0-2) .

In a freshly created bank, the first depositor mints shares 1:1 [4](#0-3) , so `total_asset_shares` can be as small as `1` (the smallest native unit) if the attacker deposits a dust amount. The attacker can then call `lending_pool_emissions_deposit` with a large `amount` of the same mint, driving `asset_share_value` to an arbitrarily large number since it's divided by the tiny `total_asset_shares`.

Once `asset_share_value` is inflated, subsequent depositors' shares are computed as `value / asset_share_value` (see `get_asset_shares`) [5](#0-4) . If a normal depositor's amount is smaller than the (now huge) `asset_share_value`, this division floors to `0` — the depositor's tokens are pulled into the vault/accounted into `total_assets`, but they receive **zero shares** representing that value. This mirrors the XDEFI class of bug: a manipulable denominator (there, `totalUnits`; here, `total_asset_shares`) combined with a permissionless function that adds real value to the numerator (there, `updateDistribution()`'s `_pointsPerUnit +=`; here, `lending_pool_emissions_deposit`'s `asset_share_value = ...`) lets an early low-cost depositor corrupt the accounting for everyone who deposits afterward.

### Impact Explanation
This is a durable state corruption/value-redirection bug with financial effect: legitimate depositors after the attack lose their deposited value (it inflates the pool but mints them 0 or negligible shares), effectively donating funds to the attacker who holds the sole meaningful share. It can also render the bank effectively unusable for new deposits (any deposit below the inflated share price rounds to 0 shares), which is a form of griefing/freeze on a newly-created bank. The severity is bounded by the fact that inflating the share value costs the attacker real capital (unlike XDEFI's overflow-based DoS which was nearly free), but the attacker recovers that capital because they own all/most of the shares before other depositors arrive.

### Likelihood Explanation
Requires the attacker to be the first depositor of a specific bank (feasible immediately after `lending_pool_add_bank` runs, before organic deposits occur) and to hold/acquire the exact reward mint tokens needed to fund `lending_pool_emissions_deposit`. This is a realistic race condition for any newly listed bank, since bank creation and the emissions-deposit instruction are both public.

### Recommendation
- Require a minimum `total_asset_shares` (or minimum deposit) before `lending_pool_emissions_deposit` can execute, or scale the guard relative to a minimum absolute value rather than just `> 0`.
- Alternatively, restrict `lending_pool_emissions_deposit` to a permissioned/admin-configured emissions authority instead of an open `Signer`.
- Consider enforcing a minimum initial deposit (dead-shares pattern, as recommended in the referenced XDEFI fix) for every newly created bank so `total_asset_shares` can never be a trivially small denominator.

### Proof of Concept
1. Attacker creates/uses a fresh bank and deposits `1` native unit as the first depositor → `total_asset_shares = 1`, `asset_share_value = 1` (per first-deposit 1:1 minting, confirmed in `07_deposit.spec.ts`) [4](#0-3) .
2. Attacker calls `lending_pool_emissions_deposit` (permissionless `Signer`) with a large `amount` X of the bank's mint [6](#0-5) .
3. New `asset_share_value = (1 + X) / 1 = 1 + X`, i.e., massively inflated.
4. A subsequent honest depositor deposits normal amount `Y < 1+X` → `get_asset_shares(Y) = Y / (1+X)` floors to `0` [5](#0-4) , so they receive no shares for their deposited `Y`, which is instead credited entirely to the attacker's single outstanding share.

*(Note: this analog was derived by static analysis of the cited code paths; it was not executed against a live/test environment as part of this review, so exact numeric overflow/precision boundaries in `I80F48` arithmetic were not empirically verified.)*

### Citations

**File:** programs/marginfi/src/instructions/marginfi_group/configure_bank.rs (L86-146)
```rust
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

**File:** tests/specs/basic/07_deposit.spec.ts (L150-153)
```typescript
    assert.equal(balances[0].active, 1);
    // Note: The first deposit issues shares 1:1 and the shares use the same decimals
    assertI80F48Approx(balances[0].assetShares, depositAmountA_native);
    assertI80F48Equal(balances[0].liabilityShares, 0);
```

**File:** programs/marginfi/src/state/bank.rs (L249-256)
```rust
    fn get_asset_shares(&self, value: I80F48) -> MarginfiResult<I80F48> {
        if self.asset_share_value == I80F48::ZERO.into() {
            return Ok(I80F48::ZERO);
        }
        Ok(value
            .checked_div(self.asset_share_value.into())
            .ok_or_else(math_error!())?)
    }
```
