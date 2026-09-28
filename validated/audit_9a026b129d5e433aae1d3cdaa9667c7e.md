### Title
Prefunded instant-withdraw receipts are double-counted into `defaultRecoveryReserve` on `finalizeDefaultRecovery`, permanently freezing the instant claimant's funds - ([File: contracts/strategies/idle/IdleCreditVault.sol](contracts/strategies/idle/IdleCreditVault.sol))

### Summary
Like the ibmvfc bug — where a queue's element pool is freed per connection event while in-flight commands still pull from it — `IdleCreditVault.finalizeDefaultRecovery` folds *already-funded* instant-withdraw underlying into `defaultRecoveryReserve` via `_defaultPrefundedInstantReserve()`, while `claimInstantWithdrawRequest` is forced to pay those same receipts through `_transferFundedClaim`, which is explicitly forbidden from touching the reserve. The receipt's backing is simultaneously "in the reserve" (distributed to other claimants via an inflated `defaultRecoveryPrice`) and "owed to the instant claimant" — a one-receipt-two-payouts conflict that resolves as a permanent revert for the instant claimant.

### Finding Description
When instant-withdraw requests are fully funded before finalization, `collectInstantWithdrawFunds` pulls the underlying into the strategy and drains `pendingInstantWithdraws` to 0 [1](#0-0) . At `finalizeDefaultRecovery`:

- `defaultPendingClaimBasis()` excludes the current-epoch instant basis because `pendingInstantWithdraws == 0` [2](#0-1) .
- `_defaultPrefundedInstantReserve()` still returns `instantWithdrawClaimsByEpoch[epochNumber]` (the full prefunded amount) and adds it to `reserveAmount` [3](#0-2) .
- `defaultInstantWithdrawsFinalized` is set to `pendingInstantWithdraws != 0` → `false` [4](#0-3) .

Then in `claimInstantWithdrawRequest`, the `defaultRecoveryFinalized && defaultInstantWithdrawsFinalized` branch is skipped, so the defaulted-epoch instant receipt is never haircut-cleared; the code falls through to `_transferFundedClaim(_user, amount)` for the full `instantWithdrawsRequests[_user]` [5](#0-4) . But `_transferFundedClaim` reverts when `balance - defaultRecoveryReserve < amount` [6](#0-5) . Since the claimant's own prefunded underlying was just swept into `defaultRecoveryReserve`, `balance - reserve` excludes exactly the funds backing that receipt, so the claim reverts (or the same underlying is paid twice if other balances exist — either way the reserve accounting is violated).

Worse, those prefunded funds inflate `recoveryPrice = reserveAmount * RECOVERY_FULL / totalBasis` while not being included in `totalBasis`, transferring the instant claimant's money to defaulted-withdraw and active-LP claimants at an above-fair recovery ratio [7](#0-6) .

### Impact Explanation
An unprivileged user holding a fully-funded instant-withdraw receipt at default time permanently loses the claim: `claimInstantWithdrawRequest` reverts whenever the reserve guard binds, and their already-collected underlying is redistributed to other recovery claimants through the inflated `defaultRecoveryPrice`. Loss equals the full prefunded instant-receipt amount (quantified: `instantWithdrawClaimsByEpoch[defaultEpoch]`). One receipt one payout is broken — the same underlying backs both the reserve and the live `instantWithdrawsRequests` entry.

### Likelihood Explanation
Requires a precise but honest sequence, all triggerable by unprivileged/normal flows around honest privileged calls: user calls `requestInstantWithdraw` during the buffer; the epoch's start/stop cycle fully funds the instant queue via `collectInstantWithdrawFunds` (so `pendingInstantWithdraws == 0` while `instantWithdrawsRequests[user] > 0` and `instantWithdrawClaimsByEpoch[epoch] > 0`); the borrower then defaults and the manager calls `finalizeDefault`. No attacker control over privileged roles is needed — the victim is the instant claimant, and any instant claimant loses their funded payout. Prefunded instant queues are a supported, documented mode of the vault.

### Recommendation
In `claimInstantWithdrawRequest`, route claims for the default epoch through `_claimDefaultedInstantWithdrawRequest` whenever `defaultRecoveryFinalized` is true and the user has a nonzero `instantWithdrawsRequestsByEpoch[user][defaultRecoveryEpoch]` — regardless of `defaultInstantWithdrawsFinalized`. Correspondingly, `_defaultPrefundedInstantReserve()` should either be excluded from `reserveAmount` for fully-funded instant claims (they pay at par outside the reserve), or the prefunded claims must be paid out of the reserve at `defaultRecoveryPrice` like other defaulted receipts. Pick one ownership domain per receipt; never count the same underlying in both the reserve and the funded-claim pool.

### Proof of Concept
Foundry fork PoC sketch (based on `test/foundry/IdleCreditVault.t.sol` helpers):

```solidity
// 1. user deposits and requests instant withdraw during buffer
_depositWithUser(user, amount, true);
vm.prank(user);
cdoEpoch.requestInstantWithdraw(trancheBal, address(AAtranche));

// 2. epoch cycle fully funds the instant queue:
//    collectInstantWithdrawFunds drains pendingInstantWithdraws to 0
//    (instantWithdrawsRequests[user] stays > 0)
_startEpochAndCheckPrices(0);   // triggers getInstantWithdrawFunds -> collectInstantWithdrawFunds

// 3. borrower defaults; manager finalizes recovery
_stopEpochAndCheckPrices(0, initialProvidedApr, 0);
_checkDefault();
deal(defaultUnderlying, manager, recovered);
vm.startPrank(manager);
IERC20Detailed(defaultUnderlying).approve(address(strategy), recovered);
cdoEpoch.finalizeDefault(recovered, manager);
vm.stopPrank();

// Assertions:
// strategy.defaultInstantWithdrawsFinalized() == false
// strategy.defaultRecoveryReserve() includes the prefunded instant amount
// (inflating defaultRecoveryPrice for other claimants)

// 4. victim claims -> reverts NotAllowed inside _transferFundedClaim
//    because balance - defaultRecoveryReserve < instantWithdrawsRequests[user]
vm.prank(user);
vm.expectRevert(abi.encodeWithSelector(NotAllowed.selector));
cdoEpoch.claimInstantWithdrawRequest();

// Victim's prefunded funds remain trapped/redistributed to other claimants.
```

Key assertion that proves the bug: `strategy.instantWithdrawsRequestsByEpoch(user, strategy.defaultRecoveryEpoch())` is nonzero, `strategy.defaultInstantWithdrawsFinalized()` is false, and `claimInstantWithdrawRequest` permanently reverts while `defaultRecoveryReserve` still contains the victim's prefunded underlying.

### Citations

**File:** contracts/strategies/idle/IdleCreditVault.sol (L380-393)
```text
  function claimInstantWithdrawRequest(address _user) external {
    _onlyIdleCDO();
    if (defaultRecoveryFinalized && defaultInstantWithdrawsFinalized) {
      // Clear the defaulted-epoch instant receipt first, then continue so the same call can
      // also pay any older instant receipt that was already funded before default finalization.
      _claimDefaultedInstantWithdrawRequest(_user);
    }
    uint256 amount = instantWithdrawsRequests[_user];
    // burn strategy tokens from user
    _burn(_user, amount);

    instantWithdrawsRequests[_user] = 0;
    _transferFundedClaim(_user, amount);
  }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L398-403)
```text
  function collectInstantWithdrawFunds(uint256 _amount) external {
    _onlyIdleCDO();
    if (_amount == 0) return;
    pendingInstantWithdraws -= _amount;
    underlyingToken.safeTransferFrom(idleCDO, address(this), _amount);
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L679-691)
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
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L694-696)
```text
    // A non-zero pending instant bucket means current-epoch instant receipts were not fully funded
    // and must be paid through the same recovery ratio as normal pending receipts.
    defaultInstantWithdrawsFinalized = pendingInstantWithdraws != 0;
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L897-906)
```text
  function _transferFundedClaim(address _user, uint256 _amount) internal {
    if (_amount == 0) return;
    uint256 reserve = defaultRecoveryReserve;
    if (reserve != 0) {
      uint256 balance = underlyingToken.balanceOf(address(this));
      // This should be unreachable when accounting is consistent. Keep the guard so old funded
      // receipts can never spend underlyings reserved for default recovery claimants.
      if (balance < reserve || balance - reserve < _amount) revert NotAllowed();
    }
    underlyingToken.safeTransfer(_user, _amount);
```
