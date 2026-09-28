### Title
Donated strategy-token (receipt) balance permanently inflates `getContractValue()`, forcing borrower overpayment or a forced default that freezes and haircuts all LP funds - (File: `contracts/IdleCDOCreditVault.sol`)

### Summary
`IdleCDOCreditVault.getContractValue()` computes NAV as the *raw* ERC20 balance of `strategyToken` plus raw underlying minus `unclaimedFees` (line 127). `_skimDonatedAssets()` in `IdleCDOEpochVariant` only sweeps the underlying `token` (lines 794-796), never `strategyToken`. Since `IdleCreditVault` is a plain `ERC20Upgradeable` with no transfer restriction (the `canTransfer` flag is explicitly "deprecated ... retained for storage compatibility", line 67) and mints freely transferable strategy tokens to users as withdraw receipts (`_mint(_user, _amount)` at lines 275 and 363), any KYC-passing lender can request a withdrawal, receive receipt strategy tokens, and donate them back to the IdleCDO contract. This inflates `getContractValue()` with phantom NAV that is never skimmed, double-counting the receipt's value (it remains an outstanding liability in `pendingWithdraws` while simultaneously re-entering NAV).

### Finding Description
Bug-class mapping: CVE-2021-2384 is an availability/DoS bug (hang/crash of a system component triggered through a reachable input path). The credit-vault analog is an unprivileged attacker permanently corrupting the NAV/interest accounting so that the epoch state machine either (a) overcharges the honest borrower every epoch, or (b) forces `_handleBorrowerDefault`, freezing all deposits and applying a loss-recovery haircut to every LP.

Concrete flow (buffer phase, fixed-APR mode):

1. Attacker deposits, then calls `requestWithdraw(_amount, tranche)` during the buffer. `IdleCreditVault.requestWithdraw` burns the CDO's strategy tokens, mints `_amount` receipt tokens to the attacker (line 275), and increments `pendingWithdraws` (line 279).
2. Attacker calls `strategyToken.transfer(address(cdoEpoch), _amount)` — permitted because the receipt token is unrestricted ERC20.
3. `getContractValue()` now counts `_amount` twice: once in the CDO's raw `strategyToken` balance, once as the still-outstanding `pendingWithdraws` liability (`_skimDonatedAssets` only moves `underlyingToken`, `contracts/IdleCDOEpochVariant.sol:794-796`).
4. `startEpoch()` computes `expectedEpochInterest = pendingWithdrawFees + _calcInterest(getContractValue())` (line 260-262) on the inflated NAV — the borrower is billed interest on phantom principal every epoch, since the donated balance is never removed.
5. At `stopEpoch`, the pull `getFundsFromBorrower(_amountToPullFromBorrower + _pendingWithdraws)` (line 408) demands the inflated amount. If the honest borrower cannot or does not cover the phantom interest, the `catch` path calls `_handleBorrowerDefault` (line 504): `defaulted = true`, `isEpochRunning = false`, withdraw requests disabled, pool paused — freezing all user funds until `finalizeDefault`, which then applies a single aggregate recovery multiplier, i.e. a real haircut to all LPs.
6. Same corruption hits close-pool: `_totBorrowed += _contractTokenBalance(strategyToken)` (line 370) demands phantom principal back from the borrower, so an attempted orderly `stopEpoch(0, 1)` pool closure can be forced into default by the same donation.

The phantom NAV also silently raises tranche prices (`virtualPrice` uses `_managedContractValue()`, line 175-176), so remaining tranche claims exceed real backing — an insolvency invariant break even before default.

### Impact Explanation
- **Forced default / freezing:** an unprivileged lender can push the borrower into `_handleBorrowerDefault`, which freezes all deposits, withdraw requests, and claims until governance finalizes a haircut — temporary freezing plus realized loss for all LPs (recovery multiplier < 1 in `finalizeDefaultRecovery`).
- **Theft from the honest borrower:** every epoch bills interest on the donated phantom balance (`_calcInterest(getContractValue())`); the donation is never skimmed, so the overcharge repeats indefinitely and compounds the insolvency.
- **Insolvency:** `getContractValue()` double-counts donated receipts while `pendingWithdraws` stays payable, so tranche prices are backed by less than reported; last withdrawers or the borrower absorb the shortfall.

