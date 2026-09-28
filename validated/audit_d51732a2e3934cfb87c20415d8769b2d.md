### Title
Lender can front-run owner's `_setLimit()` decrease to push TVL above the new cap and freeze all deposits - (File: contracts/GuardedLaunchUpgradable.sol)

### Summary
`GuardedLaunchUpgradable._setLimit()` lets the owner lower the vault TVL cap with no check that the new limit is above the current `getContractValue()`. A KYC-passing lender can watch the mempool for a limit-decrease transaction, front-run it with `depositAA`/`depositBB` (or `depositDuringEpoch`), and land the vault's TVL above the new `limit`. Every subsequent deposit reverts in `_guarded()` with `ContractLimitReached()` until the owner raises the cap again — the exact same bug class as the Enigma `setMaxTotalSupply` front-run, applied to `limit` instead of `maxTotalSupply`. [1](#0-0) [2](#0-1) [3](#0-2) 

### Finding Description
The TVL guard is:

```solidity
// GuardedLaunchUpgradable.sol
function _guarded(uint256 _amount) internal view {
  uint256 _limit = limit;
  if (_limit > 0) {
    if (getContractValue() + _amount > _limit) revert ContractLimitReached();
  }
}

function _setLimit(uint256 _limit) external {
  _checkOnlyOwner();
  limit = _limit;
}
```

`_guarded` is called from `IdleCDOCreditVault._deposit()` (backing `depositAA`/`depositBB`) and from the mid-epoch `depositDuringEpoch` path in `IdleCDOEpochVariant`. `_setLimit` writes `limit` unconditionally — it never requires `_limit >= getContractValue()`.

Attack sequence (buffer phase, epoch not running):

