Found a strong candidate. Verifying the CDO-side entry point permits instant-withdraw requests after default finalization.### Title
Post-default instant-withdraw receipts reuse the finalized default-epoch claim slot and drain `defaultRecoveryReserve` — ([File: contracts/strategies/idle/IdleCreditVault.sol])

### Summary
The use-after-free bug class maps to a "freed" accounting slot being reused: after `finalizeDefaultRecovery` crystallizes `defaultRecoveryEpoch`, `defaultRecoveryPrice`, and `defaultRecoveryReserve`, `requestInstantWithdraw` never checks `defaultRecoveryFinalized`. A post-default instant receipt is therefore recorded in `instantWithdrawsRequestsByEpoch[user][epochNumber]` — and since `epochNumber` never advances after default, that key equals `defaultRecoveryEpoch`. The next `claimInstantWithdrawRequest` treats the brand-new receipt as a defaulted-epoch claim and pays it `defaultRecoveryPrice` out of the isolated recovery reserve, even though the request was never part of `totalBasis` when the reserve was sized.

### Finding Description
- `requestWithdraw` explicitly branches on `defaultRecoveryFinalized` and routes new requests into `postDefaultRequests`, keeping them out of default accounting [1](#0-0) .
- `requestInstantWithdraw` has no such branch. It burns CDO strategy tokens, mints a receipt to the user, and indexes the basis under the current `epochNumber` [2](#0-1) .
- `claimInstantWithdrawRequest` first runs `_claimDefaultedInstantWithdrawRequest(_user)` whenever `defaultInstantWithdrawsFinalized` is set [3](#0-2) .
- `_claimDefaultedInstantWithdrawRequest` reads `instantWithdrawsRequestsByEpoch[_user][defaultRecoveryEpoch]` as `claimBasis`, burns the receipt, and pays `claimBasis * defaultRecoveryPrice / RECOVERY_FULL` via `_transferDefaultRecovery`, which decrements `defaultRecoveryReserve` [4](#0-3) .
- The reserve was sized at finalization as `reserveAmount = recovered + prefundedInstant + prior reserve` against `totalBasis = activeBasis + defaultPendingClaimBasis()` [5](#0-4) . A receipt created *after* that point is not in `totalBasis`, yet it withdraws from the same reserve at the same recovery ratio. Because `epochNumber` does not change post-default, the stale `defaultRecoveryEpoch` slot is silently "re-allocated" — the analog of a dangling-pointer write.

### Impact Explanation
When `defaultInstantWithdrawsFinalized` is true (i.e. `pendingInstantWithdraws != 0` at finalization), any tranche holder can, after default, call `requestInstantWithdraw` through the CDO and then `claimInstantWithdrawRequest`. The new receipt is paid `basis * defaultRecoveryPrice` directly from `defaultRecoveryReserve`. Every unit paid this way is a unit subtracted from the reserve that was only provisioned for claimants who existed at finalization (`_transferDefaultRecovery` unconditionally decrements the reserve [6](#0-5) ). The result is either (a) theft of recovery value ahead of honest defaulted claimants, or (b) underflow/revert making later legitimate `_claimDefaultedWithdrawRequest` / `_claimDefaultedInstantWithdrawRequest` claims permanently unpayable — direct insolvency of the recovery pool proportional to the attacker's post-default request size.

### Likelihood Explanation
Requires only that the vault defaults with `pendingInstantWithdraws != 0` at finalization (`defaultInstantWithdrawsFinalized == true`) and that the CDO variant still routes `requestInstantWithdraw` after default — nothing in the strategy blocks it, and post-default `requestWithdraw` is explicitly kept functional, indicating the vault is intended to keep serving requests post-default. The attacker needs only to hold tranche tokens (post-default they are worth roughly `recoveryPrice`, so the attack is near self-funded) and make two calls. Uncertainty I could not fully verify within the iteration budget: the exact gating in `IdleCDOEpochVariant.requestInstantWithdraw` (whether it reverts once `defaulted` is set) — if it unconditionally reverts post-default, the attack surface is closed at the CDO layer and severity drops to an accounting inconsistency only.

### Recommendation
In `requestInstantWithdraw`, mirror the `requestWithdraw` post-default path: if `defaultRecoveryFinalized` is set, either revert or route the request to a distinct, non-`defaultRecoveryEpoch` accounting key that is paid at par only from explicitly funded liquidity — never from `defaultRecoveryReserve` and never via `instantWithdrawsRequestsByEpoch[defaultRecoveryEpoch]`. Additionally, have `_claimDefaultedInstantWithdrawRequest` bound `claimBasis` by a snapshot taken at finalization (e.g., `instantWithdrawClaimsByEpoch[defaultEpoch]` as recorded then) so receipts created after finalization cannot enter the defaulted basis.

### Proof of Concept
Foundry fork PoC sketch:

```solidity
// 1. Deposit AA into cdoEpoch, start epoch, have a user requestInstantWithdraw.
// 2. Simulate borrower default; guardian calls finalizeDefaultRecovery(...)
//    while pendingInstantWithdraws != 0  =>  defaultInstantWithdrawsFinalized == true.
// 3. Attacker (any tranche holder) calls cdoEpoch.requestInstantWithdraw(amount).
//    In IdleCreditVault.requestInstantWithdraw this writes
//    instantWithdrawsRequestsByEpoch[attacker][epochNumber] where
//    epochNumber == defaultRecoveryEpoch.
// 4. Attacker calls cdoEpoch.claimInstantWithdrawRequest().
//    _claimDefaultedInstantWithdrawRequest reads the new basis and calls
//    _transferDefaultRecovery(attacker, basis * defaultRecoveryPrice / 1e18),
//    decrementing defaultRecoveryReserve.
// 5. Assert: defaultRecoveryReserve decreased by an amount whose basis was never
//    in totalBasis at finalization; sum of all defaulted claims now exceeds the
//    reserve, so the last honest claimant's claim reverts (insolvency).
```

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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L380-392)
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
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L679-696)
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L912-917)
```text
  function _transferDefaultRecovery(address _user, uint256 _amount) internal {
    if (_amount == 0) return;
    // Every defaulted or post-default claim consumes the isolated recovery reserve.
    defaultRecoveryReserve -= _amount;
    underlyingToken.safeTransfer(_user, _amount);
  }
```
