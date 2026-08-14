### Title
Front-runnable `lending_pool_emissions_deposit` allows flash-deposit sniping of same-mint emissions rewards - (File: `programs/marginfi/src/instructions/marginfi_group/configure_bank.rs`)

### Summary
`lending_pool_emissions_deposit` is a permissionless instruction that lets anyone push extra underlying tokens into a bank's liquidity vault, instantly bumping `asset_share_value` for every existing depositor of that bank [1](#0-0) . Because the reward (the share-value bump) is computed and applied atomically against whatever `total_asset_shares` exist at the instant the instruction executes, with no time-weighting or minimum holding period, a user can watch the mempool for an emissions-deposit transaction, front-run it with a large deposit into the same bank, let the emissions land (capturing a proportional slice of the reward), and immediately withdraw — exactly the "watch mempool → deposit before the reward-triggering event → withdraw after" pattern described in the referenced Biconomy `LiquidityPool` front-run finding.

### Finding Description
The instruction increases `bank.asset_share_value` uniformly based on `total_asset_shares` at call time: [2](#0-1) 

Any signer can be the `depositor`/funding authority — there is no admin/`has_one` check on who may call `lending_pool_emissions_deposit` (unlike `lending_pool_configure_bank`, which requires `admin`) [3](#0-2) . The test suite confirms this is by design ("Permissionlessly deposit same-mint emissions... increasing depositor value through asset share value") and that anyone with a funded token account can invoke it successfully [4](#0-3) .

Since:
1. Regular deposits/withdrawals into the bank (`lending_account_deposit`/`lending_account_withdraw`) are also permissionless and immediate,
2. `asset_share_value` bump from an emissions deposit is applied as a lump sum proportional to *current* shares (not time-weighted, no vesting/lockup), and
3. The emissions-deposit transaction is visible in the mempool before confirmation,

an attacker can sandwich it: deposit right before the emissions-deposit transaction lands (acquiring shares at the pre-bump `asset_share_value`), let it execute (capturing a pro-rata share of the reward instantly), then withdraw immediately after — extracting value that was intended to reward existing/long-term depositors, diluting genuine depositors' expected share of the emissions.

This mirrors the root cause identified in the referenced report: rewards computed from an instantaneous pool/share state with no protection against transiently inflating one's position purely to intercept a reward event.

### Impact Explanation
Financial value (the emissions/incentive funds transferred into the liquidity vault by whoever calls this instruction — often the protocol/admin intending to reward existing depositors) can be redirected disproportionately to an opportunistic flash-depositor instead of the long-term depositors it was meant to reward. This directly dilutes honest depositors' returns and can be repeated every time an emissions deposit is broadcast, since there is no cooldown, minimum holding period, or snapshot mechanism tied to deposit duration.

### Likelihood Explanation
Emissions deposits are visible pre-confirmation on Solana (via RPC/mempool-equivalent, i.e., pending transaction visibility), and both `lending_account_deposit` and `lending_account_withdraw` are permissionless, single-transaction, atomic actions with no lockup. Any actor monitoring for emissions-deposit calls (which is itself a permissionless, observable instruction with fixed accounts per bank) can trivially construct the front-run/back-run sandwich in the same slot or adjacent slots, making this practically exploitable whenever a bank receives an emissions/incentive deposit of meaningful size relative to its total deposits.

### Recommendation
- Require a minimum holding period (time-weighted average shares) before a depositor's shares are eligible for emissions-derived `asset_share_value` gains, or
- Snapshot eligible `total_asset_shares`/depositor balances at a fixed point prior to the emissions-deposit instruction (e.g., only count shares held before the current slot/timestamp), or
- Restrict who may trigger `lending_pool_emissions_deposit` timing relative to deposits (e.g., queue emissions to be distributed gradually over time rather than as an instant share-value bump), similar to a linear vesting/streaming distribution rather than an atomic value injection.

### Proof of Concept
1. Attacker monitors pending transactions for `lending_pool_emissions_deposit` calls against bank `B` (a permissionless, publicly invocable instruction) [5](#0-4) .
2. Attacker front-runs with `lending_account_deposit` of a large amount into `B`, receiving `asset_shares` at the pre-bump `asset_share_value`.
3. The emissions deposit executes, and `bank.asset_share_value` is increased for all shares including the attacker's, per: `bank.asset_share_value = updated_total_assets / total_asset_shares` [6](#0-5) .
4. Attacker immediately calls `lending_account_withdraw` (`withdraw_all=true`) to redeem shares at the new, higher `asset_share_value`, realizing profit from the emissions deposit without ever being a genuine long-term liquidity provider, diluting the intended beneficiaries.

### Citations

**File:** programs/marginfi/src/instructions/marginfi_group/configure_bank.rs (L68-82)
```rust
#[derive(Accounts)]
pub struct LendingPoolConfigureBank<'info> {
    #[account(
        has_one = admin @ MarginfiError::Unauthorized,
    )]
    pub group: AccountLoader<'info, MarginfiGroup>,

    pub admin: Signer<'info>,

    #[account(
        mut,
        has_one = group @ MarginfiError::InvalidGroup,
    )]
    pub bank: AccountLoader<'info, Bank>,
}
```

**File:** programs/marginfi/src/instructions/marginfi_group/configure_bank.rs (L84-96)
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

**File:** tests/specs/basic/18_emissionsDeposit.spec.ts (L118-184)
```typescript
  it("deposit same-mint emissions updates share value", async () => {
    // Mint 50 Token A to ATA owned by the bankrun payer
    const depositorAmount = 50;
    const fundingAta = getAssociatedTokenAddressSync(
      ecosystem.tokenAMint.publicKey,
      depositor,
    );

    let fundTx = new Transaction();
    fundTx.add(
      createMintToInstruction(
        ecosystem.tokenAMint.publicKey,
        fundingAta,
        depositor,
        BigInt(depositorAmount * 10 ** ecosystem.tokenADecimals),
      ),
    );
    await provider.sendAndConfirm(fundTx);

    // Snapshot bank and liquidity vault
    const bankBefore = await program.account.bank.fetch(bankKeypairA.publicKey);
    const [sharesBefore, shareValueBefore] = [
      bankBefore.totalAssetShares,
      bankBefore.assetShareValue,
    ];
    const [liquidityVault] = deriveLiquidityVault(
      program.programId,
      bankKeypairA.publicKey,
    );
    const liquidityVaultBefore = await getTokenBalance(
      provider,
      liquidityVault,
    );

    // Emissions deposit of 50 Token A from bankrun payer into liquidity vault
    const emissionsDepositAmount =
      depositorAmount * 10 ** ecosystem.tokenADecimals;
    const ix = await lendingPoolEmissionsDeposit(program, {
      bank: bankKeypairA.publicKey,
      mint: bankBefore.mint,
      fundingAccount: fundingAta,
      depositor: depositor,
      liquidityVault: liquidityVault,
      amount: new BN(emissionsDepositAmount),
    });
    let tx = new Transaction().add(ix);
    await provider.sendAndConfirm(tx);

    // Fetch after state
    const bankAfter = await program.account.bank.fetch(bankKeypairA.publicKey);
    const [sharesAfter, shareValueAfter] = [
      bankAfter.totalAssetShares,
      bankAfter.assetShareValue,
    ];

    const liquidityVaultAfter = await getTokenBalance(provider, liquidityVault);

    assertSameBankDeposit(
      sharesBefore,
      sharesAfter,
      shareValueBefore,
      shareValueAfter,
      liquidityVaultBefore,
      liquidityVaultAfter,
      emissionsDepositAmount,
    );
  });
```