Existing guards do not stop it: `_skimDonatedAssets` handles only `token`, not `strategyToken` (`contracts/IdleCDOEpochVariant.sol:794`); `testPocWithdrawDos` (Sherlock fix) covers underlying donations only; and receipt tokens are unrestricted ERC20.

### Likelihood Explanation
Attacker needs only a KYC-passing lender position and a withdraw request — both explicitly in the allowed attacker set. Cost is temporarily locking capital in a receipt that is still claimed back at par; the donation is fully recaptured because it remains in NAV backing the attacker's own claim. Trigger conditions (buffer phase, any APR mode) are routine. Uncertainty I could not fully verify in the available iterations: whether some wrapper guards `strategyToken.transfer` to the CDO address — no `_beforeTokenTransfer`/`_transfer` override was found in `IdleCreditVault.sol`, and `canTransfer` is documented as deprecated, so standard ERC20 transfer applies.

### Recommendation
Track strategy-token backing internally instead of via raw `balanceOf`: e.g., account minted/burned strategy tokens in a dedicated counter used by `getContractValue()`/`_managedContractValue()`, or extend `_skimDonatedAssets()` to sweep `strategyToken` balances above internally tracked liabilities (receipts already burned from CDO must not re-enter NAV). Apply the same fix wherever raw `strategyToken` balance feeds accounting: `startEpoch` interest calc, `_isRequestingAllFunds` recall (line 370), and `finalizeDefault` NAV split (line 210).

### Proof of Concept
```solidity
// test/foundry/StrategyTokenDonationDos.t.sol — fork test, buffer phase, fixed APR
function testStrategyTokenDonationInflatesNavAndForcesDefault() external {
    uint256 amount = 100_000 * ONE_SCALE;
    address attacker = makeAddr('attacker');
    _depositWithUser(attacker, amount, true);            // KYC'd lender deposit
    idleCDO.depositAA(amount);                            // other honest LP

    // Attacker requests withdraw -> receives transferable receipt strategy tokens
    vm.startPrank(attacker);
    uint256 receipt = cdoEpoch.requestWithdraw(0, address(AAtranche));
    IERC20Detailed(strategyToken).transfer(address(cdoEpoch), receipt); // donation
    vm.stopPrank();

    // NAV is double-counted: receipt is still pending AND back in strategyToken balance
    assertGt(cdoEpoch.getContractValue(), 2 * amount - receipt + receipt, 'sanity');
    uint256 nav = cdoEpoch.getContractValue();
    uint256 inflatedInterest = nav * (strategy.getApr() / 100) * cdoEpoch.epochDuration()
        / (365 days * ONE_TRANCHE_TOKEN);

    vm.prank(manager);
    cdoEpoch.startEpoch();
    assertEq(cdoEpoch.expectedEpochInterest(),
        cdoEpoch.pendingWithdrawFees() + inflatedInterest, 'phantom NAV billed to borrower');

    // Honest borrower only covers real liabilities -> pull fails -> forced default
    vm.warp(cdoEpoch.epochEndDate() + 1);
    deal(defaultUnderlying, borrower, receipt);           // enough for real receipt, not phantom interest
    vm.prank(borrower);
    underlying.approve(address(cdoEpoch), receipt);
    vm.prank(manager);
    cdoEpoch.stopEpoch(0, 0);

    assertTrue(cdoEpoch.defaulted(), 'attacker forced borrower default');
    assertFalse(cdoEpoch.allowAAWithdrawRequest(), 'withdrawals frozen');
    // all LP funds frozen until finalizeDefault applies a recovery haircut < 1
}
```