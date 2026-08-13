This is a clear analog. The `SafetyModule.returnFunds()` share-price-injection frontrunning risk maps directly onto marginfi's `lending_pool_emissions_deposit` instruction, which is **permissionless** and directly inflates `bank.asset_share_value` for all outstanding shares the instant it lands — with no cooldown on deposits or withdrawals in marginfi (unlike Aave's `stkAAVE` unstake window, which is the only friction the original report relies on to make frontrunning non-trivial). That absence of any lockup makes this analog reachable and arguably *more* exploitable than the original.### Title
Permissionless `lending_pool_emissions_deposit()` share-price injection can be front-run/back-run with an instant deposit-withdraw to skim value from legitimate depositors and the emissions funder - ([File: programs/marginfi/src/instructions/marginfi_group/configure_bank.rs])

### Summary
The report describes `SafetyModule.returnFunds()`, which lets governance inject funds back into `StakedToken`, raising the share price for all current stakers. Because anyone can `stake()` right before the injection and `redeem()` right after (once past the unstake window), an attacker can risklessly capture a slice of the injected value. marginfi's `lending_pool_emissions_deposit` instruction is the direct structural analog: it is **permissionless**, transfers same-mint "emissions" into a bank's liquidity vault, and immediately recomputes `bank.asset_share_value` upward for every existing depositor — with **no cooldown or lock-up** on deposit/withdraw, which is strictly weaker than Aave's unstake-window friction that the original report already treats as insufficient protection.

