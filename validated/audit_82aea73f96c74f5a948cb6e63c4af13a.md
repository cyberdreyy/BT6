### Title
Fee-on-transfer borrower repayment causes false borrower default and locked epoch funds - (File: contracts/IdleCDOEpochVariant.sol)

### Summary
`IdleCDOEpochVariant._stopEpoch` pulls a gross `_amount` of underlying from the borrower via `getFundsFromBorrower` and then pushes the same gross amount into the `IdleCreditVault` via `collectWithdrawFunds` / `_strategy.deposit(netInterest)`. If `token` ever charges a fee on transfer (e.g., USDT with fees enabled), the CDO receives `amount - fee` but tries to forward `amount`. The second `safeTransferFrom` reverts, the `try/catch` treats an honest borrower as defaulted, and the vault is forced into the default/finalization path with funds stranded in the CDO. The same gross-amount assumption makes `finalizeDefaultRecovery` overstate `defaultRecoveryReserve`, so the last default claimants can never be paid.

### Finding Description
The epoch settlement flow assumes that the amount debited from the borrower equals the amount credited to the CDO:

1. `getFundsFromBorrower(_amountToPullFromBorrower + _pendingWithdraws)` performs `safeTransferFrom(borrower, CDO, _amount)` (`contracts/IdleCDOEpochVariant.sol:550-553`).
2. On success, `_strategy.collectWithdrawFunds(_pendingWithdraws)` decrements `pendingWithdraws` by the full amount and then does `safeTransferFrom(idleCDO, strategy, _amount)` (`contracts/strategies/idle/IdleCreditVault.sol:411-429`), and `_strategy.deposit(netInterest)` again pulls a fixed amount (`contracts/IdleCDOEpochVariant.sol:466`).

With a fee-on-transfer underlying, step 1 succeeds (the borrower did repay) but the CDO's balance is `_amount - fee`, so step 2's pull of the full `_pendingWithdraws`/`netInterest` reverts. Execution falls into the `catch` branch and calls `_handleBorrowerDefault(_amountToPullFromBorrower + _pendingWithdraws)` (`contracts/IdleCDOEpochVariant.sol:501-505`), which sets `defaulted = true`, pauses the contract, stops the epoch, and disables withdraw requests — even though the borrower fully repaid. The partially received underlying sits in the CDO while accounting treats the borrower as defaulted.

The same bug class appears in `IdleCreditVault.finalizeDefaultRecovery`: `defaultRecoveryReserve` is set to the nominal `reserveAmount = _recoveredAmount + prefundedReserve + defaultRecoveryReserve` *before* pulling `_recoveredAmount` via `safeTransferFrom` (`contracts/strategies/idle/IdleCreditVault.sol:686-708`). With a fee, the strategy holds less than `defaultRecoveryReserve`, so the final `_transferDefaultRecovery` calls underflow/revert, permanently locking the tail of recovery claims.

Note the deposit path `_deposit` correctly mints on the balance delta (`contracts/IdleCDOCreditVault.sol:203-206`), but the subsequent `IIdleCDOStrategy(strategy).deposit(_amount)` still pulls the gross amount, compounding the mismatch.

### Impact Explanation
Honest borrower repayment triggers a spurious `_handleBorrowerDefault`, freezing the vault (deposits, withdraw requests, instant claims all disabled) until governance performs `finalizeDefault`. All funds received net of fee remain stranded because recovery accounting is denominated in the gross, never-received amounts. In `finalizeDefaultRecovery` the overstated `defaultRecoveryReserve` makes the last claimants' `_transferDefaultRecovery` revert permanently — a direct loss of up to `fee%` of every pulled amount per claimant at the tail. This is permanent freezing / insolvency of the recovery reserve, quantified by the token's transfer fee applied to each borrower pull.

### Likelihood Explanation
Requires the vault's `token` to charge transfer fees (USDT fee mode, or any fee-on-transfer stable chosen at init). No privileged misbehavior is needed: the honest borrower repays and approves exactly the required amount, and the revert/false default is triggered by the code's gross-amount assumption alone. Once triggered, every subsequent `stopEpoch` or `getInstantWithdrawFunds` retry hits the same catch path, so the freeze persists.

### Recommendation
Use balance-delta accounting for all inbound pulls, mirroring `_deposit`:
- In `getFundsFromBorrower`/stop flow, measure `balanceOf(CDO)` before and after the borrower `safeTransferFrom` and forward only the *received* amount to `collectWithdrawFunds` and `_strategy.deposit`, or have `collectWithdrawFunds` pull `min(amount, actualReceived)` and let the shortfall flow into the loss/pending accounting rather than reverting into the default catch.
- In `finalizeDefaultRecovery`, pull `_recoveredAmount` first and set `defaultRecoveryReserve` from the actual received delta, not the nominal parameter.
- Alternatively, explicitly whitelist non-fee-on-transfer underlyings at init and document the invariant.

### Proof of Concept
Foundry fork/setup sketch (modeled on `test/foundry/IdleCreditVault.t.sol` helpers):

```solidity
// 1. Deploy IdleCDOEpochVariant + IdleCreditVault with a fee-on-transfer ERC20
//    (e.g. 1% fee token mock as `underlying`; or a fork where USDT fee params are set).
// 2. Deposit: idleCDO.depositAA(10_000e6) -> mints on delta (9_900), but
//    strategy.deposit(_amount) pulls 10_000 -> already drains prior balance or reverts.
// 3. manager.startEpoch(): sendFundsToBorrower moves funds to borrower.
// 4. warp past epochEndDate. Borrower approves and is dealt exactly
//    expectedInterest + pendingWithdraws.
// 5. manager.stopEpoch(0, interest):
//    - getFundsFromBorrower(X) succeeds; CDO balance = X * 99 / 100.
//    - collectWithdrawFunds(pendingWithdraws) -> safeTransferFrom(CDO, strategy, X)
//      reverts (insufficient balance).
//    - catch branch -> _handleBorrowerDefault(X): defaulted == true, paused, requests closed
//      despite the borrower having repaid in full.
assertEq(cdoEpoch.defaulted(), true);            // false default on honest repayment
assertGt(underlying.balanceOf(address(cdoEpoch)), 0); // funds stranded in CDO

// 6. finalizeDefaultRecovery(r, source): defaultRecoveryReserve is set to nominal
//    reserveAmount while the safeTransferFrom delivers r * 99 / 100; the last
//    _transferDefaultRecovery call reverts -> tail recovery claims permanently locked.
```

No privileged attacker is involved; the manager/owner execute the normal stop sequence and the bug is triggered solely by the token's transfer fee.