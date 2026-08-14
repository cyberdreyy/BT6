Found it. `lending_pool_emissions_deposit` (permissionless, callable by any signer) recomputes `bank.asset_share_value` as `(total_assets + amount) / total_asset_shares`, guarded only by `total_asset_shares > I80F48::ZERO` — not by a meaningful minimum threshold. This is the closest analog to the Compound first-depositor donation bug: an attacker who is the sole/first depositor of a bank (with `total_asset_shares` reduced to a tiny non-zero dust amount, e.g. `1` raw share unit) can call this permissionless instruction to inflate `asset_share_value` arbitrarily using their own funds, then have a victim deposit and receive disproportionately few shares, stealing the victim's deposit share of the pool — mirroring the CToken `mintFresh`/exchange-rate-inflation exploit.### Title
First-depositor exchange-rate manipulation via permissionless `lending_pool_emissions_deposit` - (File: `programs/marginfi/src/instructions/marginfi_group/configure_bank.rs`)

### Summary
`lending_pool_emissions_deposit` is a permissionless instruction that lets **any signer** donate tokens directly into a bank's liquidity vault and have the program recompute `bank.asset_share_value` from the new vault-derived total divided by `total_asset_shares`. The only guard against a zero divisor is `total_asset_shares > I80F48::ZERO` — an exact-zero check, not a meaningful minimum-liquidity/dust threshold (unlike the `ZERO_AMOUNT_THRESHOLD` guard used elsewhere, e.g. in `super_admin_deposit`/`super_admin_withdraw`). This reproduces the classic Compound/CToken "first depositor" exchange-rate-inflation primitive described in the report: an attacker who controls (nearly) all of a bank's `total_asset_shares` can inflate `asset_share_value` by an arbitrary factor relative to their own tiny share count, then have a victim deposit into the same bank and receive a disproportionately small number of shares for their contributed tokens.

