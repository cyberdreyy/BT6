### Title
stopEpoch catch misroutes already-repaid borrower funds to `feeReceiver` as "donations" instead of the default recovery reserve — (File: contracts/IdleCDOEpochVariant.sol)

### Summary
The parisc bug is a fault handler that writes the error value (`-EFAULT`) into the wrong register, silently corrupting unrelated state. The analog in `IdleCDOEpochVariant._stopEpoch` is a fault handler that writes the recovered value into the wrong place: the `try` block wraps not only the borrower pull (`this.getFundsFromBorrower`) but the entire settlement sequence (`collectWithdrawFunds`, `settleBorrowerInterest`, `_updateAccounting`, `deposit`, fee transfers). If any call after the pull reverts, the `catch` treats it as a pure borrower default via `_handleBorrowerDefault`, but — unlike the `startEpoch` catch, which pushes stranded funds into `IdleCreditVault.reserveDefaultRecovery` — it never moves the already-received underlyings to the strategy recovery reserve. They sit as raw underlying on the CDO, and `finalizeDefault` then calls `_skimDonatedAssets()`, which forwards them to `feeReceiver` as "donated assets". Recovered money exists but is written to the wrong register: it never reaches `defaultRecoveryReserve`, so LPs and receipt holders are haircut on a smaller (possibly zero) reserve while the repaid funds are permanently diverted to `feeReceiver`.

### Finding Description
In `IdleCDOEpochVariant._stopEpoch` (contracts/IdleCDOEpochVariant.sol:408-505) the `try` covers the whole post-pull settlement:

```solidity
try this.getFundsFromBorrower(_amountToPullFromBorrower + _pendingWithdraws) {
    _strategy.collectWithdrawFunds(_pendingWithdraws);
    ...
    if (_mintInterest && isProgrammableBorrower) {
        IProgrammableBorrower(_borrower()).settleBorrowerInterest();
    }
    ...
    _updateAccounting();
    ...
    _strategy.deposit(_mintInterest ? 0 : netInterest);
    ...
} catch {
    _handleBorrowerDefault(_amountToPullFromBorrower + _pendingWithdraws);
}
```