1. `limit = 10_000`, `getContractValue() = 2_000` (other lenders' deposits).
2. Owner broadcasts `_setLimit(5_000)` to reduce risk exposure.
3. A whitelisted lender sees it in the mempool and front-runs with `depositAA(5_000)`. Deposit succeeds: `2_000 + 5_000 <= 10_000` under the old limit.
4. Owner's tx executes: `limit = 5_000` while `getContractValue() = 7_000`.
5. Now `getContractValue() + _amount > limit` for any `_amount > 0`, so `depositAA`, `depositBB`, `depositDuringEpoch`, and queue-based `requestDeposit` (which also enforces the limit, see `testPrefundedRequestRespectsContractLimit`) all revert with `ContractLimitReached()`.

Unlike the Enigma case, `getContractValue()` is the NAV computed from strategy tokens, so the attacker cannot push it above the cap with a raw donation — `_skimDonatedAssets()` removes raw donations before `_guarded` in the epoch path. The front-run must go through a real deposit, which mints tranche tokens to the attacker at the current `tranchePrice`, so the attacker's funds are legitimately deployed and earn yield — same profit profile as the original report.

### Impact Explanation
Temporary freezing of the deposit surface. Until the owner sends another `_setLimit` raising the cap (a second privileged transaction, with its own timelock/governance latency), no lender can deposit through any entry point. The attacker paid for the DoS with their own deposit but receives tranche tokens worth at least principal and earns epoch interest, so the attack is cheap and repeatable on each subsequent limit decrease. Invariant broken: the owner's intent that `limit` bound TVL — the cap ends up strictly below actual NAV.

### Likelihood Explanation
Requires (a) owner intending to lower `limit` below a reachable TVL, and (b) a whitelisted (KYC-passing) lender monitoring the mempool. Both are realistic: guarded-launch caps are lowered as a vault matures, and lowering a cap is precisely the case where an attacker benefits. No privileged action by the attacker; the owner is honest and simply transacts in the clear. Front-running on mainnet or a private-mempool-absent L2 is standard.

### Recommendation
Add a sanity check in `_setLimit` (or schedule limit changes with a delay): `require(_limit == 0 || _limit >= getContractValue())`. Alternatively make `_guarded` compare against `max(limit, lastNAV)` or allow the cap decrease only when the pool is closed/emergency-paused so deposits are already blocked. A timelock on limit decreases also removes the atomic front-run window.

### Proof of Concept
```solidity
// test/foundry/LimitFrontRun.t.sol
function testFrontRunLimitDecrease_FreezesDeposits() external {
    // Setup: vault with whitelisted users, limit = 10_000e6
    vm.prank(cdoEpoch.owner());
    idleCDO._setLimit(10_000 * ONE_SCALE);

    // Legit lenders deposit 2_000
    _depositAs(Alice, 2_000 * ONE_SCALE);
    assertEq(idleCDO.getContractValue(), 2_000 * ONE_SCALE);

    // Owner tx pending: _setLimit(5_000). Attacker (KYC'd lender) front-runs.
    _depositAs(attacker, 5_000 * ONE_SCALE);            // succeeds under old limit
    assertEq(idleCDO.getContractValue(), 7_000 * ONE_SCALE);

    // Owner tx lands
    vm.prank(cdoEpoch.owner());
    idleCDO._setLimit(5_000 * ONE_SCALE);

    // All deposits now revert — TVL (7_000) already exceeds new limit (5_000)
    vm.prank(Bob);
    underlying.approve(address(idleCDO), 1 * ONE_SCALE);
    vm.expectRevert(ContractLimitReached.selector);
    vm.prank(Bob);
    idleCDO.depositAA(1 * ONE_SCALE);

    // Same for queue-based path if configured:
    // queue.requestDeposit(1) -> ContractLimitReached

    // Attacker keeps tranche tokens + accrues yield; deposits stay frozen
    // until owner broadcasts a corrective _setLimit(>= 7_000).
}
```

### Citations

**File:** contracts/GuardedLaunchUpgradable.sol (L45-65)
```text
  function _guarded(uint256 _amount) internal view {
    uint256 _limit = limit;
    if (_limit > 0) {
      if (getContractValue() + _amount > _limit) revert ContractLimitReached();
    }
  }

  /// @dev Check that the second function is not called in the same tx from the same tx.origin
  function _checkOnlyOwner() internal view {
    _checkNotAuthorized(owner() != msg.sender);
  }

  /// @notice abstract method, should return the TVL in underlyings
  function getContractValue() public virtual view returns (uint256);

  /// @notice set contract TVL limit
  /// @param _limit limit in underlying value, 0 means no limit
  function _setLimit(uint256 _limit) external {
    _checkOnlyOwner();
    limit = _limit;
  }
```

**File:** contracts/IdleCDOCreditVault.sol (L191-212)
```text
  function _deposit(uint256 _amount, address _tranche) internal virtual whenNotPaused returns (uint256 _minted) {
    if (_amount == 0) {
      return _minted;
    }
    // check that we are not depositing more than the contract available limit
    _guarded(_amount);
    // interest accrued since last depositXX/withdrawXX is splitted between AA and BB
    // according to trancheAPRSplitRatio. NAVs of AA and BB are updated and tranche
    // prices adjusted accordingly
    _updateAccounting();
    // get underlyings from sender
    address _token = token;
    uint256 _preBal = _contractTokenBalance(_token);
    _transferUnderlyingsFrom(msg.sender, address(this), _amount);
    // mint tranche tokens according to the current tranche price
    _minted = _mintSharesAtCurrPrice(_contractTokenBalance(_token) - _preBal, msg.sender, _tranche);
    // update trancheAPRSplitRatio
    _updateSplitRatio(_getAARatio(true));

    // direct deposit in the strategy
    IIdleCDOStrategy(strategy).deposit(_amount);
  }
```

**File:** contracts/IdleCDOEpochVariant.sol (L679-682)
```text
    _skimDonatedAssets();
    // Check that limit is not exceeded after removing skimmable raw donations.
    _guarded(_amount);
    _updateAccounting();
```
