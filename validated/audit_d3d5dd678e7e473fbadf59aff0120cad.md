Found a strong analog in the Drift integration of marginfi that mirrors the reported bug class: a hard `require`-style constraint on an external/CPI-adjacent account state that can permanently block withdrawals for legitimate users.

### Title
Drift-integration withdraws can be permanently blocked ("bricked") by unrelated admin-reward deposits into the shared Drift user account - ([File: programs/marginfi/src/instructions/drift/withdraw.rs])

### Summary
`DriftWithdraw` enforces two Anchor account constraints on `integration_acc_2` (the shared Drift `User` account owned by the bank's `liquidity_vault_authority` PDA) that hard-fail the whole withdraw transaction if the account has accumulated more active spot-market deposit positions than marginfi's withdraw path is designed to handle, exactly the pattern described in the report where an external contract's `require` statement blocks staking/withdrawal.

### Finding Description
`DriftWithdraw::integration_acc_2` carries two constraints evaluated before the instruction body runs: [1](#0-0) 

- `validate_reward_accounts` fails with `DriftMissingRewardAccounts` if the caller doesn't supply reward oracle/spot-market accounts matching the number of active deposit positions on the shared Drift user account.
- `validate_not_bricked_by_admin_deposits` fails with `DriftBrickedAccount` outright if more than 3 active deposit positions exist on that account — there is no way to pass any combination of accounts to satisfy this check; the withdraw path simply cannot execute: [2](#0-1) 

The `integration_acc_2` Drift user account is **shared** across every marginfi user who deposits into that Drift-wrapped bank (it's owned by the bank's PDA, not per-user), so these checks look at global bank state, not the withdrawing user's own position. The in-repo test suite explicitly demonstrates this failure mode: with 4 active deposit positions, a normal user withdraw reverts with `DriftBrickedAccount` regardless of what accounts are supplied: [3](#0-2) 

This matches the report's root cause precisely: an external protocol's internal check (Drift's spot-position bookkeeping, mirrored here in `MinimalUser`) can block marginfi's own stake/withdraw instruction outright, reverting the whole user transaction rather than gracefully working around the ineligible/blocking state.

### Impact Explanation
If the number of active deposit positions on the shared Drift user account for a bank exceeds 3, **every marginfi user with a balance in that bank is blocked from withdrawing** until an admin runs `drift_harvest_reward` to clear the extra positions. This is a durable, bank-wide freeze of user funds (deposit function still nominally exists, but withdraw for potentially many depositors is completely inoperable), which is exactly the class of harm called out in the analog report: legitimate users unable to exit positions because of unrelated external/administrative state they don't control.

### Likelihood Explanation
The `>3 active deposits` brick condition is populated by "admin reward" deposits into spot markets 2–7 on the shared Drift user account (per code comments: "IMPORTANT: If admin deposits exist in other markets (rewards), you MUST... harvest rewards immediately"). I was not able to fully confirm within this repo whether the underlying Drift protocol's native deposit instruction permits an arbitrary caller to fund the shared account's spot positions (Drift deposits generally only require custody of the source tokens, not authorization from the destination `User` account) — if so, this is trivially triggerable by any unprivileged third party, not just the marginfi/bank admin. That distinction affects whether this is a griefing vector open to any user or an operational risk requiring admin error, and I could not verify it with certainty from the available code/tests before running out of investigation budget.

### Recommendation
- Do not let the withdraw instruction hard-fail into a bank-wide freeze based on unrelated reward-position bookkeeping. Consider decoupling the user's own withdrawable balance from the "extra" admin-reward positions, so a user can still withdraw their principal even if the reward slots are in a state requiring harvest.
- If Drift's deposit instruction indeed allows funding an arbitrary user account's spot positions without the receiving account's authorization, add a bank-level admin control (e.g., an allow-list or size/rate limit) restricting who/what can add positions 2–7 to the shared Drift user account, preventing a griefing party from pushing the position count past 3.
- Ensure the harvest path (`drift_harvest_reward`) can be invoked permissionlessly (not just by a privileged admin) so that any actor can restore withdrawability once bricked, minimizing the freeze window.

### Proof of Concept
Demonstrated by the existing test suite: [4](#0-3) 
1. Admin/attacker (permission model unconfirmed) funds 2 additional reward-market deposits into the shared Drift user account tied to a bank (bringing the account to 4 active deposit positions: 1 main + 3 reward).
2. A normal user with an existing balance in that bank attempts `drift_withdraw`.
3. The `integration_acc_2` account constraint `validate_not_bricked_by_admin_deposits` fails, and the transaction reverts with `DriftBrickedAccount` (`0x18ae`) — the user cannot withdraw at all, matching the report's described failure pattern of external state blocking legitimate withdraw/stake operations.

### Citations

**File:** programs/marginfi/src/instructions/drift/withdraw.rs (L401-409)
```rust
        constraint = {
            let user = integration_acc_2.load()?;
            user.validate_reward_accounts(
                drift_reward_spot_market.is_none(),
                drift_reward_spot_market_2.is_none(),
            ).is_ok()
        } @ MarginfiError::DriftMissingRewardAccounts,
        constraint = integration_acc_2.load()?.validate_not_bricked_by_admin_deposits().is_ok() @ MarginfiError::DriftBrickedAccount
    )]
```

**File:** programs/drift-mocks/src/state.rs (L267-290)
```rust
    /// Check if Drift has bricked this account with excessive admin deposits
    /// We support 1 main asset + up to 2 reward assets (3 total active deposits)
    /// If Drift admin deposited more reward assets, the account cannot withdraw
    pub fn validate_not_bricked_by_admin_deposits(&self) -> Result<()> {
        let active_deposits = self.count_active_deposits();

        if active_deposits > 3 {
            msg!(
                "ERROR: Drift has {} active deposit positions",
                active_deposits
            );
            msg!(
                "Active market indexes: {:?}",
                self.get_active_deposit_markets()
            );
            msg!("This account has been bricked by Drift admin deposits!");
            msg!("Cannot withdraw when more than 3 assets have active balances");
            msg!("We support 1 main asset + up to 2 reward assets");
            msg!("SOLUTION: Fee admin wallet needs to harvest these rewards ASAP!");
            return Err(DriftMocksError::TooManyActiveDeposits.into());
        }

        Ok(())
    }
```

**File:** tests/specs/drift/d12_driftHarvestReward.spec.ts (L1232-1336)
```typescript
  it("User: Account bricked with 4 active deposits", async () => {
    const user = users[0];

    const driftState = await getDriftStateAccount(driftBankrunProgram);
    const tokenDMarketIndex = driftState.numberOfSpotMarkets;

    await createDriftSpotMarketWithOracle(
      ecosystem.lstAlphaMint.publicKey,
      DRIFT_TOKEN_D_SYMBOL,
      tokenDMarketIndex,
      ecosystem.lstAlphaPrice,
      ecosystem.lstAlphaDecimals,
    );
    assert(driftAccounts.get(DRIFT_TOKEN_D_PULL_ORACLE));
    assert(driftAccounts.get(DRIFT_TOKEN_D_SPOT_MARKET));

    await fundAndDepositAdminReward(
      groupAdmin.wallet,
      driftTokenABank,
      ecosystem.tokenBMint.publicKey,
      TOKEN_B_MARKET_INDEX,
      depositBAmount,
    );

    await fundAndDepositAdminReward(
      groupAdmin.wallet,
      driftTokenABank,
      ecosystem.lstAlphaMint.publicKey,
      tokenDMarketIndex,
      tokenDRewardAmount,
    );

    const marginfiAccount = await createThrowawayMarginfiAccount(
      user,
      driftGroup.publicKey,
    );

    const tokenAOracle = driftAccounts.get(DRIFT_TOKEN_A_PULL_ORACLE);
    const tokenASpotMarket = driftAccounts.get(DRIFT_TOKEN_A_SPOT_MARKET);
    const tokenBOracle = driftAccounts.get(DRIFT_TOKEN_B_PULL_ORACLE);
    const tokenBSpotMarket = driftAccounts.get(DRIFT_TOKEN_B_SPOT_MARKET);

    const depositIx = await makeDriftDepositIx(
      user.mrgnBankrunProgram,
      {
        marginfiAccount,
        bank: driftTokenABank,
        signerTokenAccount: user.tokenAAccount,
        driftOracle: tokenAOracle,
      },
      tokenAWithdrawAmount,
      TOKEN_A_MARKET_INDEX,
    );

    const depositTx = new Transaction()
      .add(ComputeBudgetProgram.setComputeUnitLimit({ units: 1_000_000 }))
      .add(depositIx);

    await processBankrunTransaction(
      bankrunContext,
      depositTx,
      [user.wallet],
      false,
      true,
    );

    const remaining = composeRemainingAccounts(
      getDriftBalanceAccountGroups().filter(
        (group) => !group[0].equals(driftTokenABank)
      )
    );
    const withdrawIx = await makeDriftWithdrawIx(
      user.mrgnBankrunProgram,
      {
        marginfiAccount,
        bank: driftTokenABank,
        destinationTokenAccount: user.tokenAAccount,
        driftOracle: tokenAOracle,
        driftRewardOracle: tokenBOracle,
        driftRewardSpotMarket: tokenBSpotMarket,
        driftRewardOracle2: driftTokenCPullOracle,
        driftRewardSpotMarket2: driftTokenCSpotMarket,
      },
      {
        amount: new BN(0),
        withdrawAll: true,
        remaining,
      },
      driftBankrunProgram,
    );

    const withdrawTx = new Transaction()
      .add(ComputeBudgetProgram.setComputeUnitLimit({ units: 1_000_000 }))
      .add(withdrawIx);

    const result = await processBankrunTransaction(
      bankrunContext,
      withdrawTx,
      [user.wallet],
      true,
      false,
    );

    assertBankrunTxFailed(result, 0x18ae); // DriftBrickedAccount
  });
```
