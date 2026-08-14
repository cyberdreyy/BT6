### Title
Permissionless `lending_pool_emissions_deposit` allows early-share-holder to inflate `asset_share_value` and price out subsequent depositors - (File: `programs/marginfi/src/instructions/marginfi_group/configure_bank.rs`)

### Summary
The Hifi report describes a first-liquidity-provider attack: mint a tiny amount of shares, then directly inject a large amount of underlying token into the pool so that the share price becomes so large that later depositors are effectively priced out (their deposit rounds down to zero shares, or they must supply an enormous amount to receive even one share). Marginfi has a directly analogous, permissionless instruction, `lending_pool_emissions_deposit`, that lets *any signer* inject arbitrary amounts of the bank's mint straight into the bank's `liquidity_vault` and recompute `asset_share_value = (total_assets + amount) / total_asset_shares`, with the only safety gate being `total_asset_shares > 0`.

### Finding Description
`lending_pool_emissions_deposit` is declared permissionless and requires only a `Signer` `depositor` — no admin/group check is enforced on the `LendingPoolEmissionsDeposit` accounts struct: [1](#0-0) 

The only guard preventing this from being called on an untouched bank is `total_asset_shares > I80F48::ZERO`: [2](#0-1) 

The share value is then recomputed directly from the injected amount divided by the (still tiny) existing share supply, exactly mirroring the Hifi/Uniswap-V2-style inflation mechanic (`new_price = (old_assets + injected) / total_shares`): [3](#0-2) 

This is confirmed permissionless by the instruction doc comments and changelog entry describing it as "(permissionless) — deposit same-bank emissions directly into the liquidity vault, raising `asset_share_value`": [4](#0-3) [5](#0-4) 

Exploit path:
1. Attacker is the first depositor to a freshly created bank and deposits the smallest allowed non-zero amount via `lending_account_deposit`, receiving a minimal number of `total_asset_shares` (share/asset relationship is 1:1 at bank genesis since `asset_share_value` starts at 1): [6](#0-5) 
2. Attacker (any signer, no special role needed) then calls `lending_pool_emissions_deposit` with a very large `amount` of the bank's mint, which is transferred straight into `liquidity_vault` and divided by the tiny `total_asset_shares`, producing an enormous `asset_share_value`: [7](#0-6) 
3. A subsequent depositor calling `lending_account_deposit` with a normal-sized amount will get `share_amount = value / asset_share_value`, where `get_asset_shares` performs a straightforward division that floors toward zero for I80F48 fixed-point values smaller than one share unit — depositing a modest amount yields (near) zero shares while their tokens are still transferred into the vault, effectively donating them to the attacker's inflated position: [8](#0-7) 

Note that only same-mint emissions/rewards funding by legitimate emissions programs was presumably the intended use case, but the instruction itself performs no check that the caller is an authorized emissions distributor, nor any check that the resulting price movement is bounded, nor any minimum-liquidity lock (unlike Uniswap V2's `MINIMUM_LIQUIDITY` burn) to raise the cost of this attack.

### Impact Explanation
This directly reproduces the "smaller liquidity providers priced out" bug class in a production, unprivileged, permissionless instruction rather than merely by direct token transfer (which in most share-accounting designs — including marginfi's own bookkeeping-based `asset_share_value`, which is *not* auto-derived from raw vault balance — would normally have no effect). Here, the protocol itself exposes a dedicated call path (`lending_pool_emissions_deposit`) that lets anyone force the share-value recompute using an attacker-chosen large `amount`, so the attack requires no reliance on an unexpected vault balance/shares divergence — it is a first-class instruction. Impact: newly initialized banks (which anyone can create/permissionlessly add per `lending_pool_add_bank_permissionless`) are vulnerable to being permanently or durably rendered unusable for smaller depositors, and value can be redirected to the attacker's inflated share position at the expense of subsequent depositors whose principal is transferred into the vault but yields negligible/zero shares.

### Likelihood Explanation
Likelihood is high for newly created banks: the attacker needs only to be the first depositor (trivial, since bank creation/first deposit is permissionless) and then call a public, no-special-permission instruction with a large `amount` of the same mint. No admin cooperation, no timing race beyond being first, and no privileged role is required.

### Proof of Concept
1. Permissionlessly create a new bank for mint `M` (`lending_pool_add_bank_permissionless`).
2. Attacker deposits `1` unit of `M` via `lending_account_deposit`, receiving `total_asset_shares ≈ 1` at `asset_share_value = 1`.
3. Attacker calls `lending_pool_emissions_deposit(amount = 10^21)` with `mint = M`, funding account/depositor = attacker. Since `total_asset_shares > 0` passes, the transfer succeeds and:
   `asset_share_value = (1 + 10^21) / 1 ≈ 10^21`
   (see calculation at `programs/marginfi/src/instructions/marginfi_group/configure_bank.rs:138-146`).
4. A subsequent legitimate depositor tries to deposit, e.g., `10^18` units of `M`. `get_asset_shares` computes `value / asset_share_value = 10^18 / 10^21 ≈ 0`, so they receive (rounded) 0 shares for their deposit while their tokens are transferred into the shared vault (see `programs/marginfi/src/state/bank.rs:249-256` and the deposit flow in `programs/marginfi/src/instructions/marginfi_account/deposit.rs:92-124`), effectively donating value to the attacker's outsized share.

### Citations

**File:** programs/marginfi/src/instructions/marginfi_group/configure_bank.rs (L84-115)
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
```

**File:** programs/marginfi/src/instructions/marginfi_group/configure_bank.rs (L124-146)
```rust
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

**File:** patch-note-drafts/patch-notes-0.1.9.md (L151-155)
```markdown
### Emissions

- `lending_pool_emissions_deposit(amount)` (permissionless) — deposit same-bank emissions directly
  into the liquidity vault, raising `asset_share_value`.

```

**File:** programs/marginfi/src/instructions/marginfi_account/deposit.rs (L86-92)
```rust
    let mut bank_account = BankAccountWrapper::find_or_create(
        &bank_loader.key(),
        &mut bank,
        &mut marginfi_account.lending_account,
    )?;

    let share_amount = bank_account.deposit(I80F48::from_num(deposit_amount))?;
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
