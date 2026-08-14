Based on the investigation, marginfi-v2 has a directly analogous validation gap in the standard withdraw instruction: the destination token account for withdrawals is completely unconstrained.

### Title
Lack of validation on `destination_token_account` in `lending_account_withdraw` allows users to redirect withdrawn funds back into the bank's own liquidity vault - (File: programs/marginfi/src/instructions/marginfi_account/withdraw.rs)

### Summary
The `LendingAccountWithdraw` accounts struct declares `destination_token_account` with no `address`, `has_one`, or mint-ownership constraint tying it to the withdrawing user or preventing it from being the bank's own `liquidity_vault` (or another bank's vault sharing the same underlying mint). This mirrors the reported `PrimitiveEngine.withdraw` flaw where an unchecked recipient could be set to the contract/engine's own address, leaving funds "stuck" in the protocol rather than reaching the user.

### Finding Description
`lending_account_withdraw` decrements the user's internal share balance via `bank_account.withdraw()`/`withdraw_all()` and then performs an SPL transfer using `bank.withdraw_spl_transfer()`, moving tokens from `bank_liquidity_vault` to whatever `destination_token_account` the caller supplies [1](#0-0) . The account struct for this instruction only marks `destination_token_account` as `#[account(mut)]` with type `InterfaceAccount<'info, TokenAccount>` — there is no constraint requiring it to differ from `liquidity_vault`, from the bank's other reserve accounts, or to be owned by the withdrawing authority [2](#0-1) .

This asymmetry is notable when compared to the deposit path: `deposit_spl_transfer` explicitly enforces `check!(to.key.eq(&self.liquidity_vault), MarginfiError::InvalidTransfer)` [3](#0-2) , but the corresponding `withdraw_spl_transfer` performs no equivalent check on the `to` (destination) account at all [4](#0-3) .

If a user (accidentally, or via a malicious/buggy front-end/integrator) supplies the bank's own `liquidity_vault` (or, for the same-mint case, another bank/pool vault under the same token authority) as `destination_token_account`, the instruction proceeds: the user's internal shares are burned via `bank_account.withdraw()`, but the token transfer is a vault-to-vault (or self) transfer that produces no net outflow of tokens from the pool.

### Impact Explanation
The withdrawing user's claim on the pool (asset shares) is permanently destroyed while the underlying liquidity remains inside the bank's vault. Because `total_asset_shares` decreases without any corresponding decrease in vault liquidity, `asset_share_value` for all *other* depositors in that bank increases — value is durably redirected away from the withdrawing user to every other depositor in the same bank, with no way for the affected user to recover the burned shares. This is a genuine "value redirection with financial effect," directly analogous to Alice's funds being "stuck" in the Primitive engine.

### Likelihood Explanation
`destination_token_account` is a fully user/caller-supplied account with no protocol-side validation, and the withdraw path is reachable by any unprivileged account authority via the standard `lending_account_withdraw` instruction [5](#0-4) . No special privileges, oracle manipulation, or complex preconditions are required — only supplying the bank's `liquidity_vault` pubkey (a publicly known PDA-derived account) as the destination when constructing the instruction. This can occur through user error, a compromised/misconfigured front-end/integrator, or a crafted transaction submitted on a user's behalf during permissionless order execution/receivership flows where `authority` can be any signer [6](#0-5) .

### Recommendation
- **Short term:** Add a constraint on `destination_token_account` in `LendingAccountWithdraw` (and equivalent integration withdraw structs such as Kamino/Drift/JupLend/Solend where the same pattern may occur) to reject any destination equal to `bank.liquidity_vault`, `bank_liquidity_vault_authority`-owned accounts, or any known protocol vault, e.g. `constraint = destination_token_account.key() != liquidity_vault.key() @ MarginfiError::InvalidTransfer`.
- **Long term:** Mirror the `deposit_spl_transfer` pattern by adding an explicit sanity check inside `withdraw_spl_transfer` that the destination is not one of the bank's own vault accounts, and add fuzz/property tests (as already exists for other invariants in `programs/marginfi/fuzz`) asserting that a withdrawal always results in a net outflow of tokens from the vault equal to the amount debited from the user's shares.

### Proof of Concept
1. User has an active deposit position in `bank_pk` with liquidity vault `bank.liquidity_vault`.
2. User (or a script) constructs a `lending_account_withdraw` instruction identical to the normal flow in `withdrawIx` [7](#0-6) , but sets `destinationTokenAccount` to `bank.liquidityVault` instead of the user's own token account.
3. Instruction succeeds: `bank_account.withdraw()` burns the user's shares [8](#0-7) , and `withdraw_spl_transfer` executes a transfer from `liquidity_vault` to `liquidity_vault` (net zero token movement) since no check prevents this [9](#0-8) .
4. Result: the user's on-chain share balance is permanently reduced (or the balance closed via `withdraw_all`), the vault balance is unchanged, and `asset_share_value` for the bank silently increases for remaining depositors — the withdrawing user's assets are irrecoverably redistributed to other bank depositors.

### Citations

**File:** programs/marginfi/src/instructions/marginfi_account/withdraw.rs (L112-131)
```rust
        let (amount_pre_fee, share_amount) = if withdraw_all {
            // Note: In liquidation, we still want this passed on the books
            bank_account.withdraw_all(in_receivership)?
        } else {
            let amount_pre_fee = maybe_bank_mint
                .as_ref()
                .map(|mint| {
                    utils::calculate_pre_fee_spl_deposit_amount(
                        mint.to_account_info(),
                        amount,
                        clock.epoch,
                    )
                })
                .transpose()?
                .unwrap_or(amount);

            let share_amount = bank_account.withdraw(I80F48::from_num(amount_pre_fee))?;

            (amount_pre_fee, share_amount)
        };
