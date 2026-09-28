### Title
Unfunded withdraw/instant receipts from epochs *before* the defaulted epoch bypass the recovery haircut and are paid at par - (contracts/strategies/idle/IdleCreditVault.sol)

### Summary
Analogous to `is_in_or_equal` being tricked into treating an out-of-set input as contained, `IdleCreditVault`'s default-claim path uses a **per-epoch membership check** (`instantWithdrawsRequestsByEpoch[_user][defaultEpoch]`, `withdrawsRequestsByEpoch[_user][defaultEpoch]`, `instantWithdrawClaimsByEpoch[epochNumber]`) to decide which receipts are haircut. Receipts belonging to **earlier, still-unfunded epochs** fail that membership test, escape `defaultPendingClaimBasis()`, and are then paid 1:1 via `_transferFundedClaim`, bypassing `defaultRecoveryPrice` entirely.

### Finding Description
When a borrower defaults and `finalizeDefaultRecovery` runs, the recovery basis is computed as:

- `basis = pendingWithdraws + instantWithdrawClaimsByEpoch[epochNumber]` — only the **current (defaulted) epoch's** instant receipts are included in `defaultPendingClaimBasis` [1](#0-0) .

But `pendingInstantWithdraws` (and `withdrawsRequests`) are **aggregates across all epochs**. An instant-withdraw request made in epoch N−1 that was never funded (CDO had no cash at `startEpoch`) keeps `pendingInstantWithdraws > 0` into epoch N. When epoch N defaults:

