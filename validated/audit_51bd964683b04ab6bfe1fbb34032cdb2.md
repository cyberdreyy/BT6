### Title
APR0 withdrawal funding uses aggregate interest while claims use a truncated rate - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`IdleCreditVault.prepareStopEpochWithApr0` increases the global `pendingWithdraws` liability by the full aggregate APR0 interest, but records a floor-divided `apr0RateByEpoch`. Each user later receives `principal * apr0RateByEpoch / 1e18`, also rounded down. Consequently, the strategy can receive strictly more withdrawal funding than the sum of all claimable user entitlements, permanently leaving unclaimable underlying in the contract.

### Finding Description
During an APR0 epoch, user withdrawal principals are accumulated in `apr0TotalPrincipal` and included in `pendingWithdraws`. [1](#0-0) 

At `stopEpoch`, the strategy calculates one aggregate APR0 interest allocation and adds the full net amount to `pendingWithdraws`. [2](#0-1)  It then stores a per-principal rate using floor division. [3](#0-2) 

`IdleCDOEpochVariant` rereads the increased `pendingWithdraws` and pulls that full amount from the borrower. [4](#0-3)  `collectWithdrawFunds` transfers the entire funded basis to `IdleCreditVault` and clears `pendingWithdraws` when the transfer is complete. [5](#0-4) 

Each user's APR0 interest is independently floor-rounded when settled. [6](#0-5)  The funded claim then pays principal plus only that rounded interest. [7](#0-6) 

This breaks the reserve invariant:

```text
sum(userPrincipal_i + floor(userPrincipal_i * apr0Rate / 1e18))
    <
pendingWithdraws
```

The excess is:

```text
apr0NetInterest - sum(floor(userPrincipal_i * apr0Rate / 1e18))
```

For example, with two users each requesting one underlying wei, total APR0 principal `P = 2` and aggregate net APR0 interest `A = 1`:

```text
apr0Rate = floor(1 * 1e18 / 2) = 0.5e18
user1 interest = floor(1 * 0.5e18 / 1e18) = 0
user2 interest = floor(1 * 0.5e18 / 1e18) = 0
funded withdrawal amount = 2 + 1 = 3
total claimable amount = 2
permanently stranded amount = 1 wei
```

### Impact Explanation
The borrower funds the full aggregate APR0 interest, but users can only claim the sum of their floor-rounded entitlements. The residual underlying is not represented by any claim, is excluded from active CDO NAV because pending receipts were removed from active accounting, and has no dedicated withdrawal path. This permanently freezes unclaimed yield in `IdleCreditVault`.

The loss is bounded by `numberOfApr0Claimants - 1` underlying wei for each APR0 settlement, but can recur whenever the same conditions are met. With multiple epochs and requesters, the stranded balance accumulates.

### Likelihood Explanation
The issue requires an APR0 epoch, at least two APR0 withdrawal requesters, and an aggregate interest allocation whose scaled rate or per-user products are not exactly divisible. Those conditions occur through ordinary, honest owner/manager and borrower behavior; no privileged actor needs to act maliciously.

The attacker does not directly obtain the residual. The primary impact is broken solvency/reserve accounting and permanent locking of funded withdrawal yield rather than theft.

### Recommendation
Do not fund and clear `pendingWithdraws` using an aggregate amount that differs from the sum of claimable rounded amounts.

Possible fixes include:

- Track exact per-user APR0 entitlements at stop time and increase `pendingWithdraws` only by their summed floor-rounded values.
- Explicitly attribute the residual dust to another accounting bucket, such as protocol yield or a recoverable dust balance, instead of mixing it with withdrawal reserves.
- Store sufficient per-epoch aggregate claim data so `collectWithdrawFunds` can reserve precisely the amount that can later be claimed.
- Add an invariant test asserting that strategy withdrawal reserves equal the sum of all outstanding funded claim entitlements, plus only explicitly categorized dust.

### Proof of Concept
The following test can be added to the existing `test/foundry/IdleCreditVault.t.sol` fixture, where `cdoEpoch`, `strategy`, `underlying`, `borrower`, `manager`, `AAtranche`, and `ONE_SCALE` are already initialized.

```solidity
function testApr0AggregateInterestFundingLeavesUnclaimableDust() external {
    IdleCreditVault vault = IdleCreditVault(address(strategy));

    uint256 activeDeposit = 1_000_000 * ONE_SCALE;
    uint256 apr0PrincipalPerUser = 1;
    uint256 apr0Principal = 2;
    uint256 overrideInterest = activeDeposit;

    address alice = makeAddr("alice");
    address bob = makeAddr("bob");

    // Existing fixture should mark all three wallets as KYC-allowed.
    // One active LP supplies the principal base used to calculate APR0 interest.
    idleCDO.depositAA(activeDeposit);
    _transferBurnedTrancheTokens(address(this), true);

    underlying.approve(address(idleCDO), type(uint256).max);
    deal(address(underlying), alice, apr0PrincipalPerUser);
    deal(address(underlying), bob, apr0PrincipalPerUser);

    vm.prank(alice);
    underlying.approve(address(idleCDO), apr0PrincipalPerUser);
    vm.prank(bob);
    underlying.approve(address(idleCDO), apr0PrincipalPerUser);

    vm.prank(alice);
    cdoEpoch.depositAA(apr0PrincipalPerUser);
    vm.prank(bob);
    cdoEpoch.depositAA(apr0PrincipalPerUser);

    // Enter APR0 mode while withdrawal requests are allowed.
    vm.prank(manager);
    vault.setApr(0);

    uint256 requestEpoch = vault.epochNumber();
    uint256 trancheWeiPerUnderlying =
        1e18 / cdoEpoch.virtualPrice(address(AAtranche));

    vm.prank(alice);
    cdoEpoch.requestWithdraw(trancheWeiPerUnderlying, address(AAtranche));
    vm.prank(bob);
    cdoEpoch.requestWithdraw(trancheWeiPerUnderlying, address(AAtranche));

    assertEq(vault.apr0TotalPrincipal(), apr0Principal);
    assertEq(vault.pendingWithdraws(), apr0Principal);

    vm.prank(manager);
    cdoEpoch.startEpoch();

    // The override is an honest manager-supplied realized interest amount.
    // With T = activeDeposit, P = 2 and I = T:
    // A = floor(I * P / (T + P)) = 1.
    deal(address(underlying), borrower, overrideInterest + apr0Principal);
    vm.prank(borrower);
    underlying.approve(
        address(cdoEpoch),
        overrideInterest + apr0Principal
    );

    vm.warp(cdoEpoch.epochEndDate() + 1);
    vm.prank(manager);
    cdoEpoch.stopEpoch(0, overrideInterest);

    uint256 apr0Interest =
        overrideInterest * apr0Principal /
        (activeDeposit + apr0Principal);
    assertEq(apr0Interest, 1);

    // rate = floor(1e18 / 2), so each one-wei principal receives 0 interest.
    uint256 rate = vault.apr0RateByEpoch(requestEpoch);
    assertEq(rate, 5e17);

    uint256 activeInterest = cdoEpoch.lastEpochInterest();
    assertEq(activeInterest, overrideInterest - apr0Interest);

    vm.prank(alice);
    cdoEpoch.claimWithdrawRequest();
    vm.prank(bob);
    cdoEpoch.claimWithdrawRequest();

    // The strategy contains deposited active interest plus one permanently
    // unclaimable wei of APR0 withdrawal funding.
    assertEq(
        underlying.balanceOf(address(vault)),
        activeInterest + 1
    );
}
```

### Citations

**File:** contracts/strategies/idle/IdleCreditVault.sol (L338-349)
```text
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L411-429)
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
    } else {
      // A plain implementation upgrade may leave legacy normal receipts pending. Their next
      // successful stop can fully fund the aggregate before lazy initialization occurs.
      pendingWithdraws = pendingBasis - _amount;
    }
    if (_amount != 0) {
      underlyingToken.safeTransferFrom(idleCDO, address(this), _amount);
    }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L521-527)
```text
        uint256 _apr0InterestGross = _interestNetOfFees * _principal / _totalPrincipalForSplit;
        if (_apr0InterestGross != 0) {
          // Same fee model as normal withdraw interest.
          uint256 _apr0Fee = _apr0InterestGross * _cdo.fee() / FULL_ALLOC;
          _apr0NetInterest = _apr0InterestGross - _apr0Fee;
          _adjPendingWithdrawFees += _apr0Fee;
          _expInterest -= _apr0NetInterest;
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L532-538)
```text
    // Finalize one-epoch APR0 interest for current epoch only.
    if (_apr0NetInterest != 0) {
      // Funds owed to withdraw requesters increase by APR0 net interest.
      pendingWithdraws += _apr0NetInterest;
      // Save per-epoch net rate; each APR0 request accrues exactly once on its request epoch.
      apr0RateByEpoch[epochNumber] = (_apr0NetInterest * 1e18) / _principal;
    }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L557-563)
```text
    _apr0User.settledPrincipal += _principal;
    uint256 _rate = apr0RateByEpoch[_reqEpoch];
    if (_rate != 0) {
      // Convert per-epoch rate to claimable underlying interest.
      _apr0User.settledInterest += (_principal * _rate) / 1e18;
    }
    _apr0User.principal = 0;
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L567-577)
```text
  function _requestWithdrawApr0(uint256 _amount, address _user) internal {
    // Settle any previous APR0 request first, then start/update current epoch bucket.
    _settleApr0(_user);
    Apr0UserData storage _apr0User = apr0Users[_user];
    if (_apr0User.principal == 0) {
      _apr0User.principalEpoch = epochNumber;
    }
    _apr0User.principal += _amount;
    // Epoch-level APR0 principal used only to compute stopEpoch APR0 pro-rata interest.
    apr0TotalPrincipal += _amount;
  }
```

**File:** contracts/IdleCDOEpochVariant.sol (L361-364)
```text
    // Strategy finalizes APR0 bucket state and returns adjusted stopEpoch values.
    (_expectedInterest, _pendingWithdrawFees) = _strategy.prepareStopEpochWithApr0(_interest);
    // Pending withdraws may be increased during APR0 settlement, so read after prepareStopEpochWithApr0.
    uint256 _pendingWithdraws = _strategy.pendingWithdraws();
```
