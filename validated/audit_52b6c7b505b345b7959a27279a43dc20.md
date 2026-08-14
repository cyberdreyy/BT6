This confirms the exact bug pattern: in the "first-come-first-served" TOKENLESS_REPAYMENTS_COMPLETE deleverage endgame, user 1 explicitly "gets what's left in the liquidity vault" — the test asserts `lstAfter - lstBefore == liqVaultBefore` and `liqVaultAfter == 0`, and later user 2 "gets nothing, we're out of money". This is the on-chain intended/tested behavior, but the clamp value read from `accessor::amount(&bank_liquidity_vault...)` (the raw vault balance) is never reduced by `bank.collected_insurance_fees_outstanding` / `collected_group_fees_outstanding` / `collected_program_fees_outstanding` before being handed to the first withdrawer, exactly mirroring the Locke.sol `excess` calculation that omitted `depositTokenFlashloanFeeAmount`. [1](#0-0) [2](#0-1) [3](#0-2) 

### Title
Tokenless-deleverage withdraw clamp ignores outstanding fee reserves, letting the first withdrawer drain fee-earmarked liquidity - (File: `programs/marginfi/src/instructions/marginfi_account/withdraw.rs`)

### Summary
When a bank reaches `TOKENLESS_REPAYMENTS_COMPLETE` (all liabilities discharged via deleverage, but the liquidity vault may be short of funds), `lending_account_withdraw` clamps a depositor's payout to the raw SPL balance of `liquidity_vault` via `accessor::amount(&bank_liquidity_vault...)`. That raw balance is never reduced by the bank's outstanding, uncollected fee buckets (`collected_insurance_fees_outstanding`, `collected_group_fees_outstanding`, `collected_program_fees_outstanding`), which by design still sit physically inside `liquidity_vault` until someone calls the permissionless `lending_pool_collect_bank_fees`. The first depositor(s) to withdraw in this state can therefore consume fee-earmarked tokens along with their own principal, exactly the same root cause as the Locke.sol `excess` calculation that omitted `depositTokenFlashloanFeeAmount`.

### Finding Description
`lending_account_withdraw` computes the user's owed `amount_pre_fee` from share value, then in `TOKENLESS_REPAYMENTS_COMPLETE` mode clamps it to the vault's live balance: [1](#0-0) 

The `actual` value used for the clamp is the unmodified token account balance, not `actual - collected_insurance_fees_outstanding - collected_group_fees_outstanding - collected_program_fees_outstanding`. The project's own fuzz-harness solvency invariant makes the accounting model explicit: it defines "net vault" liquidity as `vault_balance - outstanding_fees`, precisely the subtraction missing in the withdraw clamp: [4](#0-3) 

Per the fee-collection design docs, fees accrue into the `collected_*_fees_outstanding` counters but the actual tokens are not physically separated from the `liquidity_vault` until the permissionless `LendingPoolCollectBankFees` ix runs: [5](#0-4) 

Because the clamp reads the raw vault balance rather than the fee-adjusted balance, any accrued-but-uncollected insurance/group/program fees are indistinguishable from legitimate depositor principal once a bank enters the tokenless-deleverage endgame. A depositor who withdraws first receives `min(shares_owed, raw_vault_balance)`, which may include those fee tokens, draining the vault before governance can `collect_bank_fees`/`withdraw_fees`/`withdraw_insurance`, and before later depositors withdraw. The project's own end-to-end test explicitly documents and asserts this exact "first come, first served, last one gets nothing" outcome: [3](#0-2) 

### Impact Explanation
This causes fund loss/misallocation with real financial effect:
- Governance/group admin can be permanently unable to collect insurance, group, and program fees that had already accrued on the bank, because the depositor who withdrew first consumed those tokens as part of their "whatever's left" payout.
- Later depositors in the same bank receive strictly less than their fair pro-rata share (in the worst case, zero, as shown by user 2 receiving `0` in the cited test), because an earlier withdrawer's payout was inflated by fee reserves that should not have been available to any depositor.
- This is a durable, on-chain financial-effect bug: value that belongs to the protocol/insurance fund is redirected to whichever unprivileged depositor withdraws first.

### Likelihood Explanation
This is reachable whenever a bank has (a) accrued but not yet collected fees (a normal, expected steady state — `collect_bank_fees` is permissionless but not guaranteed to run before every deleverage/liquidity crunch), and (b) enters `TOKENLESS_REPAYMENTS_COMPLETE` following a legitimate deleverage flow used for winding down insolvent/illiquid banks. The withdraw call itself is fully unprivileged — any depositor can trigger the clamp path by simply calling withdraw/withdraw_all once the flag is set. The sequence is deterministic and does not require any race condition beyond withdraw ordering, which is already the documented outcome of this code path.

### Recommendation
When computing `actual` for the `TOKENLESS_REPAYMENTS_COMPLETE` clamp, subtract the bank's outstanding fee buckets before comparing/clamping:
```rust
let actual = accessor::amount(&bank_liquidity_vault.to_account_info())?
    .saturating_sub(bank.collected_insurance_fees_outstanding.into())
    .saturating_sub(bank.collected_group_fees_outstanding.into())
    .saturating_sub(bank.collected_program_fees_outstanding.into());
```
Alternatively, require `lending_pool_collect_bank_fees` to be run (or fees zeroed) before allowing withdrawals to clamp against the raw vault balance in this state, so that outstanding fees are always segregated from depositor principal.

### Proof of Concept
1. Bank X has active borrowers; interest accrues, incrementing `collected_insurance_fees_outstanding`/`collected_group_fees_outstanding`/`collected_program_fees_outstanding`, while `LendingPoolCollectBankFees` has not yet been run, so those amounts remain physically inside `liquidity_vault` (per `guides/ADMIN/COLLECTING_FEES.md`).
2. Risk admin runs deleverage on all borrowers of Bank X until `total_liability_shares` reaches ~0, auto-setting `TOKENLESS_REPAYMENTS_COMPLETE` (as in `repay.rs`, lines 142-149, and exercised by `tests/specs/bankruptcy/zb02_e2eSunset.spec.ts`).
3. `liquidity_vault` now holds `depositor_principal_remaining + uncollected_fees`, but is insufficient to fully pay all depositors' share-derived claims.
4. Depositor A calls `lending_account_withdraw` with `withdraw_all = true`. `amount_pre_fee` (share value) exceeds the vault's raw balance, so the code clamps to `actual = accessor::amount(liquidity_vault)`, which includes the uncollected fees — Depositor A receives that entire amount, draining the vault to `0` (as literally asserted in the cited test: `liqVaultAfter == 0`).
5. Governance now calls `LendingPoolCollectBankFees`; `available_liquidity` is `0`, so `insurance_fee_transfer_amount`/`group_fee_transfer_amount`/`program_fee_transfer_amount` all resolve to `0` — fees are effectively lost even though `collected_*_fees_outstanding` may still show nonzero balances that can never actually be transferred out.
6. Depositor B (e.g. "user 2" in the cited test) then calls withdraw and receives `0`, confirmed by the test's own assertion `lstAfter - lstBefore == 0`.

### Citations

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

**File:** trident-tests/fuzz_0/invariants/solvency.rs (L46-66)
```rust
    let asset_share_value = from_wrapped(bank.asset_share_value.value);
    let liability_share_value = from_wrapped(bank.liability_share_value.value);
    let total_asset_shares = from_wrapped(bank.total_asset_shares.value);
    let total_liability_shares = from_wrapped(bank.total_liability_shares.value);

    let total_deposits = total_asset_shares
        .checked_mul(asset_share_value)
        .expect("total_deposits overflow");
    let total_liabilities = total_liability_shares
        .checked_mul(liability_share_value)
        .expect("total_liabilities overflow");

    let outstanding_fees = from_wrapped(bank.collected_group_fees_outstanding.value)
        + from_wrapped(bank.collected_insurance_fees_outstanding.value)
        + from_wrapped(bank.collected_program_fees_outstanding.value);

    let vault_balance = I80F48::from_num(token_balance(trident, bank.liquidity_vault));
    let net_vault = vault_balance - outstanding_fees;
    let net_book = total_deposits - total_liabilities;

    let drift = (net_vault - net_book).abs();
```

**File:** tests/specs/bankruptcy/zb02_e2eSunset.spec.ts (L527-609)
```typescript
  // Note: at this point we have discharged all b1 debts. But wait! There's not enough liquidity in
  // b1 for depositors to withdraw!

  it("(user 1) Attempts to withdraw b1 - gets just what's left in the liquidity vault!", async () => {
    const user = users[1];
    const userAccount = user.accounts.get(USER_ACCOUNT_THROWAWAY);

    // first repay our debt so we can withdraw without interference.
    // For repayAll, include all active balances, including the closing bank.
    const remaining = composeRemainingAccounts([
      [banks[0], oracles.pythPullLst.publicKey],
      [banks[1], oracles.pythPullLst.publicKey],
    ]);
    const repayTx = new Transaction();
    repayTx.add(
      await repayIx(user.mrgnBankrunProgram, {
        marginfiAccount: userAccount,
        bank: banks[0],
        tokenAccount: user.lstAlphaAccount,
        remaining,
        amount: new BN(1234),
        repayAll: true,
      })
    );
    await processBankrunTransaction(bankrunContext, repayTx, [user.wallet]);

    const [liqVault] = deriveLiquidityVault(bankrunProgram.programId, banks[1]);

    const [bankBefore, userBefore, lstBefore, liqVaultBefore] =
      await Promise.all([
        bankrunProgram.account.bank.fetch(banks[1]),
        bankrunProgram.account.marginfiAccount.fetch(userAccount),
        getTokenBalance(bankRunProvider, user.lstAlphaAccount),
        getTokenBalance(bankRunProvider, liqVault),
      ]);

    // For withdrawAll, include all active balances, including the closing bank.
    const remainingWithdraw = composeRemainingAccounts(
      [
        [banks[1], oracles.pythPullLst.publicKey],
        [banks[0], oracles.pythPullLst.publicKey],
      ].filter((group) => !group[0].equals(banks[1]))
    );
    const tx = new Transaction();
    tx.add(
      await withdrawIx(user.mrgnBankrunProgram, {
        marginfiAccount: userAccount,
        bank: banks[1],
        tokenAccount: user.lstAlphaAccount,
        remaining: remainingWithdraw,
        amount: new BN(12345),
        withdrawAll: true,
      })
    );
    await processBankrunTransaction(bankrunContext, tx, [user.wallet]);

    const [bankAfter, userAfter, lstAfter, liqVaultAfter] = await Promise.all([
      bankrunProgram.account.bank.fetch(banks[1]),
      bankrunProgram.account.marginfiAccount.fetch(userAccount),
      getTokenBalance(bankRunProvider, user.lstAlphaAccount),
      getTokenBalance(bankRunProvider, liqVault),
    ]);

    const sharesBefore = wrappedI80F48toBigNumber(bankBefore.totalAssetShares);
    const sharesAfter = wrappedI80F48toBigNumber(bankAfter.totalAssetShares);
    if (verbose) {
      console.log(
        "asset shares before: " + sharesBefore.toString() + " after " + sharesAfter.toString()
      );
      console.log("user before: " + lstBefore + " after: " + lstAfter);
    }

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

**File:** guides/ADMIN/COLLECTING_FEES.md (L60-72)
```markdown
## How Fees Are Collected

(1) Interset accrues and increases the number in `fees_outstanding` fields. Optionally, origination
fees are charged and do the same.

(2) Anyone (the ix is permissionless) runs `LendingPoolCollectBankFees`. This moves:
* The amount specified in `collected_insurance_fees_outstanding` from the `liquidity_vault` to the
  `insurance_vault`
* The amount specified in `collected_group_fees_outstanding` from the `liquidity_vault` to the `fee_vault`
* The amount specified in `collected_program_fees_outstanding` from the `liquidity_vault` to the
    `fee_ata`, which is the cannonical ATA of the global fee wallet.

Then the `fees_outstanding` are all reset to zero. The vast majority of the time, we run this ix just before a withdraw.
```
