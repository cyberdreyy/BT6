### Title
Stale-epoch instant withdraw receipts are excluded from default recovery basis and become permanently unclaimable - (contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`defaultPendingClaimBasis()` only adds instant receipts recorded under the *current* `epochNumber` (`instantWithdrawClaimsByEpoch[epochNumber]`), while the trigger condition uses the aggregate `pendingInstantWithdraws`. Unfunded instant receipts created in an earlier epoch (partial `collectInstantWithdrawFunds` funding) are therefore never included in `totalBasis` inside `finalizeDefaultRecovery`, so no recovery reserve is allocated for them. After finalization, `claimInstantWithdrawRequest` routes through `_claimDefaultedInstantWithdrawRequest` (which only clears the *default* epoch) and then `_transferFundedClaim`, which reverts whenever the payout would touch `defaultRecoveryReserve`. The stale-epoch claimant's funds are permanently frozen with no alternative path.

### Finding Description
In `IdleCreditVault.sol`, `claimInstantWithdrawRequest` handles post-default claims in two steps: [1](#0-0) 

`_claimDefaultedInstantWithdrawRequest` only looks up `instantWithdrawsRequestsByEpoch[_user][defaultRecoveryEpoch]`: [2](#0-1) 

The basis that receives the recovery multiplier in `finalizeDefaultRecovery` only counts the current epoch: [3](#0-2) [4](#0-3) 

Meanwhile `_transferFundedClaim` hard-blocks any spend that would dip into `defaultRecoveryReserve` once it is non-zero: [5](#0-4) 

Attack sequence (attacker = ordinary KYC'd lender / tranche holder):
1. Buffer/running epoch N−1: attacker calls the CDO withdraw path → `requestInstantWithdraw(amount, attacker)`. `instantWithdrawsRequestsByEpoch[attacker][N−1] += amount` and `pendingInstantWithdraws += amount`.
2. Epoch N−1 stops; the borrower/manager only partially funds the instant queue via `collectInstantWithdrawFunds` (attacker's portion remains unfunded). Epoch N starts (`epochNumber` = N).
3. During epoch N the borrower defaults; the CDO calls `finalizeDefaultRecovery`.
4. `pendingInstantWithdraws != 0` is true (attacker's stale receipt), so `defaultInstantWithdrawsFinalized = true` and basis gets `instantWithdrawClaimsByEpoch[N]` — which is 0 for the attacker. `totalBasis` and `defaultRecoveryReserve` exclude the attacker's `amount`.
5. Attacker calls `claimInstantWithdrawRequest`: `_claimDefaultedInstantWithdrawRequest` finds `instantWithdrawsRequestsByEpoch[attacker][N] == 0` and returns; the fallback `_transferFundedClaim(attacker, amount)` reverts because `balance - reserve < amount` — the strategy holds only the isolated recovery reserve.

The analogous broken invariant to the CVE's malformed-input/OOB-read is an out-of-scope epoch read: finalization "parses" only the current epoch's instant claim table while gating on the aggregate pending counter, so cross-epoch receipts fall outside both recovery and funded accounting — one receipt, zero payout.

### Impact Explanation
Permanent freezing of user funds: the attacker's instant-withdraw underlying is neither claimable at par (reserve guard) nor haircut-paid (not in recovery basis), and `instantWithdrawsRequests[attacker]` can never be cleared. Loss equals the unfunded stale-epoch instant amount (up to the full `pendingInstantWithdraws` remainder left unfunded before the default epoch). Unlike a pure DoS, there is no recovery path — the reserve accounting will never cover this basis, and `requestWithdraw` post-default also reverts for the user (`instantWithdrawsRequests[_user] != 0` check), blocking every future withdraw route.

### Likelihood Explanation
Requires (a) an instant withdraw request left partially unfunded across an epoch boundary (possible whenever borrower/`startEpoch` funding via `collectInstantWithdrawFunds` covers less than `pendingInstantWithdraws`) and (b) a subsequent borrower default finalized by honest admin roles. Both are normal operating conditions of the vault, not attacker-only states, so the scenario needs no privileged misbehavior. Likelihood is moderate: it depends on a default occurring while stale instant receipts remain unfunded.

### Recommendation
Include *all* epochs' instant claim basis in `defaultPendingClaimBasis` — e.g. track an aggregate `instantWithdrawClaims` counter (or iterate/trap) so the basis equals the total unfunded instant claims, not only `instantWithdrawClaimsByEpoch[epochNumber]`. Alternatively, in `_claimDefaultedInstantWithdrawRequest`, clear `instantWithdrawsRequests[_user]` entirely (all epochs) and apply `defaultRecoveryPrice` to the full aggregate, and decrement `pendingInstantWithdraws` consistently so stale receipts receive the haircut payout instead of reverting in `_transferFundedClaim`.

### Proof of Concept
```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;
// Foundry fork test sketch: deploy IdleCDOEpochVariant + IdleCreditVault,
// attacker deposits, epoch N-1 starts, attacker requestInstantWithdraw(X).
// Honest manager stops epoch; collectInstantWithdrawFunds funds only X-1 dust
// (or nothing), leaving pendingInstantWithdraws = X with claim basis in epoch N-1.
// startEpoch -> epochNumber = N. Borrower defaults -> finalizeDefaultRecovery(R, src).
// assert: instantWithdrawClaimsByEpoch[N][attacker] == 0,
//         pendingInstantWithdraws == X, defaultInstantWithdrawsFinalized == true.
// vm.expectRevert(NotAllowed) on cdo.claimInstantWithdrawRequest(attacker):
//   _claimDefaultedInstantWithdrawRequest clears nothing (per-epoch == 0),
//   _transferFundedClaim reverts: balance - defaultRecoveryReserve < X.
// Attacker's X underlying is permanently frozen; reserve accounting never covered it.
function test_staleEpochInstantClaimFrozen() public {
    // 1. seed vault, attacker deposits AA, requestInstantWithdraw(X) in epoch N-1
    // 2. stopEpoch: borrower sends only partial instant funding
    // 3. startEpoch (epoch N); borrower defaults; finalizeDefaultRecovery
    // 4. expect revert on attacker.claimInstantWithdrawRequest via CDO
    // 5. assert instantWithdrawsRequests[attacker] == X forever
}
```

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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L644-649)
```text
  function defaultPendingClaimBasis() public view returns (uint256 basis) {
    basis = pendingWithdraws;
    if (pendingInstantWithdraws != 0) {
      basis += instantWithdrawClaimsByEpoch[epochNumber];
    }
  }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L685-696)
```text
    uint256 prefundedReserve = _defaultPrefundedInstantReserve();
    uint256 reserveAmount = _recoveredAmount + prefundedReserve + defaultRecoveryReserve;
    // Recovery can be above par if the recovered funds exceed the computed basis.
    uint256 recoveryPrice = reserveAmount * RECOVERY_FULL / totalBasis;

    defaultRecoveryFinalized = true;
    defaultRecoveryReserve = reserveAmount;
    defaultRecoveryPrice = recoveryPrice;
    defaultRecoveryEpoch = epochNumber;
    // A non-zero pending instant bucket means current-epoch instant receipts were not fully funded
    // and must be paid through the same recovery ratio as normal pending receipts.
    defaultInstantWithdrawsFinalized = pendingInstantWithdraws != 0;
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
