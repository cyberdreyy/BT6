### Title
Post-default instant withdraw receipts are paid from `defaultRecoveryReserve`, draining funds reserved for legitimate default claimants - ([File: contracts/strategies/idle/IdleCreditVault.sol])

### Summary
`requestWithdraw` was patched to handle the post-default-finalization state (`defaultRecoveryFinalized == true`) by escrowing requests as `postDefaultRequests` and forbidding them while old receipts are pending. `requestInstantWithdraw` was not given the same treatment. After default finalization it still mints a receipt and records it under the *current* `epochNumber` — which is identical to `defaultRecoveryEpoch`, since the epoch counter no longer advances. When the user later calls `claimInstantWithdrawRequest`, `_claimDefaultedInstantWithdrawRequest` clears that receipt using the finalized `defaultRecoveryPrice` and pays out of `defaultRecoveryReserve`, even though the reserve and recovery price were computed from a claim basis that never included these post-default requests.

### Finding Description
In `IdleCreditVault.requestWithdraw`, the `defaultRecoveryFinalized` branch (lines 247-258) routes post-default requests into `postDefaultRequests` and blocks users with pending receipts. `requestInstantWithdraw` (lines 356-375) has no such branch: it unconditionally burns the CDO's strategy tokens, mints a receipt to `_user`, and increments `instantWithdrawsRequests[_user]`, `instantWithdrawsRequestsByEpoch[_user][epochNumber]` and `instantWithdrawClaimsByEpoch[epochNumber]`.

`finalizeDefaultRecovery` sets `defaultRecoveryEpoch = epochNumber` (line 693) and `defaultInstantWithdrawsFinalized = pendingInstantWithdraws != 0` (line 696), and sizes `defaultRecoveryReserve` exactly against the claim basis captured in `defaultPendingClaimBasis` (lines 644-649). After finalization `epochNumber` stays frozen, so a post-default instant request lands in `instantWithdrawsRequestsByEpoch[user][defaultRecoveryEpoch]`, indistinguishable from a defaulted-epoch receipt.

When the attacker calls `claimInstantWithdrawRequest`, `_claimDefaultedInstantWithdrawRequest` (lines 842-856) reads that receipt as a defaulted-epoch claim, burns the receipt tokens, and pays `claimBasis * defaultRecoveryPrice / RECOVERY_FULL` via `_transferDefaultRecovery`, which decrements `defaultRecoveryReserve` (line 915). The reserve was never sized for this claim, so each such request spends recovery funds earmarked for legitimate defaulted-epoch claimants, who later revert in `_transferDefaultRecovery`/`safeTransfer` on insufficient balance.

The one mitigating precondition is whether `pendingInstantWithdraws != 0` at finalization: `defaultInstantWithdrawsFinalized` must be true for `_claimDefaultedInstantWithdrawRequest` to run. That is satisfied whenever any current-epoch instant request was still unfunded when the borrower defaulted — a normal condition, since the strategy only sets it false when the instant queue was fully funded.

### Impact Explanation
Direct theft of the isolated default-recovery reserve. For every unit an attacker requests via instant withdraw after finalization, they receive `defaultRecoveryPrice` per unit out of `defaultRecoveryReserve`, up to draining it entirely. Legitimate holders of defaulted-epoch normal and instant receipts (priced into `defaultPendingClaimBasis`) are then permanently unable to claim: `_transferDefaultRecovery` underflows/reverts once the reserve is exhausted. Loss is bounded by `min(attacker request, defaultRecoveryReserve)` and is permanent.

### Likelihood Explanation
Preconditions: the pool must reach `defaulted` state with a finalized recovery and `defaultInstantWithdrawsFinalized == true`, and the IdleCDO variant must still forward `requestInstantWithdraw` after finalization (this is the main unverified point — the strategy itself has no gate, but I could not confirm `IdleCDOEpochVariant`'s instant-withdraw entry checks post-default within available search budget). Attacker needs only tranche tokens to request an instant withdraw — no privileged role, matching the unprivileged-attacker model. The mechanism closely mirrors the CVE pattern: an incomplete fix (post-default handling added to `requestWithdraw` but not `requestInstantWithdraw`) leaves one specific path exploitable.

### Recommendation
Mirror the `defaultRecoveryFinalized` guard in `requestInstantWithdraw`: either revert when `defaultRecoveryFinalized` is true, or route the request into `postDefaultRequests`-style accounting that cannot consume `defaultRecoveryReserve`. Alternatively, record post-default instant requests under a distinct epoch key so `_claimDefaultedInstantWithdrawRequest` cannot match them. Add a test that finalizes a default with unfunded instant receipts, then requests a new instant withdraw and asserts it cannot draw from the reserve.

### Proof of Concept
```solidity
// Foundry fork test (pool = IdleCDOEpochVariant + IdleCreditVault)
// Assumes an unfunded instant request exists at default so
// defaultInstantWithdrawsFinalized == true.

function testPostDefaultInstantDrainsRecovery() external {
    // 1. victim deposits AA, attacker deposits AA
    idleCDO.depositAA(100_000e6);               // victim (this contract)
    uint256 attackerShares = _depositWithUser(attacker, 10_000e6);

    // 2. epoch 0: victim files an instant withdraw that stays unfunded
    _startEpochAndCheckPrices(0);
    vm.prank(victim);
    cdoEpoch.requestInstantWithdraw(victimAmount, address(AAtranche));

    // 3. borrower defaults at stopEpoch; owner finalizes with partial recovery
    _stopEpochDefault();                        // strategy.defaulted path
    vm.prank(owner);
    cdoEpoch.finalizeDefault(recoveredAmount, recoverySource);
    // now: defaultRecoveryFinalized, defaultRecoveryEpoch == epochNumber,
    //      defaultRecoveryReserve sized for victim's basis only

    // 4. attacker files an instant withdraw AFTER finalization
    vm.prank(attacker);
    cdoEpoch.requestInstantWithdraw(attackerShares, address(AAtranche));
    // lands in instantWithdrawsRequestsByEpoch[attacker][defaultRecoveryEpoch]

    // 5. attacker claims and is paid from defaultRecoveryReserve
    vm.prank(attacker);
    cdoEpoch.claimInstantWithdrawRequest();     // succeeds, drains reserve

    // 6. victim's legitimate defaulted instant claim now reverts /
    //    pays less than claimBasis * defaultRecoveryPrice
    vm.prank(victim);
    vm.expectRevert();
    cdoEpoch.claimInstantWithdrawRequest();
}
```

Key strategy lines: `requestInstantWithdraw` lacking the default guard [1](#0-0) , the guarded `requestWithdraw` counterpart [2](#0-1) , epoch-keyed claim clearing paid from the reserve [3](#0-2) , and reserve finalization [4](#0-3) .

### Citations

**File:** contracts/strategies/idle/IdleCreditVault.sol (L247-258)
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
    }
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L688-696)
```text
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
