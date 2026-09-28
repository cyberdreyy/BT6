### Title
`depositDuringEpoch` mints shares at an internally computed price with no `minSharesOut` bound, allowing front-running dilution - ([File: contracts/IdleCDOEpochVariant.sol](contracts/IdleCDOEpochVariant.sol))

### Summary
The Arrakis report describes a `mint` where the amounts pulled from the depositor are computed entirely from mutable on-chain state with no user-supplied bound, so a front-runner can change the terms after the victim signed. The same class exists in `IdleCDOEpochVariant.depositDuringEpoch`: the caller fixes `_amount`, but the number of tranche tokens minted is derived from `expectedEpochInterest`, `pendingWithdrawFees`, `lastNAVAA`/`lastNAVBB` and the live tranche supply — all of which an unprivileged, KYC-passed lender can shift in the same block before the victim's transaction executes. There is no `minSharesOut` parameter.

### Finding Description
In `depositDuringEpoch` (`contracts/IdleCDOEpochVariant.sol:656-733`), the minted amount is:

```solidity
uint256 expectedFinal = _lastSavedNAV(_tranche) + trancheExpected;
_minted = (_amount + trancheInterest) * _trancheTotSupply / expectedFinal;
```

where `trancheExpected` is the victim tranche's share of `expectedEpochInterest - pendingWithdrawFees` (lines 699-724). Both terms are attacker-malleable in the running-epoch phase (fixed-APR mode, `isDepositDuringEpochDisabled == false`, `isAYSActive == false`, non-programmable):

- An attacker calling `depositDuringEpoch` on the same tranche first increases `expectedEpochInterest` (line 728) and `lastNAV`, inflating `trancheExpected` and `expectedFinal`, so the victim receives strictly fewer shares than the fair mid-epoch price for the same `_amount`.
- The shortfall is not a pricing artifact: at `stopEpoch` the victim's shares redeem less than `_amount + trancheInterest`, and the difference is distributed pro-rata over the enlarged supply — a fraction of which is captured by the attacker's own minted shares.
- Conversely, an attacker holding tranche tokens can call `requestWithdraw` (lines 739-778) to raise `pendingWithdrawFees`, zeroing `trancheExpected` and shifting the mint in the other direction, which can be used offensively in a sandwich (withdraw before, re-deposit after) around a target deposit to skew which side bears rounding/interest-share loss.

The depositor cannot defend themselves: there is no slippage parameter, and `isWalletAllowed`/KYC does not stop an attacker who is also an allowed lender — an explicitly permitted actor per the threat model.

### Impact Explanation
Broken invariant: fair mint. A mid-epoch depositor signs a transaction for `_amount` underlyings but the consideration (shares) is computed from state an unprivileged user can worsen in the same block. The victim's loss equals the share shortfall times the epoch-end tranche price, bounded by the interest-share distortion the attacker can inject; the attacker captures a pro-rata fraction of that shortfall while still earning their own legitimate epoch interest. This is direct extraction of depositor value, the same impact class as the referenced report.

### Likelihood Explanation
Requires `depositDuringEpoch` to be enabled (owner flag) during a running epoch and an attacker who passes `isWalletAllowed`. Both are normal operating conditions; mid-epoch deposits are an advertised feature. The attacker needs capital in the same tranche to move `expectedFinal` meaningfully, which tempers magnitude but not the missing-protection defect.

### Recommendation
Add a `minSharesOut` parameter to `depositDuringEpoch` and revert if `_minted < minSharesOut` (mirroring the `maxAmount0In/maxAmount1In` fix recommended in the external report). Optionally also bound the `trancheExpected` term against a caller-supplied reference.

### Proof of Concept
Reproducible Foundry fork PoC, extending the existing harness in `test/foundry/IdleCreditVault.t.sol` (`testDepositDuringEpochNumericalAA`, lines 1591-1648):

```solidity
// Setup (as in testDepositDuringEpochNumericalAA):
// 365-day epoch, 10% APR, 1000 USDC initial AA deposit, epoch started,
// warp to mid-epoch, isDepositDuringEpochDisabled = false.

uint256 depositAmount = 1000 * ONE_SCALE;
address victim  = makeAddr('victim');
address attacker = makeAddr('attacker'); // also KYC-allowed
deal(defaultUnderlying, victim, depositAmount);
deal(defaultUnderlying, attacker, attackAmount);

// Baseline: victim mints expectedMinted = 954.545...e18 (from the test).
// Attack: attacker front-runs with depositDuringEpoch on AATranche.
vm.startPrank(attacker);
IERC20Detailed(defaultUnderlying).approve(address(cdoEpoch), attackAmount);
cdoEpoch.depositDuringEpoch(attackAmount, address(AAtranche)); // raises expectedEpochInterest + lastNAVAA
vm.stopPrank();

vm.startPrank(victim);
IERC20Detailed(defaultUnderlying).approve(address(cdoEpoch), depositAmount);
uint256 minted = cdoEpoch.depositDuringEpoch(depositAmount, address(AAtranche));
vm.stopPrank();

// Assert: minted < 954545454545454545454 — victim received fewer shares for
// identical deposit and remaining time, purely due to the front-run.
assertLt(minted, 954545454545454545454);
// At stopEpoch, victim's minted * virtualPrice < 1050e18; the shortfall is
// distributed pro-rata, partially to attacker's shares.
```

The victim's transaction does not revert and cannot express a minimum acceptable mint, confirming the missing-bound defect.