### Finding Description
`lending_pool_emissions_deposit` is explicitly documented and implemented as permissionless: [1](#0-0) 

It recomputes the bank's `asset_share_value` as `(total_assets + amount) / total_asset_shares`, instantly and proportionally crediting every share outstanding at that moment — including shares minted by a deposit placed just before the call: [2](#0-1) 

Unlike `StakedToken`, which has an unstake/cooldown window that at least delays redemption after a `stake()`, marginfi's `lending_account_deposit` / `lending_account_withdraw` have no lock-up period: a user can deposit into a bank and withdraw again in the very next instruction or the very next slot. This is confirmed by the emissions-deposit test suite itself, which deposits, calls `lending_pool_emissions_deposit`, and shows `asset_share_value` jumps proportionally with no intervening delay required: [3](#0-2) [4](#0-3) 

Because `emissions_deposit` is a normal, mempool-visible transaction from a `depositor` signer (the emissions funder, e.g. protocol/partner rewards), an attacker observing it can:
1. Front-run it with a large `lending_account_deposit` into the same bank, minting asset shares at the pre-injection `asset_share_value`.
2. Let the emissions-deposit transaction land, which raises `asset_share_value` for all shares, including the attacker's freshly minted ones.
3. Immediately back-run with `lending_account_withdraw` (or `withdraw_all`), realizing the proportional gain in a single atomic block of transactions, with zero holding-period risk and zero exposure to bank utilization/borrow risk if the attacker sizes the deposit to dominate `total_asset_shares`.

This directly reproduces the root cause identified in the external report: a permissionless mechanism that increases share price for whoever holds shares at execution time, combined with unrestricted just-in-time entry/exit, allows value redirection away from the intended, existing (long-term) depositors and from the party funding the injection.

### Impact Explanation
An attacker can extract a disproportionate share of legitimate emissions/incentive funding intended for genuine long-term depositors of a bank, diluting the yield paid to existing users and effectively stealing value from whoever is funding `emissions_funding_account`. If the attacker deposits an amount large relative to existing `total_asset_shares`, they can capture the overwhelming majority of any given emissions injection risk-free (bounded only by the deposit limit of the bank, `bank.config.deposit_limit`, and gas/priority-fee costs to guarantee ordering).

### Likelihood Explanation
Likelihood is low-to-moderate: it depends on (a) `lending_pool_emissions_deposit` transactions being visible in the mempool before confirmation, (b) sufficiently large emissions deposits to be worth the attacker's capital lock-up cost and priority fees, and (c) the target bank's deposit limit permitting a large enough front-running deposit. This mirrors the report's own "Low" likelihood rating for `returnFunds()`, which is similarly gated on a large-enough injected amount. Because marginfi's deposit/withdraw have *no* cooldown at all (a strictly weaker mitigation than Aave's unstake window), the attack is easier to execute here than in the original report.

### Recommendation
- Restrict `lending_pool_emissions_deposit` funding events to be non-frontrunnable, e.g. by requiring the deposit to be queued and applied only against shares held for some minimum duration (a snapshot mechanism), or by having the group admin authorize/execute the deposit atomically alongside a distribution to a snapshotted depositor set rather than pro-rata against current live shares.
- Alternatively, rate-limit or cap `asset_share_value` increases from a single `lending_pool_emissions_deposit` call, and/or add a minimum holding-period requirement (or a deposit-then-immediate-withdraw fee) for the affected bank so JIT deposit-withdraw sandwiches are not profitable.
- Consider emitting the emissions deposit through a private/committed transaction path (e.g., pre-announced but executed by validators post-slot-boundary) to reduce front-runnability, analogous to the report's "pause then return funds" mitigation.

### Proof of Concept
1. Bank `B` has existing depositors holding `total_asset_shares = S` at `asset_share_value = V`.
2. Protocol/partner intends to reward depositors by calling `lending_pool_emissions_deposit(amount = A)` via `configure_bank.rs::lending_pool_emissions_deposit` (see cited lines 86–156), which will set `asset_share_value = (S*V + A) / S`.
3. Attacker observes this pending transaction in the mempool and submits, with higher priority fee, `lending_account_deposit(amount = D)` into bank `B`, minting `D/V` shares just before the emissions transaction lands (`marginfi_account/deposit.rs`).
4. The emissions transaction executes, updating `asset_share_value` to `(S*V + D + A) / (S + D)` — the attacker's shares now carry a slice of `A` proportional to `D / (S + D)`.
5. Attacker immediately submits `lending_account_withdraw` (or `withdraw_all`) for their full position, realizing `D + A * D / (S + D)` tokens, i.e., profit `≈ A * D / (S + D)` extracted with no holding period and no market risk, exactly analogous to the `stake()`→`returnFunds()`→`redeem()` sandwich described in the external report.

### Citations

**File:** programs/marginfi/src/instructions/marginfi_group/configure_bank.rs (L84-92)
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

**File:** programs/marginfi/tests/misc/emissions_deposit.rs (L211-286)
```rust
#[tokio::test]
async fn emissions_same_bank_deposit_updates_asset_share_value() -> anyhow::Result<()> {
    let test_f = TestFixture::new(Some(TestSettings::all_banks_payer_not_admin())).await;

    let usdc_bank = test_f.get_bank(&BankMint::Usdc);

    let emissions_funding = test_f.usdc_mint.create_token_account_and_mint_to(50).await;

    let depositor_a = test_f.create_marginfi_account().await;
    let depositor_b = test_f.create_marginfi_account().await;

    let depositor_a_usdc = test_f.usdc_mint.create_token_account_and_mint_to(40).await;
    let depositor_b_usdc = test_f.usdc_mint.create_token_account_and_mint_to(60).await;

    let depositor_a_amount = 40;
    depositor_a
        .try_bank_deposit(
            depositor_a_usdc.key,
            usdc_bank,
            depositor_a_amount as f64,
            None,
        )
        .await?;

    let depositor_b_amount = 60;
    depositor_b
        .try_bank_deposit(
            depositor_b_usdc.key,
            usdc_bank,
            depositor_b_amount as f64,
            None,
        )
        .await?;

    let bank_before = usdc_bank.load().await;
    let shares_before = I80F48::from(bank_before.total_asset_shares);
    let share_value_before = I80F48::from(bank_before.asset_share_value);

    let liquidity_vault_before =
        TokenAccountFixture::fetch(test_f.context.clone(), bank_before.liquidity_vault)
            .await
            .balance()
            .await;

    let emissions_deposit = 50;
    usdc_bank
        .try_emissions_deposit(native!(emissions_deposit, "USDC"), emissions_funding.key)
        .await?;

    let bank_after = usdc_bank.load().await;
    let shares_after = I80F48::from(bank_after.total_asset_shares);
    let share_value_after = I80F48::from(bank_after.asset_share_value);

    let liquidity_vault_after =
        TokenAccountFixture::fetch(test_f.context.clone(), bank_after.liquidity_vault)
            .await
            .balance()
            .await;

    let asset_shares_value_multiplier =
        1.0 + emissions_deposit as f64 / (depositor_a_amount + depositor_b_amount) as f64;

    assert_eq!(shares_after, shares_before);

    // Should be equal, zero liabilities are present
    assert_eq!(
        share_value_before
            .checked_mul(I80F48::from_num(asset_shares_value_multiplier))
            .unwrap(),
        share_value_after
    );
    assert_eq!(
        liquidity_vault_after - liquidity_vault_before,
        native!(emissions_deposit, "USDC")
    );
    assert_eq!(I80F48::from(bank_after.emissions_remaining), I80F48::ZERO);
```

**File:** tests/specs/basic/18_emissionsDeposit.spec.ts (L62-90)
```typescript
function assertSameBankDeposit(
  sharesBefore: { value: number[] },
  sharesAfter: { value: number[] },
  shareValueBefore: { value: number[] },
  shareValueAfter: { value: number[] },
  liquidityVaultBefore: number,
  liquidityVaultAfter: number,
  emissionsDepositAmount: number,
) {
  const beforeVal = wrappedI80F48toBigNumber(shareValueBefore).toNumber();
  const totalDeposited =
    wrappedI80F48toBigNumber(sharesBefore).toNumber() * beforeVal;
  assert.equal(
    wrappedI80F48toBigNumber(sharesAfter).toString(),
    wrappedI80F48toBigNumber(sharesBefore).toString(),
    "total asset shares should be unchanged",
  );
  // Should be roughly equal. If at all interest accrual happens, time barely passes between
  // this and the last one.
  assert.approximately(
    wrappedI80F48toBigNumber(shareValueAfter).toNumber(),
    beforeVal * (1 + emissionsDepositAmount / totalDeposited),
    beforeVal * 10 ** -10,
  );
  assert.equal(
    liquidityVaultAfter - liquidityVaultBefore,
    emissionsDepositAmount,
  );
}
```
