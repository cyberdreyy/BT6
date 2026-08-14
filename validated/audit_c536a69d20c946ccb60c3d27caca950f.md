### Title
Permissionless `lending_pool_emissions_deposit` allows a sandwich attack that lets an attacker capture pro-rata "rebase" rewards with zero time exposure - ([File: programs/marginfi/src/instructions/marginfi_group/configure_bank.rs])

### Summary
`lending_pool_emissions_deposit` instantly injects tokens into a bank's liquidity vault and bumps `asset_share_value` pro-rata across all currently-outstanding shares, with no time-weighting, warm-up, or minimum holding period on the deposit/withdraw side. This mirrors the root cause of the Yieldy finding: a "rebase" event distributes rewards based on shares held at the moment the rebase fires, not on how long those shares have been held, and there is no mechanism to prevent a user from entering right before the event and exiting right after.

### Finding Description
`lending_pool_emissions_deposit` is explicitly permissionless [1](#0-0) . It accrues interest, transfers `amount` into the bank's liquidity vault, and then recomputes `asset_share_value` as `(total_assets + amount) / total_asset_shares` — i.e., it retroactively raises the value of every existing share instantly and irrevocably at the moment it is called: [2](#0-1) 

There is no check on how recently a depositor's shares were created, and marginfi's `lending_account_deposit` / `lending_account_withdraw` instructions impose no cooldown, warm-up period, or lockup — a user can deposit and withdraw within the same block/transaction (subject only to health-check and `deposit_limit` constraints). `guides/USER/EMISSIONS.md` confirms the design intent is real-time pro-rata distribution with no time-weighting: "Each lender's share is determined on a pro-rata basis in real time" [3](#0-2) , and the emissions-deposit test suite confirms `asset_share_value` jumps immediately and proportionally for whoever holds shares at call time [4](#0-3) .

This is the same bug class as the Yieldy `Staking.sol` report: a "rebase"/reward-distribution event (there, the forced rebase in `stake`/`instantUnstakeCurve`; here, `lending_pool_emissions_deposit`) pays out based on instantaneous balance rather than duration held, and nothing prevents a user from timing their deposit to just before the event and their withdrawal to just after, capturing a share of rewards intended for depositors who bore the pool's risk/duration.

### Impact Explanation
An attacker who observes (or anticipates, e.g., via a public/scheduled reward campaign) an upcoming `lending_pool_emissions_deposit` call funded by a third party (project admin, reward sponsor, or campaign wallet) can:
1. Deposit a large amount into the target bank immediately before the emissions-deposit transaction lands, diluting existing long-term depositors' share of the upcoming reward.
2. Let the emissions deposit execute, instantly raising `asset_share_value` for all current shareholders, including the attacker's newly-minted shares.
3. Immediately withdraw, realizing a slice of the reward proportional to the attacker's now-inflated share of `total_asset_shares`, despite having zero actual duration of capital exposure or risk in the pool.

This directly redirects value away from genuine, longer-term depositors to the attacker, with no compensating cost besides transaction fees — a durable, exploitable misvaluation/value-redirection with financial effect, consistent with the "Concrete ... value redirection" acceptance bar.

### Likelihood Explanation
Likelihood depends on whether emissions deposits are funded by third parties whose transactions are visible/predictable before confirmation (mempool visibility, scheduled/announced campaigns, or repeated/automatic funding flows) versus purely self-funded lump sums (which are not profitable to sandwich, since the funder recoups their own contribution pro-rata to their own share). Given `lending_pool_emissions_deposit` is explicitly permissionless and documented as a general reward-distribution mechanism (not restricted to self-funding by the sole depositor), and Solana's leader/validator MEV tooling (Jito bundles, etc.) makes transaction-ordering attacks practical, likelihood is moderate: it requires an externally-funded, non-atomic emissions deposit to be exploitable, but no protocol-level control currently prevents it.

### Recommendation
Add time-weighting or an anti-sandwich guard around pro-rata reward events analogous to the Yieldy fix (`warmUpBalance = userWarmInfo.amount` semantics): e.g., snapshot eligible balances prior to the emissions deposit (based on a `last_deposit_ts` cooldown or a minimum holding-period requirement before a deposit counts toward `asset_share_value` upside), or restrict `lending_pool_emissions_deposit` to be called only atomically alongside the funding transaction in a way that cannot be front/back-run (e.g., commit-reveal, or requiring the depositor of emissions and the funding to be the sole beneficiary rather than pro-rata to all shares at call time). At minimum, document/flag this as an accepted MEV risk if the emissions-deposit funder is always expected to be the reward-sponsor rather than an independent third party whose transaction could be sandwiched.

### Proof of Concept
1. Attacker monitors mempool/known schedule for an upcoming `lending_pool_emissions_deposit(amount)` call on Bank X, funded by Reward Sponsor S (a wallet other than the attacker).
2. Attacker submits `lending_account_deposit` into Bank X for a large amount `D`, ordered immediately before S's `lending_pool_emissions_deposit` transaction (e.g., via a Jito bundle or same-slot ordering).
3. S's `lending_pool_emissions_deposit(amount)` executes: `bank.asset_share_value = (total_assets + amount) / total_asset_shares` [5](#0-4)  — this raises the value of every share including the attacker's newly deposited `D`.
4. Attacker immediately submits `lending_account_withdraw` for their full position, walking away with `D` plus a pro-rata cut of `amount` proportional to `D / total_asset_shares_after_step_2`, at zero net time-exposure to the pool's other risks.

Note: I could not fully verify (due to index limits) whether `deposit_limit` or other per-bank caps in `programs/marginfi/src/state/bank.rs` would materially cap the attacker's deposit size `D` in production configurations, which would bound (but not eliminate) the exploit's magnitude. A Devin session with full repo/test access would be needed to confirm exact bank configuration limits used in production groups.

### Citations

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

**File:** programs/marginfi/src/instructions/marginfi_group/configure_bank.rs (L117-146)
```rust
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

**File:** guides/USER/EMISSIONS.md (L10-17)
```markdown
For example, a Campaign might distribute 7 tokens of A to lenders per week (one per day). Each
lender's share is determined on a pro-rata basis in real time. If there are two lenders, each
depositing the same amount, then each will be 3.5 tokens per week.

Now let's say there are two users, the first one has \$1 in deposits. User 2 deposits \$1 on
Thursday, and \$5 more on Saturday. This means User 1 and 2 both get 0.5 tokens/day on Thursday and
Friday. On Saturday and beyond, User 1 gets $1/(1+6)= 0.143$ tokens, and User 2 gets $6/(1+6)=0.857$
tokens/day.
```

**File:** programs/marginfi/tests/misc/emissions_deposit.rs (L245-286)
```rust
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
