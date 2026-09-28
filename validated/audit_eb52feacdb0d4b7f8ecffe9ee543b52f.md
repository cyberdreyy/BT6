### Title
Post-default instant-withdraw receipts bypass recovery haircut and drain the fixed `defaultRecoveryReserve` - ([File: contracts/strategies/idle/IdleCreditVault.sol])

### Summary
`requestWithdraw` explicitly rejects or reroutes requests once `defaultRecoveryFinalized` is set, but `requestInstantWithdraw` has no equivalent check. A receipt created after default finalization is written into `instantWithdrawsRequestsByEpoch[_user][epochNumber]`, which is the same bucket as `defaultRecoveryEpoch` (the epoch counter no longer advances after default). It was never counted in `defaultPendingClaimBasis` when the fixed recovery reserve and `defaultRecoveryPrice` were computed, yet it is still claimable — either haircut at the default epoch (diluting the fixed reserve) or, worse, at par through `_transferFundedClaim` when `defaultInstantWithdrawsFinalized` is false. This is the exact analog of the NEAR race: a "pending" item is accepted without checking that the terminal state (header/default finalization) was already reached.

### Finding Description
- `requestWithdraw` guards the finalized state at `IdleCreditVault.sol:247-257`: post-default requests are rejected if old receipts exist, otherwise parked in `postDefaultRequests` and paid 1:1 (fair, because the CDO already haircut the amount via `virtualPrice`). [1](#0-0) 
- `requestInstantWithdraw` at `IdleCreditVault.sol:356-375` performs no such check. It burns CDO strategy tokens, mints a full-size receipt to the user, and records it in `instantWithdrawsRequestsByEpoch[_user][epochNumber]` and `instantWithdrawClaimsByEpoch[epochNumber]`. [2](#0-1) 
- The recovery reserve is fixed once in `finalizeDefaultRecovery`: `reserveAmount` and `recoveryPrice` are computed from `defaultPendingClaimBasis()` evaluated at finalization time only (`IdleCreditVault.sol:679-699`). Any basis added afterwards is unbacked. [3](#0-2) 
- Two payout paths then leak value:
  1. If `defaultInstantWithdrawsFinalized` is true, `claimInstantWithdrawRequest` routes through `_claimDefaultedInstantWithdrawRequest`, which reads `instantWithdrawsRequestsByEpoch[_user][defaultRecoveryEpoch]`. Because `epochNumber == defaultRecoveryEpoch` still holds, the post-default receipt is paid `claimBasis * defaultRecoveryPrice` from `defaultRecoveryReserve` — a haircut it never deserved (the underlying tranche tokens were already haircut), and taken from a reserve sized for pre-default claimants only. [4](#0-3) 
  2. If `defaultInstantWithdrawsFinalized` is false (`pendingInstantWithdraws == 0` at finalization), `claimInstantWithdrawRequest` skips the defaulted branch entirely and pays the full `instantWithdrawsRequests[_user]` via `_transferFundedClaim` — par payment against a vault whose only remaining backing is the discounted `defaultRecoveryReserve`. [5](#0-4) 
- In both cases the new claimBasis is pure dilution: total claims exceed the finalized reserve, so earlier legitimate claimants (or active-LP recovery) are left underpaid or unpaid.

### Impact Explanation
Direct theft / dilution of the default-recovery reserve. An unprivileged tranche holder who can route an instant-withdraw request after `finalizeDefault` extracts underlyings that are earmarked for defaulted-epoch receipt holders and active LPs. Quantified loss is up to the full `defaultRecoveryReserve` (path 2 pays par; the attacker's receipt is minted 1:1 while the reserve only covers `recoveryPrice` per unit of basis), and any positive fraction of it under path 1. One-receipt-one-payout and the fixed-recovery-price invariant are broken.

### Likelihood Explanation
Depends on CDO-side reachability: `requestInstantWithdraw` is `_onlyIdleCDO`, so the attack requires the IdleCDOEpochVariant instant-withdraw path to remain callable while `defaulted()` (e.g., closed-pool / `epochEndDate == 0` flows, or a defaulted epoch where instant withdrawals are still permitted). The asymmetric guarding — `requestWithdraw` was explicitly hardened for post-default reentry while `requestInstantWithdraw` was not — suggests this edge was missed. I could not fully verify the CDO-side gating in this pass; if the CDO unconditionally reverts instant requests when defaulted, the issue reduces to a defense-in-depth gap rather than an exploitable path.

### Recommendation
Mirror the `requestWithdraw` handling in `requestInstantWithdraw`: after `_ensureDefaultRecoveryInitialized()`, if `defaultRecoveryFinalized` revert `NotAllowed()` (or route through a `postDefaultRequests`-style bucket paid 1:1 only if the amount is pre-haircut). Additionally, `claimInstantWithdrawRequest` should not pay post-default receipts through `_transferFundedClaim` when `defaultInstantWithdrawsFinalized` is false.

### Proof of Concept
Foundry fork PoC sketch (standard epoch variant):

```solidity
// 1. Users deposit; startEpoch; request some instant + normal withdraws.
// 2. Warp past epochEndDate, under-fund borrower, stopEpoch -> defaulted.
// 3. finalizeDefault(recovered, manager) with recovered < basis
//    -> defaultRecoveryPrice < RECOVERY_FULL, reserve fixed.
//    (Case A: pendingInstantWithdraws == 0 so defaultInstantWithdrawsFinalized == false)
// 4. Attacker: cdoEpoch.requestInstantWithdraw(trancheAmount)   // no default guard in vault
// 5. Attacker: cdoEpoch.claimInstantWithdrawRequest()
//    -> _transferFundedClaim pays full trancheAmount at par from the reserve.
// 6. Assert: sum of legitimate default claims now exceeds remaining reserve;
//    a second defaulted claimant's transfer reverts or is shortchanged.
```

For Case B (`pendingInstantWithdraws != 0` at finalization): step 4 records the receipt into `instantWithdrawsRequestsByEpoch[user][defaultRecoveryEpoch]`, and step 5 pays `amount * defaultRecoveryPrice` — reserve drain equal to the unaccounted claimBasis. Expected assertion: `defaultRecoveryReserve` decreases by more than the attacker's fair pro-rata share, and `instantWithdrawClaimsByEpoch[defaultEpoch]` goes negative relative to the finalized basis.

### Citations

**File:** contracts/strategies/idle/IdleCreditVault.sol (L247-257)
```text
    if (defaultRecoveryFinalized) {
      // user should first claim old already-funded withdraw requests before requesting new ones after default
      if (_hasWithdrawRequest(_user) || instantWithdrawsRequests[_user] != 0 || postDefaultRequests[_user] != 0) {
        revert NotAllowed();
      }
      // Preserve request/claim UX after default without increasing borrower-facing pendingWithdraws.
      // The CDO passes an already-haircut amount because finalization lowered virtualPrice first.
      _burn(msg.sender, _amount);
      _mint(_user, _amount);
      postDefaultRequests[_user] = _amount;
      return;
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L356-375)
```text
  function requestInstantWithdraw(uint256 _amount, address _user) external {
    _onlyIdleCDO();
    _ensureDefaultRecoveryInitialized();
    // burn strategy tokens from cdo
    _burn(msg.sender, _amount);
  
    // mint equal amount of strategy tokens to the user as receipt, useful in case of default
    _mint(_user, _amount);

    // increase the instant withdraw requests for the user
    instantWithdrawsRequests[_user] += _amount;
    uint256 currentEpoch = epochNumber;
    // we record both per-user (old, kept for compatibility) and per-epoch so on
    // finalization we can distinguish "default-epoch pending instant receipts"
    // from old funded instant receipts.
    instantWithdrawsRequestsByEpoch[_user][currentEpoch] += _amount;
    instantWithdrawClaimsByEpoch[currentEpoch] += _amount;
    // increase the total instant withdraw requests
    pendingInstantWithdraws += _amount;
  }
```

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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L679-699)
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
    defaultRecoveryEpoch = epochNumber;
    // A non-zero pending instant bucket means current-epoch instant receipts were not fully funded
    // and must be paid through the same recovery ratio as normal pending receipts.
    defaultInstantWithdrawsFinalized = pendingInstantWithdraws != 0;
    // Bring active CDO NAV to the same recovery ratio. IdleCDOEpochVariant then calls
    // _forceUpdateAccounting so tranche prices/virtualPrice expose the crystallized loss.
    uint256 activeFinalNAV = (activeBasis * recoveryPrice) / RECOVERY_FULL;
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
