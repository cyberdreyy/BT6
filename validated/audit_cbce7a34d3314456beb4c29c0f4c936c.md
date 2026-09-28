### Title
Pending BB withdrawals escape the first-loss waterfall and socialize realized losses to AA - ([File: contracts/strategies/idle/IdleCreditVault.sol](contracts/strategies/idle/IdleCreditVault.sol))

### Summary
`IdleCreditVault.requestWithdraw` removes BB principal from active CDO NAV and converts it into an aggregate pending receipt without preserving its junior tranche identity. When an epoch is later stopped with a realized loss, `previewLossAdjustedWithdrawFunds` assigns losses to the pending bucket pro rata with active LPs instead of exhausting BB claims first. A BB holder can therefore request withdrawal during the buffer phase and convert junior capital into an epoch receipt before a loss is crystallized. The remaining loss is then applied to active AA holders, breaking the BB-first loss waterfall and allowing the BB holder to recover funds that should have been lost.

### Finding Description
During `requestWithdraw`, the strategy burns the CDO-held strategy-token principal, mints an equivalent receipt to the user, and adds the claim to the global `pendingWithdraws` bucket. [1](#0-0) 

The pending bucket does not retain whether the claim originated from AA or BB. On `stopEpochWithDuration`, `_stopEpoch` calls `previewLossAdjustedWithdrawFunds` before pulling borrower funds. [2](#0-1) 

When both active NAV and pending receipts exist, the strategy computes:

```solidity
pendingLoss = _lossAmount * pendingBasis / totalBasis;
pendingToFund = pendingBasis - pendingLoss;
activeLoss = _lossAmount - pendingLoss;
``` [3](#0-2) 

This is inconsistent with the normal credit-vault waterfall, where active losses exhaust BB before reducing AA. [4](#0-3) 

`collectWithdrawFunds` stores only the aggregate recovery price for the pending epoch, while `_claimLossAdjustedWithdrawRequest` later pays each receipt according to that shared haircut. [5](#0-4) [6](#0-5) 

### Impact Explanation
The BB holder permanently escapes part or all of a loss that junior capital was supposed to absorb.

Example with zero epoch interest:

- Active AA NAV after BB withdrawal request: `100`.
- Pending BB withdrawal basis: `50`.
- Realized epoch loss: `50`.

Correct tranche waterfall:

- BB loses the full `50`.
- AA loses `0`.
- BB receipt payout should be `0`.

Actual result:

```text
pendingLoss = 50 * 50 / (100 + 50) = 16.666...
pendingToFund = 33.333...
activeLoss    = 33.333...
```

The former BB holder receives approximately `33.333` underlying despite holding the first-loss tranche. Active AA holders absorb the remaining `33.333` loss even though the full `50` loss should have been covered by junior capital. This is a direct redistribution of `33.333` underlying from AA holders to the queued BB holder and breaks vault solvency accounting relative to the promised waterfall.

### Likelihood Explanation
The attack only requires an unprivileged BB tranche holder to call `requestWithdraw` during the buffer phase before the next epoch starts. Withdrawal requests are open outside a running epoch and are only disabled once `startEpoch` executes. [7](#0-6) 

The manager’s later `stopEpochWithDuration` call is assumed to be honest and merely crystallizes a real borrower loss. The vulnerability is in how the protocol prices the attacker’s already-queued receipt, not in a malicious privileged action. The likelihood depends on the BB holder anticipating elevated loss or default risk before requesting withdrawal, but no special permissions, oracle manipulation, reentrancy, or invalid privileged input is needed.

### Recommendation
Preserve tranche identity for pending withdrawal claims, such as separate `pendingWithdrawsAA` and `pendingWithdrawsBB` buckets. Apply `_lossAmount` in this order:

1. Pending BB receipts and active BB NAV.
2. Pending AA receipts.
3. Active AA NAV only after all junior claims are exhausted.

At minimum, pending BB receipts should be haircut before any active AA NAV is burned. If per-tranche pending claims cannot be tracked under the current storage layout, loss realization should be disabled while mixed pending receipts exist, or pending claims should be migrated to tranche-aware accounting.

### Proof of Concept
The following Foundry test can be added to the existing `IdleCreditVault.t.sol` fork-test setup. It assumes an APR-zero epoch so the basis arithmetic is exact apart from rounding.

```solidity
function testPendingBBEscapesLossWaterfall() external {
    uint256 aaDeposit = 100e18;
    uint256 bbDeposit = 50e18;
    uint256 lossAmount = 50e18;

    // Buffer phase: create an active senior position and a junior position.
    idleCDO.depositAA(aaDeposit);
    idleCDO.depositBB(bbDeposit);

    // Attacker converts all BB principal into an aggregate pending receipt.
    uint256 bbReceipt = BBtranche.balanceOf(address(this));
    uint256 pendingBasis = cdoEpoch.requestWithdraw(bbReceipt, address(BBtranche));

    assertEq(pendingBasis, bbDeposit);
    assertEq(IdleCreditVault(address(strategy)).pendingWithdraws(), pendingBasis);
    assertEq(cdoEpoch.lastNAVAA(), aaDeposit);
    assertEq(cdoEpoch.lastNAVBB(), 0);

    // Honest manager starts the epoch.
    _startEpochAndCheckPrices(0);

    // A real 50-underlying loss is discovered at epoch end.
    vm.warp(cdoEpoch.epochEndDate() + 1);

    // Expected vulnerable split:
    // pendingLoss = 50 * 50 / 150 = 16.666...
    // pendingToFund = 33.333...
    // activeLoss = 33.333...
    uint256 expectedPendingToFund = pendingBasis * 2 / 3;

    // Honest borrower funds only the loss-adjusted pending amount.
    deal(defaultUnderlying, borrower, expectedPendingToFund);
    vm.prank(borrower);
    IERC20Detailed(defaultUnderlying).approve(address(cdoEpoch), expectedPendingToFund);

    // Honest manager crystallizes the real loss.
    vm.prank(manager);
    cdoEpoch.stopEpochWithDuration(0, 0, 30 days, lossAmount);

    // The pending BB receipt is only haircut by one third.
    uint256 balanceBefore = IERC20Detailed(defaultUnderlying).balanceOf(address(this));
    cdoEpoch.claimWithdrawRequest();
    uint256 bbPayout =
        IERC20Detailed(defaultUnderlying).balanceOf(address(this)) - balanceBefore;

    assertApproxEqAbs(bbPayout, expectedPendingToFund, 2);

    // Correct BB-first waterfall would pay zero for a 50 loss against 50 BB basis.
    // Vulnerability: the former BB holder receives about 33.333 underlying.
    assertGt(bbPayout, 0);

    // Active AA absorbed the remaining loss even though junior claim basis existed.
    assertApproxEqAbs(
        cdoEpoch.virtualPrice(address(AAtranche)),
        uint256(2e18) / 3,
        3
    );
}
```

The assertions demonstrate both halves of the exploit: the queued BB holder receives approximately `33.333` underlying, while AA’s virtual price falls to approximately `0.6667`, instead of BB paying `0` and AA remaining whole.

### Citations

**File:** contracts/strategies/idle/IdleCreditVault.sol (L272-280)
```text
    // burn strategy tokens from cdo (we don't burn future interest here, only the principal)
    _burn(msg.sender, _principal);
    // mint equal amount of strategy tokens to the user as receipt (interest included), useful in case of default
    _mint(_user, _amount);
    // A successfully closed pool already recalled all funds and has no later stopEpoch.
    if (!isClosed) {
      // Global amount that stopEpoch must source from borrower/strategy for all pending receipts.
      pendingWithdraws += _amount;
    }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L414-421)
```text
    if (_amount < pendingBasis) {
      // Legacy receipts do not have per-epoch ownership data, so they can only be fully funded.
      if (!defaultRecoveryInitialized) revert NotAllowed();
      uint256 lossRecoveryPrice = _amount * RECOVERY_FULL / pendingBasis;
      // Avoid storing a zero price, which is indistinguishable from "no loss-adjusted epoch".
      if (lossRecoveryPrice == 0) revert NotAllowed();
      pendingWithdraws = 0;
      lossRecoveryPriceByEpoch[epochNumber] = lossRecoveryPrice;
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L454-459)
```text
    uint256 totalBasis = activeBasis + pendingBasis;
    if (_lossAmount >= totalBasis) revert NotAllowed();

    uint256 pendingLoss = _lossAmount * pendingBasis / totalBasis;
    pendingToFund = pendingBasis - pendingLoss;
    activeLoss = _lossAmount - pendingLoss;
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L789-800)
```text
  function _claimLossAdjustedWithdrawRequest(address _user) internal returns (uint256 amount) {
    uint256 lossEpoch = lastWithdrawRequest[_user];
    uint256 lossRecoveryPrice = lossRecoveryPriceByEpoch[lossEpoch];
    if (lossRecoveryPrice == 0) return amount;

    (uint256 claimBasis, uint256 burnAmount) = _clearWithdrawClaimForEpoch(_user, lossEpoch, false);
    if (claimBasis == 0) return amount;

    // pendingWithdraws was already cleared when the borrower funded the loss-adjusted amount.
    _burn(_user, burnAmount);
    amount = (claimBasis * lossRecoveryPrice) / RECOVERY_FULL;
    _transferFundedClaim(_user, amount);
```

**File:** contracts/IdleCDOEpochVariant.sol (L244-250)
```text
    isEpochRunning = true;
    // prevent deposits
    _pause();

    // prevent withdrawals requests
    allowAAWithdrawRequest = false;
    allowBBWithdrawRequest = false;
```

**File:** contracts/IdleCDOEpochVariant.sol (L391-393)
```text
    // Pending receipts have no tranche identity, so their aggregate loss is pro rata across all
    // receipts. The remaining active loss is applied BB-first after the stop succeeds.
    (_pendingWithdraws, _lossAmount) = _strategy.previewLossAdjustedWithdrawFunds(_lossAmount);
```

**File:** contracts/IdleCDOCreditVault.sol (L238-245)
```text
    // Ordinary losses exhaust BB before reducing AA. Stop normal interactions once BB is wiped,
    // or when an AA-only vault is fully wiped, so the loss must be crystallized explicitly.
    if ((_totalBBGain < 0 && -_totalBBGain >= int256(_lastNAVBB)) || (_lastNAV != 0 && nav == 0)) {
      shutdown = true;
      if (!skipDefaultCheck) revert Default();
      // Keep a total wipe distinguishable from an uninitialized vault when no BB NAV existed.
      if (nav == 0) _priceAA = 0;
      _emergencyShutdown(true);
```