```

**File:** programs/marginfi/src/instructions/marginfi_account/withdraw.rs (L176-192)
```rust
        marginfi_account.last_update = clock.unix_timestamp as u64;

        bank.withdraw_spl_transfer(
            amount_pre_fee,
            bank_liquidity_vault.to_account_info(),
            destination_token_account.to_account_info(),
            bank_liquidity_vault_authority.to_account_info(),
            maybe_bank_mint.as_ref(),
            token_program.to_account_info(),
            bank_signer!(
                BankVaultType::Liquidity,
                bank_loader.key(),
                liquidity_vault_authority_bump
            ),
            ctx.remaining_accounts,
        )?;
        bank.update_bank_cache(&group)?;
```

**File:** programs/marginfi/src/instructions/marginfi_account/withdraw.rs (L278-282)
```rust
    /// Must be marginfi_account's authority, unless in liquidation/deleverage receivership or order execution
    ///
    /// Note: during receivership and order execution, there are no signer checks whatsoever: any key can repay as
    /// long as the invariants checked at the end of execution are met.
    pub authority: Signer<'info>,
```

**File:** programs/marginfi/src/instructions/marginfi_account/withdraw.rs (L300-317)
```rust
    pub bank: AccountLoader<'info, Bank>,

    #[account(mut)]
    pub destination_token_account: InterfaceAccount<'info, TokenAccount>,

    /// CHECK: Seed constraint check
    #[account(
        seeds = [
            LIQUIDITY_VAULT_AUTHORITY_SEED.as_bytes(),
            bank.key().as_ref(),
        ],
        bump = bank.load()?.liquidity_vault_authority_bump,
    )]
    pub bank_liquidity_vault_authority: UncheckedAccount<'info>,

    #[account(mut)]
    pub liquidity_vault: InterfaceAccount<'info, TokenAccount>,

```

**File:** programs/marginfi/src/state/bank.rs (L699-702)
```rust
        check!(
            to.key.eq(&self.liquidity_vault),
            MarginfiError::InvalidTransfer
        );
```

**File:** programs/marginfi/src/state/bank.rs (L769-797)
```rust
    fn withdraw_spl_transfer<'info>(
        &self,
        amount: u64,
        from: AccountInfo<'info>,
        to: AccountInfo<'info>,
        authority: AccountInfo<'info>,
        maybe_mint: Option<&InterfaceAccount<'info, Mint>>,
        program: AccountInfo<'info>,
        signer_seeds: &[&[&[u8]]],
        remaining_accounts: &[AccountInfo<'info>],
    ) -> MarginfiResult {
        debug!(
            "withdraw_spl_transfer: amount: {} from {} to {}, auth {}",
            amount, from.key, to.key, authority.key
        );

        #[cfg(feature = "client")]
        if let Some(mint) = maybe_mint {
            invoke_client_token_transfer(
                program.key,
                amount,
                from,
                Some(mint.to_account_info()),
                to,
                authority,
                Some(mint.decimals),
                remaining_accounts,
                signer_seeds,
            )?;
```

**File:** programs/marginfi/src/lib.rs (L374-380)
```rust
    pub fn lending_account_withdraw<'info>(
        ctx: Context<'info, LendingAccountWithdraw<'info>>,
        amount: u64,
        withdraw_all: Option<bool>,
    ) -> MarginfiResult {
        marginfi_account::lending_account_withdraw(ctx, amount, withdraw_all)
    }
```

**File:** tests/utils/user-instructions.ts (L220-247)
```typescript
export const withdrawIx = (
  program: Program<Marginfi>,
  args: WithdrawIxArgs
) => {
  const oracleMeta: AccountMeta[] = args.remaining.map((pubkey) => ({
    pubkey,
    isSigner: false,
    isWritable: false,
  }));
  // False is the same as null, so if false we'll just pass null
  const all = args.withdrawAll === true ? true : null;
  const ix = program.methods
    .lendingAccountWithdraw(args.amount, all)
    .accounts({
      // marginfiGroup: args.marginfiGroup, // implied from bank
      marginfiAccount: args.marginfiAccount,
      // authority: args.authority, // implied from account
      bank: args.bank,
      destinationTokenAccount: args.tokenAccount,
      // bankLiquidityVaultAuthority = deriveLiquidityVaultAuthority(id, bank);
      // bankLiquidityVault = deriveLiquidityVault(id, bank)
      tokenProgram: TOKEN_PROGRAM_ID,
    })
    .remainingAccounts(oracleMeta)
    .instruction();

  return ix;
};
```
