### Title
Instant withdrawals are not triggered at the configured APR-delta boundary - (File: contracts/IdleCDOEpochVariant.sol)

### Summary

`requestWithdraw` routes a lender through instant withdrawal only when `lastEpochApr > currentApr + instantWithdrawAprDelta`, so an APR decrease exactly equal to the configured minimum delta is treated as a normal withdrawal request instead of an instant withdrawal request. [1](#0-0) 

### Finding Description

`instantWithdrawAprDelta` is defined as the minimum APR change needed to trigger instant withdrawals. [2](#0-1)  The strict `>` comparison makes the actual activation condition `lastEpochApr - currentApr > instantWithdrawAprDelta`, rather than `lastEpochApr - currentApr >= instantWithdrawAprDelta`. [3](#0-2) 

At the boundary, execution falls through to the normal withdrawal path, creates a funded-at-next-stop receipt, and records the request in `pendingWithdraws`. [4](#0-3)  A normal receipt cannot be claimed while its request epoch is still the current strategy epoch. [5](#0-4)  By contrast, instant withdrawals are funded when the next epoch starts and become claimable once `allowInstantWithdraw` is enabled. [6](#0-5) 

### Impact Explanation

A KYC-passing lender requesting withdrawal immediately after the honest manager sets the next APR exactly `instantWithdrawAprDelta` lower has the full requested amount routed into the normal withdrawal queue rather than the instant-withdrawal queue. [7](#0-6) 

The quantified impact is temporary freezing of 100% of that lender’s requested underlying amount for an additional epoch cycle: the instant path can be funded at `startEpoch`, while the normal path must wait until the subsequent `stopEpoch` and associated funding collection. [6](#0-5) [8](#0-7) 

### Likelihood Explanation

The trigger is a precise boundary condition: `currentApr` must equal `lastEpochApr - instantWithdrawAprDelta`. [9](#0-8)  APR changes and the threshold are configured by an honest owner or manager, so the attacker cannot force the boundary, but the attacker is only required to time an ordinary `requestWithdraw` transaction around the privileged epoch transition. [10](#0-9) [11](#0-10) 

### Recommendation

Change the instant-withdrawal boundary check to include equality:

```solidity
// contracts/IdleCDOEpochVariant.sol
if (lastEpochApr >= currentApr + instantWithdrawAprDelta) {
    creditVault.requestInstantWithdraw(_underlyings, msg.sender);
    _withdrawOps(_amount, _underlyings, _tranche);
    return _underlyings;
}
```

This preserves the existing no-instant-withdrawal behavior when the decrease is below `instantWithdrawAprDelta`, while making the documented minimum delta actually activate instant withdrawals. [2](#0-1) 

### Proof of Concept

The following Foundry fork test expects a configured, currently running `IdleCDOEpochVariant`, a KYC-passing lender that already holds AA tranche tokens, and borrower funding sufficient for the honest stop transaction. It demonstrates that the equality boundary produces `pendingWithdraws` instead of `pendingInstantWithdraws`.

```solidity
// test/foundry/InstantWithdrawBoundaryFork.t.sol
// SPDX-License-Identifier: UNLICENSED
pragma solidity 0.8.10;

import "forge-std/Test.sol";
import {IERC20Detailed} from "contracts/interfaces/IERC20Detailed.sol";
import {IdleCDOEpochVariant} from "contracts/IdleCDOEpochVariant.sol";
import {IdleCreditVault} from "contracts/strategies/idle/IdleCreditVault.sol";

contract InstantWithdrawBoundaryForkTest is Test {
    function testInstantWithdrawNotTriggeredAtExactDelta() external {
        IdleCDOEpochVariant cdo = IdleCDOEpochVariant(vm.envAddress("CDO"));
        IdleCreditVault strategy = IdleCreditVault(cdo.strategy());
        IERC20Detailed underlying = IERC20Detailed(cdo.token());
        IERC20Detailed aaTranche = IERC20Detailed(cdo.AATranche());

        address lender = vm.envAddress("KYC_AA_LENDER");
        address manager = strategy.manager();
        address borrower = strategy.borrower();

        require(cdo.isEpochRunning(), "fork requires running epoch");
        require(cdo.isWalletAllowed(lender), "lender must pass KYC");
        require(aaTranche.balanceOf(lender) != 0, "lender must hold AA tranches");

        uint256 oldApr = strategy.unscaledApr();
        uint256 delta = cdo.instantWithdrawAprDelta();
        require(oldApr > delta, "boundary requires oldApr > delta");

        // Honest manager sets the next APR to exactly the configured lower boundary.
        uint256 boundaryApr = oldApr - delta;
        uint256 borrowerPayment =
            cdo.expectedEpochInterest() + strategy.pendingWithdraws();

        deal(address(underlying), borrower, borrowerPayment);
        vm.prank(borrower);
        underlying.approve(address(cdo), borrowerPayment);

        vm.prank(manager);
        cdo.stopEpoch(boundaryApr, 0);

        assertEq(strategy.unscaledApr(), boundaryApr, "boundary APR not set");
        assertEq(cdo.lastEpochApr(), oldApr, "old APR checkpoint mismatch");

        uint256 pendingInstantBefore = strategy.pendingInstantWithdraws();
        uint256 pendingNormalBefore = strategy.pendingWithdraws();

        vm.prank(lender);
        uint256 requested = cdo.requestWithdraw(0, cdo.AATranche());

        assertGt(requested, 0, "test requires non-zero withdrawal");
        assertEq(
            strategy.pendingInstantWithdraws(),
            pendingInstantBefore,
            "equality boundary incorrectly avoided instant withdrawal"
        );
        assertEq(
            strategy.pendingWithdraws(),
            pendingNormalBefore + requested,
            "request was routed to delayed normal withdrawal"
        );

        // The normal receipt remains unclaimable during its request epoch.
        vm.prank(lender);
        vm.expectRevert();
        cdo.claimWithdrawRequest();
    }
}
```

Run with:

```bash
CDO=<cdo> KYC_AA_LENDER=<holder> \
forge test --fork-url "$RPC_URL" --mt testInstantWithdrawNotTriggeredAtExactDelta -vvv
```

### Citations

**File:** contracts/IdleCDOEpochVariant.sol (L35-39)
```text
  /// @notice apr of the last epoch, unscaled
  uint256 public lastEpochApr;
  /// @notice min apr change to trigger instant withdraw
  uint256 public instantWithdrawAprDelta;
  /// @notice fees from pending withdraw request for the curr epoch
```

**File:** contracts/IdleCDOEpochVariant.sol (L127-137)
```text
  /// @notice update instant withdraw params
  /// @param _delay delay in seconds
  /// @param _aprDelta min apr delta to trigger instant withdraw
  /// @param _disable flag to disable instant withdraw
  function setInstantWithdrawParams(uint256 _delay, uint256 _aprDelta, bool _disable) public virtual {
    _checkOnlyOwnerOrManager();
    _checkNotAllowed(paused());
    instantWithdrawDelay = _delay;
    instantWithdrawAprDelta = _aprDelta;
    disableInstantWithdraw = _disable;
  }
```

**File:** contracts/IdleCDOEpochVariant.sol (L276-292)
```text
    // we should first check if there are *instant* redeem requests pending 
    // and if yes we should send as much underlyings as possible to the IdleCreditVault contract
    // if there is any surplus then we send those to the borrower
    uint256 pendingInstant = _pendingInstant();
    uint256 totUnderlyings = _contractTokenBalance(token);
    uint256 _pendingWithdraws = _strategy.pendingWithdraws();
    _strategy.collectInstantWithdrawFunds(pendingInstant > totUnderlyings ? totUnderlyings : pendingInstant);

    // if there are more requests than the current underlyings we simply send all underlyings
    // to the IdleCreditVault contract
    if (pendingInstant > totUnderlyings) {
      // if borrower is programmable, notify epoch start even if no funds were sent
      _startEpochProgrammableBorrower(_pendingWithdraws);
      return;
    }
    // allow instant withdraws right away without waiting for the deadline
    allowInstantWithdraw = true;
```

**File:** contracts/IdleCDOEpochVariant.sol (L739-744)
```text
  function requestWithdraw(uint256 _amount, address _tranche) external returns (uint256 _underlyings) {
    _checkTranche(_tranche);
    _checkNotAllowed(
      (_tranche == AATranche ? !allowAAWithdrawRequest : !allowBBWithdrawRequest) ||
      !isWalletAllowed(msg.sender)
    );
```

**File:** contracts/IdleCDOEpochVariant.sol (L758-790)
```text
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L319-330)
```text
  function _claimFundedWithdrawRequest(address _user) internal returns (uint256 amount) {
    // User should wait at least an epoch before claiming the withdraw. Once the epoch is over user can withdraw 
    // at any time even if a new epoch started. 
    // So if epochNumber is the same as the last withdraw request then we revert. Epoch number is increased at stopEpoch
    // NOTE: If a user does not claim a withdraw request and instead requests another withdraw, he will have to wait
    // for another epoch to claim both requests.
    // NOTE 2: if borrower defaults, old withdraw requests can still be claimed
    if (IIdleCDOEpochVariant(idleCDO).epochEndDate() != 0 && (epochNumber <= lastWithdrawRequest[_user])) {
      revert NotAllowed();
    }
    // settle APR=0 requests once the related epoch has ended
    _settleApr0(_user);
```
