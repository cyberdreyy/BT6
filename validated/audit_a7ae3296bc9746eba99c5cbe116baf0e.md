### Title
Partial `lending_account_withdraw` burns shares for the pre-cap amount but transfers only the vault-capped (smaller) amount during bank sunset - ([File: programs/marginfi/src/instructions/marginfi_account/withdraw.rs])

### Summary
When a bank has completed its tokenless-repayment sunset process (`TOKENLESS_REPAYMENTS_COMPLETE` flag set) and its liquidity vault holds less than the amount a user is entitled to, a **partial** withdraw burns shares based on the full requested/entitled amount before the amount actually transferred is capped down to the vault's real balance. The user's asset-share balance is reduced to reflect the full `amount_pre_fee`, but they only receive `min(amount_pre_fee, actual_vault_balance)` tokens — the difference is silently lost, exactly mirroring the yAxis `Vault.withdraw` bug where shares are burned for more than the fair-share amount actually paid out.

### Finding Description
In `lending_account_withdraw` [1](#0-0) , for the non-`withdraw_all` path, `amount_pre_fee` is computed from the user-requested `amount` and immediately used to burn shares via `bank_account.withdraw(I80F48::from_num(amount_pre_fee))?`: [2](#0-1) 

Only *after* this share-burn has already happened does the code check whether the bank is flagged `TOKENLESS_REPAYMENTS_COMPLETE` and, if so, cap the actually-transferred amount to whatever remains in the liquidity vault: [3](#0-2) 

The capped `amount_pre_fee` (potentially much smaller than what was used to burn shares) is what actually gets transferred to the user via `bank.withdraw_spl_transfer`: [4](#0-3) 

This is the same root cause as the yAxis `Vault.withdraw` M-03 finding: the code computes the shares to burn from the requested amount *before* checking whether enough underlying liquidity exists, and when it turns out that less than the requested amount is available, it reduces only the payout — not the already-computed share burn — leaving the withdrawer under-compensated for the shares they lost.

The `TOKENLESS_REPAYMENTS_COMPLETE` flag and this liquidity shortfall condition are directly exercised in the bankruptcy/sunset test suite [5](#0-4) , confirming this is a reachable, intended-to-be-handled state (bank has discharged its debts but does not have enough liquidity for all lenders to withdraw in full) rather than a purely theoretical scenario.

### Impact Explanation
In this state, any user performing a **partial** withdraw (not `withdraw_all`) from a bank whose liquidity vault is short of the entitled amount has their on-chain asset shares reduced by the full uncapped amount while receiving fewer tokens than that share reduction represents. This is a direct, quantifiable value loss to the withdrawer with no corresponding benefit anywhere else in the system (the difference is not credited or trackable) — it is a durable state-inconsistency/value-redirection bug with financial effect, not merely a rounding/dust issue.

The existing test coverage in `zb02_e2eSunset.spec.ts` only exercises the `withdraw_all` path for the shortfall scenario, where the balance is fully closed anyway ("gets just what's left") [6](#0-5) ; there is no evident test that exercises the partial-withdraw case with this flag active and an under-funded vault, so the burns-too-many-shares scenario for a still-open balance is not verified to be prevented.

### Likelihood Explanation
This requires the bank to reach the `TOKENLESS_REPAYMENTS_COMPLETE` sunset state (an admin-driven deleverage/bankruptcy-resolution flow) and for the liquidity vault to be under-funded relative to outstanding withdrawable claims — a scenario the codebase itself anticipates and builds tooling for (deleverage, sunset tests). Within that state, any unprivileged account holder performing a normal partial withdraw (no special privilege needed) can trigger the mismatch. Likelihood is therefore contingent on the bank being in this specific late-stage sunset condition, but once there, the bug is trivially triggered by ordinary user action.

### Recommendation
Compute the capped `amount_pre_fee` (`u64::min(requested_amount_pre_fee, vault_balance)`) **before** calling `bank_account.withdraw(...)`, so that the shares burned always correspond exactly to the amount actually transferred, rather than burning shares for the full request and only later reducing the transferred amount.

### Proof of Concept
1. Bring a bank to the `TOKENLESS_REPAYMENTS_COMPLETE` state (as exercised in `zb02_e2eSunset.spec.ts`) such that its liquidity vault balance is less than a user's owed asset value.
2. Have that user call `lending_account_withdraw` with `withdraw_all = false` and `amount` set to (or exceeding) their owed balance so that `amount_pre_fee` exceeds the vault's actual balance.
3. Observe: `bank_account.withdraw(I80F48::from_num(amount_pre_fee))` at line 128 burns shares equal to the full `amount_pre_fee`, closing/reducing the balance as if the user received that much.
4. The subsequent cap at lines 134-144 reduces the actual token transfer to `vault_balance < amount_pre_fee`.
5. The user's shares are permanently reduced by more than the value they received, and since the balance remains open (not `withdraw_all`), there is no compensating mechanism to recover the shortfall — the loss is not `assert`-checked or reverted anywhere in this code path.

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

**File:** programs/marginfi/src/instructions/marginfi_account/withdraw.rs (L133-144)
```rust
        // If in deleverage mode and deleverage is complete, you get what's left!
        let amount_pre_fee = if bank.get_flag(TOKENLESS_REPAYMENTS_COMPLETE) {
            let actual = accessor::amount(&bank_liquidity_vault.to_account_info())?;
            msg!(
                "amount expected withdrawn: {:?}, actual: {:?}",
                amount_pre_fee,
                actual
            );
            u64::min(amount_pre_fee, actual)
        } else {
            amount_pre_fee
        };
```

**File:** programs/marginfi/src/instructions/marginfi_account/withdraw.rs (L178-191)
```rust
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
```

**File:** tests/specs/bankruptcy/zb02_e2eSunset.spec.ts (L527-530)
```typescript
  // Note: at this point we have discharged all b1 debts. But wait! There's not enough liquidity in
  // b1 for depositors to withdraw!

  it("(user 1) Attempts to withdraw b1 - gets just what's left in the liquidity vault!", async () => {
```

**File:** tests/specs/bankruptcy/zb02_e2eSunset.spec.ts (L599-609)
```typescript
    // User gets what's left!
    assert.equal(lstAfter - lstBefore, liqVaultBefore);
    // Liquidity vault is empty
    assert.equal(liqVaultAfter, 0);
    // Balance closed!
    assert.equal(userBefore.lendingAccount.balances[0].active, 1);
    assert.equal(userAfter.lendingAccount.balances[0].active, 0);
    // Before we have user 1 and 2's balances, after all that's left is user 2's balance.
    assertI80F48Equal(bankBefore.totalAssetShares, new BN(100 * 10 ** 9 + 42));
    assertI80F48Equal(bankAfter.totalAssetShares, new BN(42));
  });
```
