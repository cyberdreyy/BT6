### Title
Boundary condition in liquidation health check allows liquidation of non-unhealthy (break-even) accounts - (File: programs/marginfi/src/state/marginfi_account.rs)

### Summary
The reported bug class is: a threshold/ratio check that should require a *genuinely unhealthy* (bad-debt) account to trigger liquidation instead treats a degenerate boundary value (zero) as "unhealthy", allowing accounts with no real bad debt to be liquidated. In marginfi-v2, the same boundary-condition class exists in `check_pre_liquidation_condition_and_get_account_health`, which gates both the permissionless `start_liquidation` instruction and, indirectly, the standard `liquidate` instruction's overall account-health precondition.

### Finding Description
`check_pre_liquidation_condition_and_get_account_health` computes `account_health = assets - liabs` and only rejects the liquidation attempt if the account is strictly healthy:

```rust
let account_health = assets.checked_sub(liabs).ok_or_else(math_error!())?;
let healthy = account_health > I80F48::ZERO;
...
if healthy && !ignore_healthy {
    return err!(MarginfiError::HealthyAccount);
}
``` [1](#0-0) 

This means an account whose maintenance health is exactly `0` (assets == liabilities, e.g. a break-even account, or a fresh/empty account) is *not* classified as healthy and therefore passes the pre-liquidation gate, even though the project's own documentation defines the liquidation boundary as strictly negative health:

> "Maintenance Health (for liquidation eligibility)... If < 0, the account can be liquidated." [2](#0-1) 

This check is the sole health gate used by the permissionless `start_liquidation` instruction (via `start_receivership`, called with `liability_bank_pk = None`, i.e. with no requirement that the account actually holds an active liability):

```rust
let (_pre_health, assets, liabs) = check_pre_liquidation_condition_and_get_account_health(
    marginfi_account,
    remaining_ais,
    None,
    &mut Some(&mut health_cache),
    HealthPriceMode::Live { liq_cache: Some(&mut liq_price_cache) },
    ignore_healthy,
)?;
``` [3](#0-2) 

`StartLiquidation` is explicitly permissionless with respect to the `marginfi_account` (no owner/authority signature is required on that account) and the `liquidation_receiver` account has "no checks whatsoever, liquidator decides this without restriction": [4](#0-3) 

Once in receivership, the withdraw/repay instructions explicitly drop the normal owner-signature check: "during receivership and order execution, there are no signer checks whatsoever: any key can repay [or withdraw] as long as the invariants checked at the end of execution are met." [5](#0-4) 

At `end_liquidation`, the liquidator/receiver is still permitted to keep a profit bounded by `fee_state.liquidation_max_fee` (plus `LIQUIDATION_BONUS_FEE_MINIMUM`) as long as the post-liquidation health did not worsen relative to the pre-liquidation (zero) health: [6](#0-5) 

Because the pre-liquidation health for a break-even account is exactly `0`, the "health must not get worse" invariant in `end_receivership` (`pre_health > post_health` check) is satisfied trivially, and the liquidator can still legitimately claim the standard liquidation bonus fee even though the account was never actually in bad-debt/unhealthy territory: [7](#0-6) 

By contrast, the standard `liquidate` instruction path additionally requires the specified liability bank to actually hold outstanding liabilities (`NoLiabilitiesInLiabilityBank`) before allowing partial liquidation, which correctly blocks liquidation of a genuinely debt-free account with assets: [8](#0-7) 
No equivalent liability-existence check exists in the `start_liquidation`/`start_receivership` path, so the only defense against liquidating a non-bad-debt account is the strict-inequality boundary, which is implemented as a non-strict one (`> 0` is required to be "healthy", so `== 0` is treated as liquidatable).

### Impact Explanation
Any account whose maintenance-weighted assets exactly equal its maintenance-weighted liabilities (a legitimate, non-bad-debt, break-even state — not merely an empty account) can be forced into `start_liquidation` receivership by an unrelated third party without holding any real bad debt. During the mandatory single-transaction receivership window, that third party (as `liquidation_receiver`) gains signer-check-free withdraw/repay authority over the account and can still legitimately extract the standard liquidation bonus fee/premium at `end_liquidation`, since the "health must not worsen" and "profit ≤ max_fee" invariants are computed relative to a pre-health of `0`, not relative to an actual unhealthy/negative starting point. This constitutes unauthorized value extraction from an account that was never in bad debt, matching the underlying bug class in the source report (a boundary/degenerate value in the liquidation eligibility check causing non-unhealthy accounts to be liquidatable).

### Likelihood Explanation
Reaching an exact `health == 0` tie requires either an account with truly zero assets and liabilities, or precise value matching between weighted assets and weighted liabilities — achievable deliberately by an attacker who fully controls their own account's deposits/borrows to engineer this exact state, then has any party (including themselves) call the permissionless `start_liquidation`/`end_liquidation` pair against that specific account. Because oracle price feeds and fixed-point weighting make achieving an *exact* tie non-trivial in practice (rounding tends to avoid exact zero), likelihood is lower than the original report's trivially-reachable "any freshly funded, debt-free account" case, but it remains a concretely reachable state for a determined attacker targeting their own or a colluding account.

### Recommendation
Change the liquidation-eligibility check to strictly match the documented invariant: reject liquidation attempts (`healthy`) when `account_health >= I80F48::ZERO`, not only when `account_health > I80F48::ZERO`, in `check_pre_liquidation_condition_and_get_account_health`. Additionally, consider requiring `start_liquidation`/`start_receivership` to verify the account holds at least one genuine outstanding liability (mirroring the `NoLiabilitiesInLiabilityBank` check already present in the standard `liquidate` path) before allowing entry into receivership.

### Proof of Concept
1. Attacker opens a `MarginfiAccount` and deposits/borrows such that maintenance-weighted assets exactly equal maintenance-weighted liabilities (`account_health == 0`), e.g. by depositing collateral and borrowing against it with matching weights, or simply using a freshly created account with zero balances.
2. Any party calls `start_liquidation` on this account; `check_pre_liquidation_condition_and_get_account_health` computes `healthy = (0 > 0) = false`, so the `HealthyAccount` error is not raised and the account enters `ACCOUNT_IN_RECEIVERSHIP` — see [1](#0-0)  and [3](#0-2) .
3. Within the same transaction, the attacker (as `liquidation_receiver`/unchecked `authority`) calls `lending_account_withdraw`/`lending_account_repay` — no owner signature is enforced during receivership — see [5](#0-4) .
4. `end_liquidation` is called last in the transaction; because `pre_health == 0`, the "health must not worsen" check passes as long as `post_health >= 0`, and the attacker keeps the standard liquidation bonus fee bounded by `fee_state.liquidation_max_fee` — see [6](#0-5)  and [7](#0-6)  — despite the account never having been in actual bad debt.

### Citations

**File:** programs/marginfi/src/state/marginfi_account.rs (L916-933)
```rust
    if let Some(bank_pk) = liability_bank_pk {
        let lending_account = &marginfi_account.lending_account;
        let liability_balance = lending_account
            .balances
            .iter()
            .find(|b| b.is_active() && b.bank_pk == *bank_pk)
            .ok_or(MarginfiError::LendingAccountBalanceNotFound)?;

        check!(
            !liability_balance.is_empty(BalanceSide::Liabilities),
            MarginfiError::NoLiabilitiesInLiabilityBank
        );

        check!(
            liability_balance.is_empty(BalanceSide::Assets),
            MarginfiError::AssetsInLiabilityBank
        );
    }
```

**File:** programs/marginfi/src/state/marginfi_account.rs (L944-959)
```rust
    let account_health = assets.checked_sub(liabs).ok_or_else(math_error!())?;
    let healthy = account_health > I80F48::ZERO;

    if let Some(cache) = health_cache.as_mut() {
        cache.set_healthy(healthy);
    }

    if healthy && !ignore_healthy {
        msg!(
            "pre_liquidation_health: {} ({} - {})",
            account_health,
            assets,
            liabs
        );
        return err!(MarginfiError::HealthyAccount);
    }
```

**File:** guides/DEVELOPERS_INTEGRATORS/ACCOUNT_LIFECYCLE.md (L109-110)
```markdown
- **Maintenance Health** (for liquidation eligibility): Uses `asset_weight_maint` and
  `liability_weight_maint`. If < 0, the account can be liquidated.
```

**File:** programs/marginfi/src/instructions/marginfi_account/liquidate_start.rs (L95-104)
```rust
    let (_pre_health, assets, liabs) = check_pre_liquidation_condition_and_get_account_health(
        marginfi_account,
        remaining_ais,
        None,
        &mut Some(&mut health_cache),
        HealthPriceMode::Live {
            liq_cache: Some(&mut liq_price_cache),
        },
        ignore_healthy,
    )?;
```

**File:** programs/marginfi/src/instructions/marginfi_account/liquidate_start.rs (L205-237)
```rust
#[derive(Accounts)]
pub struct StartLiquidation<'info> {
    /// Account under liquidation
    #[account(
        mut,
        has_one = liquidation_record @ MarginfiError::InvalidLiquidationRecord,
        constraint = {
            let acc = marginfi_account.load()?;
            !acc.get_flag(ACCOUNT_IN_RECEIVERSHIP)
                && !acc.get_flag(ACCOUNT_IN_DELEVERAGE)
                && !acc.get_flag(ACCOUNT_IN_FLASHLOAN)
                && !acc.get_flag(ACCOUNT_DISABLED)
                && !acc.get_flag(ACCOUNT_IN_ORDER_EXECUTION)
        } @MarginfiError::UnexpectedLiquidationState
    )]
    pub marginfi_account: AccountLoader<'info, MarginfiAccount>,

    /// The associated liquidation record PDA for the given `marginfi_account`
    #[account(mut)]
    pub liquidation_record: AccountLoader<'info, LiquidationRecord>,

    /// This account will have the authority to withdraw/repay as if they are the user authority
    /// until the end of the tx.
    ///
    /// CHECK: no checks whatsoever, liquidator decides this without restriction
    pub liquidation_receiver: UncheckedAccount<'info>,

    /// CHECK: validated against known hard-coded sysvar key
    #[account(
        address = solana_instructions_sysvar::id()
    )]
    pub instruction_sysvar: UncheckedAccount<'info>,
}
```

**File:** programs/marginfi/src/instructions/marginfi_account/withdraw.rs (L278-282)
```rust
    /// Must be marginfi_account's authority, unless in liquidation/deleverage receivership or order execution
    ///
    /// Note: during receivership and order execution, there are no signer checks whatsoever: any key can repay as
    /// long as the invariants checked at the end of execution are met.
    pub authority: Signer<'info>,
```

**File:** programs/marginfi/src/instructions/marginfi_account/liquidate_end.rs (L50-63)
```rust
    // Liquidator's allowed fee cannot go lower than the bonus fee minimum
    let fee_state_max_fee: I80F48 = fee_state.liquidation_max_fee.into();
    let max_fee: I80F48 = I80F48::max(
        I80F48!(1) + fee_state_max_fee,
        I80F48!(1) + LIQUIDATION_BONUS_FEE_MINIMUM,
    );

    // Ensure seized asset‐value ≤ N% of repaid liability‐value, where N = 100% + the bonus fee
    if !ignore_healthy {
        check!(
            seized <= repaid * max_fee,
            MarginfiError::LiquidationPremiumTooHigh
        );
    }
```

**File:** programs/marginfi/src/instructions/marginfi_account/liquidate_end.rs (L145-153)
```rust
    // health must not get worse
    if pre_health > post_health {
        msg!(
            "pre_health > post_health: {} >= {}",
            pre_health,
            post_health
        );
        return err!(MarginfiError::WorseHealthPostLiquidation);
    }
```
