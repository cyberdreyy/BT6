### Title
Unfunded instant-withdraw requests inherit funded status through shared `instantWithdrawsRequests` aggregate - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
Analogous to the `fluxTransform` shared `RequestMessageHolder` bug — where a new message inherits reply headers from whichever message last touched the shared holder — `IdleCreditVault.claimInstantWithdrawRequest` pays out the aggregate `instantWithdrawsRequests[_user]` balance even though funding is tracked per epoch (`instantWithdrawsRequestsByEpoch`, `instantWithdrawClaimsByEpoch`, `pendingInstantWithdraws`). A second, unfunded instant request "leaks into" the funded context of an earlier request: one claim pays both, pulling underlying that was collected only for the funded portion.

### Finding Description
`requestInstantWithdraw` burns CDO-held strategy tokens, mints a receipt to the user, and increments three buckets: the per-user aggregate `instantWithdrawsRequests[_user]`, the per-epoch `instantWithdrawsRequestsByEpoch[_user][currentEpoch]`, and the global unfunded counter `pendingInstantWithdraws` [1](#0-0) . Funding arrives later when the CDO calls `collectInstantWithdrawFunds`, which decrements `pendingInstantWithdraws` and transfers underlying into the strategy [2](#0-1) .

`claimInstantWithdrawRequest` then reads only the aggregate `instantWithdrawsRequests[_user]` and pays it in full, with no check against `pendingInstantWithdraws`, no per-epoch funded accounting, and no requirement that the request span exactly one epoch:

```solidity
uint256 amount = instantWithdrawsRequests[_user];
_burn(_user, amount);
instantWithdrawsRequests[_user] = 0;
_transferFundedClaim(_user, amount);
``` [3](#0-2) 

The normal (non-instant) path was hardened against exactly this class of cross-epoch leakage: `requestWithdraw` reverts when a prior loss-adjusted receipt is unclaimed, precisely because "a user must claim a loss-adjusted receipt before opening a later request so its recovery epoch is preserved" [4](#0-3) . No equivalent guard exists for instant requests — the shared aggregate lets a later request silently inherit the funded status of the earlier one.

Attack sequence (running epoch, instant mode enabled):
1. Attacker (a KYC-passing lender) calls `requestInstantWithdraw(X)` in epoch N.
2. Epoch stops; CDO calls `collectInstantWithdrawFunds(X)` — underlying for X now sits in the strategy, `pendingInstantWithdraws` decremented. Attacker does **not** claim.
3. In epoch N+1, attacker calls `requestInstantWithdraw(X)` again. `instantWithdrawsRequests[attacker] = 2X`, but `pendingInstantWithdraws = X` (unfunded).
4. Attacker calls `claimInstantWithdrawRequest` via the CDO. The vault burns 2X receipts and transfers 2X underlying, though only X was ever funded.

### Impact Explanation
The extra X is paid from the strategy's underlying balance, which consists of funded-but-unclaimed claims belonging to **other** users (both normal and instant receipts are paid from the same contract balance via `_transferFundedClaim`) [2](#0-1) . Each legitimately funded claim is effectively a payable-on-demand balance stored in the strategy; the attacker converts an unfunded receipt into a funded payout, directly stealing other users' withdraw proceeds and leaving their subsequent claims underfunded/reverting — a broken one-receipt-one-payout and solvency invariant, with theft quantified as the full size of the second request.

### Likelihood Explanation
Requires only that the attacker leave a funded instant request unclaimed across an epoch boundary, then place a second request — all via unprivileged user calls (the honest manager/borrower perform `stopEpoch`/`collectInstantWithdrawFunds` in the normal course). No loss event, default, or privileged misbehavior is needed; the only precondition is that instant withdrawals are enabled and other funded claims (or donated/stray underlying) are present in the strategy.

### Recommendation
Track funded instant claims per user, mirroring the normal-request fix: either (a) revert in `requestInstantWithdraw` when `instantWithdrawsRequests[_user] != 0` (force claim-before-re-request, like the `lastWithdrawRequest`/loss-adjusted guard in `requestWithdraw`), or (b) record a per-user funded balance in `collectInstantWithdrawFunds`/`stopEpoch` and have `claimInstantWithdrawRequest` pay only `min(aggregate, funded)` while decrementing the funded bucket. Additionally consider clearing `instantWithdrawsRequestsByEpoch[_user][epoch]` on claim so post-default accounting (`_claimDefaultedInstantWithdrawRequest`) cannot double-count [5](#0-4) .

### Proof of Concept
Foundry fork sketch (anvil, mainnet fork of an instantiated `IdleCreditVault` + `IdleCDOEpochVariant` with instant withdrawals enabled):

```solidity
function testUnfundedInstantClaimDrainsFunded() public {
    // epoch N running; attacker is KYC'd and holds tranche tokens
    vm.prank(attacker);
    cdo.requestInstantWithdraw(amountX);          // strategy: instantWithdrawsRequests[attacker]=X, pendingInstant=X

    vm.prank(manager); cdo.stopEpoch();           // borrower funds; CDO calls collectInstantWithdrawFunds(X)
    // pendingInstantWithdraws == 0, strategy holds X underlying earmarked for attacker

    vm.prank(manager); cdo.startEpoch();          // epoch N+1

    vm.prank(attacker);
    cdo.requestInstantWithdraw(amountX);          // instantWithdrawsRequests[attacker]=2X, pendingInstant=X (unfunded)

    // another user's funded claim Y is also sitting unclaimed in the strategy
    uint256 stratBal = underlying.balanceOf(address(strategy)); // == X + Y

    vm.prank(attacker);
    cdo.claimInstantWithdrawRequest();            // pays 2X; funded portion was only X

    assertEq(underlying.balanceOf(attacker), 2 * amountX);      // attacker stole X
    // later: victim's claimInstantWithdrawRequest / claimWithdrawRequest reverts
    // or underpays because strategy balance was drained
}
```

Uncertainty: the exact CDO-side claim path in `IdleCDOEpochVariant` (whether it adds an epoch check before forwarding to `claimInstantWithdrawRequest`) was not fully verified within the tool-call budget; the strategy-level absence of a funded-balance check is confirmed and is the enforcement point that matters.

### Citations

**File:** contracts/strategies/idle/IdleCreditVault.sol (L243-271)
```text
  function requestWithdraw(uint256 _amount, address _user, uint256 _principal) external {
    _onlyIdleCDO();
    _ensureDefaultRecoveryInitialized();
    if (_amount == 0) return;
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
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L356-374)
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L398-403)
```text
  function collectInstantWithdrawFunds(uint256 _amount) external {
    _onlyIdleCDO();
    if (_amount == 0) return;
    pendingInstantWithdraws -= _amount;
    underlyingToken.safeTransferFrom(idleCDO, address(this), _amount);
  }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L811-837)
```text
  function _clearWithdrawClaimForEpoch(address _user, uint256 _claimEpoch, bool _isClearingApr0) internal returns (uint256 claimBasis, uint256 burnAmount) {
    (claimBasis, burnAmount) = _withdrawClaimAmountsForEpoch(_user, _claimEpoch);
    if (claimBasis == 0) return (claimBasis, burnAmount);

    uint256 normalAmount = withdrawsRequestsByEpoch[_user][_claimEpoch];
    if (normalAmount != 0) {
      withdrawsRequestsByEpoch[_user][_claimEpoch] = 0;
      // The aggregate may also include older funded receipts; clear only this epoch's piece.
      withdrawsRequests[_user] -= normalAmount;
    }
    Apr0UserData storage apr0User = apr0Users[_user];
    if (apr0User.principal != 0 && apr0User.principalEpoch == _claimEpoch) {
      if (_isClearingApr0) {
        uint256 apr0Principal = apr0User.principal;
        uint256 totalApr0Principal = apr0TotalPrincipal;
        // prepareStopEpochWithApr0 may already close the global APR0 bucket before default finalization.
        apr0TotalPrincipal = apr0Principal >= totalApr0Principal ? 0 : totalApr0Principal - apr0Principal;
      }
      apr0User.principal = 0;
      apr0User.principalEpoch = 0;
    }
    if (lastWithdrawRequest[_user] == _claimEpoch) {
      // The cleared epoch was the latest request marker. Any remaining normal/APR0 receipt
      // is older and already funded, so it can continue to the funded-claim path.
      lastWithdrawRequest[_user] = 0;
    }
  }
```