1. The old receipt is not in `instantWithdrawClaimsByEpoch[N]`, so it is excluded from the recovery `basis` → `defaultRecoveryPrice = reserve / basis` is computed *as if that debt did not exist* [2](#0-1) .
2. The holder calls `claimInstantWithdrawRequest`. `_claimDefaultedInstantWithdrawRequest` looks up `instantWithdrawsRequestsByEpoch[_user][defaultEpoch]` — which is `0` for the old receipt — so nothing is cleared and no haircut is applied [3](#0-2) .
3. Execution falls through to the funded path: `_burn(_user, instantWithdrawsRequests[_user])` and `_transferFundedClaim(_user, amount)` pay the **full par amount** [4](#0-3) .

The only guard is `_transferFundedClaim`'s check `balance - reserve < _amount` [5](#0-4) , which merely prevents spending the reserve itself. Any non-reserve underlying present at the strategy — most importantly **late borrower repayments arriving after `finalizeDefaultRecovery`**, or any funded remainder from `_defaultPrefundedInstantReserve` bookkeeping — is payable at par to these escaped receipts.

The identical escape exists for normal withdraw requests: `_claimDefaultedWithdrawRequest` clears only `withdrawsRequestsByEpoch[_user][defaultEpoch]` via `_clearWithdrawClaimForEpoch`, and the remaining aggregate `withdrawsRequests[_user]` is paid at par by `_claimFundedWithdrawRequest` [6](#0-5) .

### Impact Explanation
An unprivileged lender whose withdraw request matured but was never funded before the default epoch receives **100 cents on the dollar** while every defaulted-epoch claimant receives only `defaultRecoveryPrice`. The loss comes from funds that should ratably belong to all defaulted creditors (post-finalization recoveries); the attacker effectively front-runs the recovery pool — or forces `_transferFundedClaim` reverts (temporary freezing) for legitimate funded claims while non-reserve funds are drained first. Loss magnitude: up to the full unfunded pre-default receipt balance, claimable at par instead of at `defaultRecoveryPrice`.

### Likelihood Explanation
Requires: (a) an instant/normal withdraw request that stays unfunded across an epoch boundary (occurs whenever the CDO's cash at `startEpoch` doesn't cover `pendingInstantWithdraws`), then (b) a borrower default, then (c) any non-reserve underlying reaching the strategy post-finalization (late partial repayment — common in credit recoveries). Preconditions are orchestratable by an ordinary KYC'd lender; (b) and (c) are external events but are precisely the scenario this recovery code exists for. The analogous CheckPoint: the membership check was designed for same-epoch receipts and silently misclassifies older ones.

### Recommendation
Include **all** unfunded instant receipts (not only `instantWithdrawClaimsByEpoch[epochNumber]`) and all unfunded normal receipts in `defaultPendingClaimBasis`, or at claim time route *any* receipt that was unfunded at `defaultRecoveryEpoch` through `_claimDefaulted*` paths regardless of which epoch recorded it. Concretely: iterate `instantWithdrawsRequestsByEpoch`/`withdrawsRequestsByEpoch` for epochs `<= defaultRecoveryEpoch`, or track a per-user "unfunded at default" flag so the par-funded path is unreachable for receipts that were never backed.

### Proof of Concept
```solidity
// Foundry fork test sketch — fixed-APR vault, instant withdraws enabled.
function testPreDefaultInstantReceiptBypassesHaircut() public {
    // Epoch N-1: Alice deposits, requests instant withdraw.
    _depositAA(alice, 100e6);
    vm.prank(alice);
    cdoEpoch.requestInstantWithdraw(50e6); // mints receipt, instantWithdrawsRequests[alice]=50e6
    // startEpoch: CDO has no cash -> collectInstantWithdrawFunds(0)
    // pendingInstantWithdraws stays 50e6 going into epoch N.
    _startEpoch(); // epochNumber -> N

    // Epoch N: borrower defaults. finalizeDefaultRecovery:
    //   basis = pendingWithdraws + instantWithdrawClaimsByEpoch[N]
    //   alice's receipt is in epoch N-1 basis -> excluded.
    //   recovered = 100e6, basis = e.g. 1000e6 -> defaultRecoveryPrice = 0.1e18
    _handleBorrowerDefault();
    strategy.finalizeDefaultRecovery(100e6, recoverySource);

    // Late partial repayment lands at the strategy (non-reserve funds).
    deal(address(underlying), borrower, 50e6);
    vm.prank(borrower);
    underlying.transfer(address(strategy), 50e6);

    // Alice claims: _claimDefaultedInstantWithdrawRequest clears basis[N]==0,
    // funded path burns her receipt and pays 50e6 AT PAR instead of 50e6*0.1.
    vm.prank(address(cdoEpoch));
    strategy.claimInstantWithdrawRequest(alice);
    assertEq(underlying.balanceOf(alice), 50e6); // escaped the haircut
    // Meanwhile defaulted-epoch claimants only recover 10% from the reserve.
}
```

### Citations

**File:** contracts/strategies/idle/IdleCreditVault.sol (L387-392)
```text
    uint256 amount = instantWithdrawsRequests[_user];
    // burn strategy tokens from user
    _burn(_user, amount);

    instantWithdrawsRequests[_user] = 0;
    _transferFundedClaim(_user, amount);
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L842-856)
```text
  function _claimDefaultedInstantWithdrawRequest(address _user) internal returns (uint256 claimBasis) {
    uint256 defaultEpoch = defaultRecoveryEpoch;
    claimBasis = instantWithdrawsRequestsByEpoch[_user][defaultEpoch];
    if (claimBasis == 0) return claimBasis;

    instantWithdrawsRequestsByEpoch[_user][defaultEpoch] = 0;
    instantWithdrawsRequests[_user] -= claimBasis;
    uint256 pending = pendingInstantWithdraws;
    // `pendingInstantWithdraws` is only the unfunded remainder. If this user's claim is larger,
    // the extra amount was already counted as prefunded reserve during default finalization.
    pendingInstantWithdraws = claimBasis >= pending ? 0 : pending - claimBasis;
    instantWithdrawClaimsByEpoch[defaultEpoch] -= claimBasis;
    _burn(_user, claimBasis);
    _transferDefaultRecovery(_user, (claimBasis * defaultRecoveryPrice) / RECOVERY_FULL);
  }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L897-907)
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
  }
```
