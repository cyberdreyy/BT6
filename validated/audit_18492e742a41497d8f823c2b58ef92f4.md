## Analysis

The ZEAL bug class is: a permissionless "add value without minting shares" primitive combined with a share-value ratchet that is never reset when total supply returns to zero, letting a self-funded actor who owns 100% of the shares recycle their own capital to permanently inflate the exchange rate and grief future depositors.

marginfi contains a structurally identical primitive in `lending_pool_emissions_deposit`, which is explicitly documented as "Permissionlessly deposit same-mint emissions directly into the bank liquidity vault, increasing depositor value through asset share value" [1](#0-0) . It recomputes `asset_share_value` as `(total_assets + amount) / total_asset_shares` without minting any new shares [2](#0-1) , guarded only by `total_asset_shares > 0` [3](#0-2) . Crucially, `asset_share_value` is bank-level global state that is never reset to its initial value when `total_asset_shares` returns to zero — `withdraw_all` only decrements `total_asset_shares` via `bank.change_asset_shares(-total_asset_shares, false)` [4](#0-3)  and never touches `asset_share_value`. Future share issuance uses `get_asset_shares`, which divides the deposited value by this stale, possibly inflated `asset_share_value` [5](#0-4) .

This lets a sole depositor in a bank (e.g., a freshly created bank, or any bank where they can temporarily be the only depositor) do: deposit → `lending_pool_emissions_deposit` (self-funded, since they own 100% of shares they get 100% of it back) → `withdraw_all` (returns to `total_asset_shares == 0` while `asset_share_value` stays elevated) → repeat, ratcheting `asset_share_value` arbitrarily high at near-zero net cost (only fees + temporary capital lockup), exactly mirroring the ZEAL PoC's stake/reward/unstake loop. Once `asset_share_value` is large enough, `get_asset_shares` for a normal-sized deposit floors to zero (or a negligible amount), effectively DoS'ing new depositors — the same "prevent others from staking" impact described in the report.

I could not fully verify from the indexed code whether `add_bank`/`add_pool` enforce a minimum initial deposit or "dead shares" lock (as in Uniswap V2) that would force at least one honest depositor to always hold some shares and thus block a `total_asset_shares == 0` state during the attack window; this would need to be checked in `programs/marginfi/src/instructions/marginfi_group/add_pool.rs` and related add-pool paths (I only saw grep hits, not full file contents). This affects whether an attacker can practically drive `total_asset_shares` to exactly zero on a shared bank, versus needing to create/control an isolated bank themselves. Given `lending_pool_add_bank_permissionless` and `add_pool` variants suggest anyone can create a new bank, an attacker likely can trivially create a bank they control end-to-end and are guaranteed sole ownership of all shares throughout the loop.

### Title
Permissionless `lending_pool_emissions_deposit` combined with a non-resetting `asset_share_value` allows share-price inflation / griefing DoS - ([File: programs/marginfi/src/instructions/marginfi_group/configure_bank.rs])

### Summary
`lending_pool_emissions_deposit` lets anyone raise a bank's `asset_share_value` by depositing tokens without minting shares. Because `asset_share_value` is never reset when `total_asset_shares` returns to zero, an attacker who is the bank's sole depositor can repeatedly deposit → self-fund emissions → withdraw-all to inflate `asset_share_value` at negligible net cost, permanently ratcheting the exchange rate.

### Finding Description
`lending_pool_emissions_deposit` only requires `total_asset_shares > 0` and recomputes `asset_share_value = (current_total_assets + amount) / total_asset_shares` [6](#0-5) . No shares are minted for the injected `amount`, so if the caller owns all outstanding shares, the injected value is entirely attributable back to them.

`withdraw_all` burns the caller's shares and reduces `total_asset_shares` to zero without adjusting `asset_share_value` [7](#0-6) . There is no code path that resets `asset_share_value` to `1` (or any baseline) once `total_asset_shares` hits zero.

New deposits mint shares via `get_asset_shares`, which divides deposited value by the current `asset_share_value` [5](#0-4) . If `asset_share_value` has been ratcheted to a very large number, ordinary-sized deposits floor to zero shares.

This is the same root cause pattern as the ZEAL report: a monotonically-increasing (never-reset) exchange-rate floor combined with a free/self-funded value-injection mechanism, exploitable via deposit→inject→withdraw-all cycles when the actor holds 100% of the shares.

### Impact Explanation
An attacker can create a new bank (or become the sole depositor of an existing thin bank) and, at the cost of gas plus temporarily locking their own capital, permanently inflate `asset_share_value` to a point where legitimate depositors receive zero or dust shares for real deposits. This is a durable griefing/DoS on that bank's deposit function — new depositors effectively cannot participate, and any already-cached health/valuation logic depending on `asset_share_value` is corrupted for that bank going forward. This does not directly steal other users' existing funds (the attacker only recycles their own capital while they are the sole owner), but it permanently disables normal use of the affected bank, a durable freeze/inconsistency with financial effect.

### Likelihood Explanation
Likelihood is moderate: it requires the attacker to be (or become) the bank's sole shareholder at the moment of each `emissions_deposit` → `withdraw_all` cycle, which is straightforward for a newly created/permissionless bank but harder on an established, actively-used bank with existing depositors. `lending_pool_emissions_deposit` is explicitly permissionless (any depositor can pay) [1](#0-0) , and bank creation itself appears permissionless in this codebase (`add_pool_permissionless.rs`), making it easy to set up a fresh, attacker-controlled bank to run the loop.

### Recommendation
Reset `asset_share_value` (and `liability_share_value` if analogous) to its default/baseline value whenever `total_asset_shares` returns to zero, mirroring the ZEAL fix. Additionally, consider requiring `lending_pool_emissions_deposit` to be usable only when there are multiple/independent depositors (e.g., a minimum "dead shares" floor that can never be fully withdrawn), and/or bound the maximum single-call increase to `asset_share_value` relative to existing TVL to blunt self-funded ratcheting.

### Proof of Concept
1. Attacker creates (or becomes sole depositor of) bank B; `asset_share_value = 1`, `total_asset_shares = 0`.
2. Attacker deposits `X` tokens → receives `X` shares; `total_asset_shares = X`.
3. Attacker calls `lending_pool_emissions_deposit(amount = X)` from their own token account (self-funded) [8](#0-7) : `asset_share_value` becomes `(X + X) / X = 2`.
4. Attacker calls `withdraw_all` [7](#0-6) : receives back `X * 2 = 2X` (their own capital plus their own "emissions"), `total_asset_shares` returns to `0`, but `asset_share_value` stays at `2`.
5. Repeat steps 2–4 with the returned capital: each cycle doubles `asset_share_value` (`2 → 4 → 8 → ...`) at only the cost of transaction fees.
6. After enough iterations, `asset_share_value` is astronomically large. A legitimate user depositing a normal amount `Z` gets `get_asset_shares(Z) = Z / asset_share_value ≈ 0`, i.e., they receive no shares for their deposit — a permanent DoS on bank B.

### Citations

**File:** programs/marginfi/src/instructions/marginfi_group/configure_bank.rs (L84-86)
```rust
/// Permissionlessly deposit same-mint emissions directly into the bank liquidity vault,
/// increasing depositor value through asset share value.
pub fn lending_pool_emissions_deposit(
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

**File:** programs/marginfi/src/state/marginfi_account.rs (L1627-1678)
```rust
    pub fn withdraw_all(&mut self, in_receivership: bool) -> MarginfiResult<(u64, I80F48)> {
        let balance = &mut self.balance;
        let bank = &mut self.bank;

        let total_asset_shares: I80F48 = balance.asset_shares.into();
        let current_asset_amount = bank.get_asset_amount(total_asset_shares)?;
        let current_liability_amount =
            bank.get_liability_amount(balance.liability_shares.into())?;

        debug!("Withdrawing all: {}", current_asset_amount);

        check!(
            current_asset_amount.is_positive_with_tolerance(ZERO_AMOUNT_THRESHOLD),
            MarginfiError::NoAssetFound
        );

        check!(
            current_liability_amount.is_zero_with_tolerance(ZERO_AMOUNT_THRESHOLD),
            MarginfiError::NoAssetFound
        );

        balance.close()?;

        // Only clear the lock when this account is actually in receivership.
        // The lock is bank-level global state, so clearing it unconditionally
        // would affect unrelated accounts sharing the same bank.
        if in_receivership {
            bank.cache.clear_liquidation_price_cache_locked();
        }

        bank.decrement_lending_position_count();
        bank.change_asset_shares(-total_asset_shares, false)?;
        bank.check_utilization_ratio()?;

        let spl_withdraw_amount = current_asset_amount
            .checked_floor()
            .ok_or_else(math_error!())?;

        bank.collected_insurance_fees_outstanding = {
            current_asset_amount
                .checked_sub(spl_withdraw_amount)
                .ok_or_else(math_error!())?
                .checked_add(bank.collected_insurance_fees_outstanding.into())
                .ok_or_else(math_error!())?
                .into()
        };

        let spl_withdraw_amount = spl_withdraw_amount
            .checked_to_num()
            .ok_or_else(math_error!())?;

        Ok((spl_withdraw_amount, total_asset_shares))
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