### Finding Description
For standard SPL banks, `asset_share_value` is *not* an independent invariant maintained purely by internal share accounting — `lending_pool_emissions_deposit` explicitly re-derives it from the vault balance: [1](#0-0) 

```
let total_asset_shares = I80F48::from(bank.total_asset_shares);
check!(
    total_asset_shares > I80F48::ZERO,
    MarginfiError::EmissionsUpdateError
);
...
let total_assets = bank.get_asset_amount(total_asset_shares)?;
let updated_total_assets = total_assets.checked_add(I80F48::from_num(amount))...;
bank.asset_share_value = updated_total_assets
    .checked_div(total_asset_shares)...;
```

This is called by any `depositor: Signer<'info>` — no admin `has_one` constraint is present on the instruction's accounts other than the bank/group/mint linkage, and the doc comment itself states: "Permissionlessly deposit same-mint emissions directly into the bank liquidity vault, increasing depositor value through asset share value."

Because the divisor check only rejects an *exact* zero (`total_asset_shares > I80F48::ZERO`), an attacker can:
1. Be the sole depositor of a freshly created bank (or reduce their own position via `withdraw` until only dust, e.g. `1` raw share unit, remains as `total_asset_shares`, keeping it non-zero).
2. Call `lending_pool_emissions_deposit` with a large `amount` of the bank's own mint (same-mint emissions are required: `has_one = mint` and `is_marginfi_asset_tag` checks apply, but there is no minimum-share-count or minimum-value guard).
3. This inflates `asset_share_value` to `(dust_worth_of_assets + amount) / dust_shares` — an arbitrary, attacker-controlled multiplier.
4. When a victim subsequently deposits `X` tokens via `lending_account_deposit`, their minted shares are computed as `X / asset_share_value` (see `get_asset_shares`), which floors to a vanishingly small (or zero) share count due to the inflated `asset_share_value`.
5. The attacker's dust shares, now valued at `dust_shares * asset_share_value`, entitle the attacker to withdraw a share of the pool that includes the victim's newly deposited tokens. [2](#0-1) 

This mirrors exactly the CToken bug described in the report: attacker manipulates a low-total-supply exchange rate via a direct token injection, then a subsequent depositor is shortchanged. The key difference from a standard SPL bank's normal deposit flow (which computes shares purely from existing `asset_share_value`, unaffected by raw vault balance) is that `lending_pool_emissions_deposit` is the one code path that *re-derives* `asset_share_value` from vault balance ÷ `total_asset_shares`, reintroducing the low-total-supply divisor vulnerability that the rest of the share-accounting design otherwise avoids.

By contrast, the privileged/staging-only `super_admin_deposit` and `super_admin_withdraw` instructions that perform the same balance-based recomputation are gated behind `check!(total_asset_shares > ZERO_AMOUNT_THRESHOLD, ...)` — a meaningful dust floor — and are additionally restricted to `STAGING_ID`/`LOCALNET_ID` and admin signers. `lending_pool_emissions_deposit` has neither the stronger threshold nor an admin restriction.

### Impact Explanation
This directly enables value redirection / theft of deposited funds on any bank where `total_asset_shares` can be driven down to a small non-zero amount (trivially true for a newly created bank before others deposit, or any bank an attacker can reduce their own share count in via `withdraw`). Victims lose part or all of their deposit's value to the attacker upon their first deposit into the manipulated bank. This has a direct financial-loss effect on protocol users and corrupts the bank's core accounting invariant (`asset_share_value`), which is used everywhere in health/liquidation/withdrawal math.

### Likelihood Explanation
The attack is unprivileged and requires only:
- Being a bank's dominant/sole depositor (straightforward immediately after bank creation, since `lending_pool_add_bank*` are admin-only but any user can be the first regular depositor once the bank exists — a race that's easy to win/front-run), and
- The ability to call `lending_pool_emissions_deposit` with a large `amount` of the bank mint (only constrained by holding/acquiring enough of the underlying token, and passing the non-fee/non-hook mint checks).

No governance, validator, or privileged role is required, matching the report's front-running first-deposit attack pattern.

### Recommendation
Replace the `total_asset_shares > I80F48::ZERO` check in `lending_pool_emissions_deposit` with the same meaningful minimum threshold used elsewhere (`ZERO_AMOUNT_THRESHOLD`), or better, enforce a minimum absolute `total_asset_shares`/`total_assets` value (analogous to Uniswap V2's/Compound-fix's minimum-liquidity lock) before permitting any vault-balance-derived recomputation of `asset_share_value`. Consider also requiring `lending_pool_emissions_deposit` to be usable only once a bank has a healthy, diversified deposit base (e.g., minimum total value or minimum unique depositor count), or restricting it to bank admin/emissions-admin rather than an arbitrary signer.

### Proof of Concept
1. Admin creates a new bank via `lending_pool_add_bank` (standard SPL bank), `total_asset_shares = 0`.
2. Attacker deposits a minimal amount (e.g., `1` unit) via `lending_account_deposit`, becoming the sole depositor: `total_asset_shares = 1`, `asset_share_value = 1.0`.
3. Attacker calls `lending_pool_emissions_deposit(amount = LARGE_N)` with `mint` == bank mint, funding from their own token account. Per `configure_bank.rs:111-146`, this passes `total_asset_shares (1) > 0` and sets:
   `asset_share_value = (1 * 1.0 + LARGE_N) / 1 = 1 + LARGE_N`
4. Victim calls `lending_account_deposit(amount = VICTIM_DEPOSIT)`. Minted shares = `VICTIM_DEPOSIT / asset_share_value` ≈ `0` (floors to near-zero) since `asset_share_value` is huge, per `bank.get_asset_shares` in `bank.rs:249-256`.
5. Attacker's `1` share is now worth `1 * asset_share_value` ≈ `LARGE_N`, which includes the victim's freshly deposited tokens in the vault. Attacker withdraws, capturing the victim's deposit. [3](#0-2) [4](#0-3)

### Citations

**File:** programs/marginfi/src/instructions/marginfi_group/configure_bank.rs (L86-156)
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

    bank.update_bank_cache(&group)?;

    msg!(
        "Deposited {} same-bank emissions into liquidity vault",
        amount
    );

    Ok(())
}
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

**File:** programs/marginfi/src/instructions/marginfi_group/super_admin_deposit.rs (L66-80)
```rust

    let total_asset_shares: I80F48 = bank.total_asset_shares.into();
    check!(
        total_asset_shares > ZERO_AMOUNT_THRESHOLD,
        MarginfiError::NoAssetFound
    );

    let assets_before = bank.get_asset_amount(total_asset_shares)?;
    let assets_after = assets_before
        .checked_add(I80F48::from_num(amount))
        .ok_or_else(math_error!())?;
    bank.asset_share_value = assets_after
        .checked_div(total_asset_shares)
        .ok_or_else(math_error!())?
        .into();
```