`getFundsFromBorrower` executes `_transferUnderlyingsFrom(_borrower(), address(this), _amount)` — the borrower repayment lands **on the CDO**. Every subsequent call is inside the same `try`. If a later step reverts — for example `IProgrammableBorrower.settleBorrowerInterest()` failing because an attacker (a user of the programmable borrower's ERC4626 vault) withdrew vault liquidity or moved its share price — the catch fires even though the borrower fully repaid.

Compare `startEpoch` (contracts/IdleCDOEpochVariant.sol:295-303): its catch explicitly does `_transferUnderlyings(address(_strategy), _toBorrower)` and `_strategy.reserveDefaultRecovery(_toBorrower)`, so stranded funds are counted in `defaultRecoveryReserve` and included by `finalizeDefaultRecovery` at contracts/strategies/idle/IdleCreditVault.sol:686 via `reserveAmount = _recoveredAmount + prefundedReserve + defaultRecoveryReserve`. The `stopEpoch`/`getInstantWithdrawFunds` catches do neither — the asymmetry is the "wrong register" write.

Then `finalizeDefault` (IdleCDOEpochVariant.sol:196-199) calls `_skimDonatedAssets()` (`_transferUnderlyings(feeReceiver, _contractTokenBalance(token))`, line 795) **before** `finalizeDefaultRecovery`. All repaid funds parked on the CDO are swept to `feeReceiver` and permanently excluded from `defaultRecoveryReserve`. `recoveryPrice` is computed over a reserve that omits the real repayment; with zero additional recovery it finalizes at price 0, burning active CDO NAV to zero and letting `_claimDefaultedWithdrawRequest`/`_claimDefaultedInstantWithdrawRequest` clear every receipt at zero payout (IdleCreditVault.sol:772-784, 842-856). Since `defaulted` blocks `unpause`/`restoreOperations`, the stolen yield cannot be re-accounted.

### Impact Explanation
Direct theft/permanent loss equal to the full repayment already pulled from the borrower: `_amountToPullFromBorrower + _pendingWithdraws`, which in close-pool mode (`_interest == 1`) is the **entire pool principal plus interest** (`_totBorrowed += _contractTokenBalance(strategyToken)`). The funds are not lost to the borrower — they were repaid — they are routed to `feeReceiver` while every LP and pending/instant receipt claimant is haircut to (near) zero. This breaks solvency, fair burn, and one-receipt-one-payout invariants. No existing guard stops it: `defaulted` blocks `unpause`/`restoreOperations`, `_skimDonatedAssets` deliberately sweeps raw CDO underlying, and `_transferFundedClaim`'s reserve guard only protects the strategy balance, not funds left on the CDO.

### Likelihood Explanation
The revert does not require a privileged or malicious actor. In programmable-borrower mode (which forces `isInterestMinted`), `settleBorrowerInterest()` is called inside the `try` after funds are pulled; an attacker who is a user of the borrower's ERC4626 vault can drain/lock vault liquidity or move its exchange rate so the settle step reverts — the hooks explicitly acknowledge ERC4626 liquidity failure modes (comment at line 396-397 shows `onStopEpoch` returning false is a handled path, but a *revert inside the try after the pull* is not). Even outside programmable mode, any internal revert after the pull (e.g. `collectWithdrawFunds` reverting on a `lossRecoveryPrice == 0` rounding case at IdleCreditVault.sol:419 during a manager `stopEpochWithDuration` with a large `_lossAmount`) produces the same misrouting. Sequencing only requires an honest manager `stopEpoch` call.

### Recommendation
Mirror the `startEpoch` catch: in the `_stopEpoch` and `getInstantWithdrawFunds` catch blocks, push whatever underlying was actually received (`_contractTokenBalance(token)`) to the strategy and call `reserveDefaultRecovery` before `_handleBorrowerDefault`. Better, narrow the `try` to only the external borrower pull and handle post-pull failures separately, since post-pull reverts are not borrower defaults. At minimum, `finalizeDefault`/`_skimDonatedAssets` should not run before accounting for raw underlying received from the borrower during the failed stop.

### Proof of Concept
```solidity
// Fork test outline (Foundry), programmable-borrower deployment:
function testStopEpochCatchDivertRepaymentToFeeReceiver() external {
    uint256 amount = 100_000 * ONE_SCALE;
    idleCDO.depositAA(amount);            // KYC'd lender
    _startEpochAndCheckPrices(0);

    // Attacker (ERC4626 vault user) drains the programmable borrower's vault
    // liquidity so settleBorrowerInterest() will revert.
    attacker.redeemAllFromBorrowerVault(); // leaves vault unable to settle

    // Borrower honestly approves and repays interest + pendingWithdraws.
    vm.warp(cdoEpoch.epochEndDate() + 1);
    vm.prank(manager);
    cdoEpoch.stopEpoch(0, 0);             // pull succeeds, settle reverts -> catch

    assertTrue(cdoEpoch.defaulted());
    // Repaid funds sit as raw underlying on the CDO (the "wrong register").
    uint256 stranded = underlying.balanceOf(address(cdoEpoch));
    assertGt(stranded, 0);
    assertEq(IdleCreditVault(address(strategy)).defaultRecoveryReserve(), 0);

    // Finalize: skim sends the repayment to feeReceiver as a "donation".
    uint256 feePre = underlying.balanceOf(cdoEpoch.feeReceiver());
    vm.prank(owner);
    cdoEpoch.finalizeDefault(0, address(0));

    assertEq(underlying.balanceOf(cdoEpoch.feeReceiver()), feePre + stranded);
    assertEq(IdleCreditVault(address(strategy)).defaultRecoveryPrice(), 0);
    assertEq(cdoEpoch.virtualPrice(address(AAtranche)), 0);
    // LPs lose the entire repaid amount; feeReceiver gains it — theft of unclaimed yield.
}
```

Caveat: the exact revert vector depends on `ProgrammableBorrower.settleBorrowerInterest()` internals and the ERC4626 vault, which I could not fully inspect in this session; any post-pull revert inside the `try` (including the `collectWithdrawFunds` zero-price rounding revert) produces the identical misrouting, so the core defect stands regardless of the specific trigger.