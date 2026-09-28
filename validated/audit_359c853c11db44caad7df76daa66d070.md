### Title
Cross-epoch instant-withdraw receipts escape the default haircut and overpay at par, draining other claimants' funded balance - ([File: contracts/strategies/idle/IdleCreditVault.sol](contracts/strategies/idle/IdleCreditVault.sol))

### Summary
The tar-rs analog is a scope-escape: `Builder::append_dir_all()` follows symlinks so archived content escapes the intended source root. In `IdleCreditVault`, instant-withdraw receipts are tracked per-epoch (`instantWithdrawsRequestsByEpoch[user][epoch]`), but the default-finalization basis and the defaulted-claim path only look at the *current* epoch bucket. An unprivileged lender who lets a partially-funded instant receipt from an earlier epoch roll into a later epoch — then stacks a second instant request in the default epoch — gets the stale receipt paid at par even though it was never fully funded, escaping the recovery haircut applied to everyone else and spending underlying reserved for other claimants.

### Finding Description
`defaultPendingClaimBasis()` adds instant claims only for the current epoch and only when `pendingInstantWithdraws != 0`:

```solidity
basis = pendingWithdraws;
if (pendingInstantWithdraws != 0) {
    basis += instantWithdrawClaimsByEpoch[epochNumber];
}
``` [1](#0-0) 

`instantWithdrawsRequestsByEpoch[user][epoch]` is written per request epoch, but `pendingInstantWithdraws` is a single global remainder across all epochs. After default finalization, `claimInstantWithdrawRequest` haircuts only `instantWithdrawsRequestsByEpoch[user][defaultRecoveryEpoch]` via `_claimDefaultedInstantWithdrawRequest`, then pays the *entire remaining* `instantWithdrawsRequests[user]` aggregate — including receipts recorded in earlier epochs — at par through `_transferFundedClaim`. [2](#0-1) [3](#0-2) [4](#0-3) 

Additionally, `_defaultPrefundedInstantReserve()` compares `instantWithdrawClaimsByEpoch[epochNumber]` (current-epoch basis only) against `pendingInstantWithdraws` (global remainder). When older unfunded instant receipts exist, `pendingInstant > instantBasis` yields `prefundedReserve = 0`, so underlying already collected for the older receipts is not added to the recovery reserve either — it is simultaneously excluded from the haircut basis *and* left spendable at par. [5](#0-4) 

Nothing forces a user to claim an instant receipt before `requestInstantWithdraw` mints a new one in a later epoch — `requestInstantWithdraw` has no existing-request check, unlike the post-default `requestWithdraw` guard. [6](#0-5) 

### Impact Explanation
The broken invariant is "one receipt, one haircut-scoped payout": all receipts outstanding at default should settle at `defaultRecoveryPrice`. Instead, stale-epoch instant receipts bypass the recovery ratio entirely and are paid 1:1. The payout comes from the strategy's non-reserve balance, which is fungible with funded claims of other users (the `_transferFundedClaim` guard only protects `defaultRecoveryReserve`, not other claimants' collected funds). The attacker extracts up to the unfunded remainder of their earlier receipt at par while honest defaulted claimants receive only `claimBasis * defaultRecoveryPrice / RECOVERY_FULL` — a direct transfer of value, quantified as `staleReceiptUnfundedPortion * (RECOVERY_FULL - defaultRecoveryPrice) / RECOVERY_FULL` in stolen underlying, plus potential insolvency for late claimers whose funded receipts are drained.

### Likelihood Explanation
Requires: instant withdrawals enabled (`allowInstantWithdraw`), an epoch where the collected instant funds cover only part of the queue (partial prefunding, demonstrated in `testFinalizeDefaultHaircutsPendingInstantRedeems`), the user not claiming between epochs, a second instant request in a later epoch, and a subsequent borrower default with `pendingInstantWithdraws != 0` at finalization. All steps use only unprivileged user actions sequenced around honest manager/borrower calls. The honest-manager operations (`startEpoch`, `getInstantWithdrawFunds`, `stopEpoch`/`finalizeDefault`) are routine flows, so likelihood is moderate.

### Recommendation
- Make `defaultPendingClaimBasis()` include the full outstanding instant claim base (all epochs), not just `instantWithdrawClaimsByEpoch[epochNumber]` — e.g., track a global `totalInstantClaims` or iterate per-user.
- In `_claimDefaultedInstantWithdrawRequest`, haircut the user's entire `instantWithdrawsRequests[user]` balance at `defaultRecoveryPrice`, not just the default-epoch bucket; or revert `requestInstantWithdraw`/`requestWithdraw` (CDO-side) when the user has an unfunded instant receipt from a prior epoch, mirroring the `requestWithdraw` loss-receipt guard.
- Fix `_defaultPrefundedInstantReserve` to compare global instant claims vs `pendingInstantWithdraws` so prefunded amounts for older receipts are correctly counted into the reserve.

### Proof of Concept
Foundry fork test (extend `test/foundry/IdleCreditVault.t.sol`):

```solidity
function testStaleInstantReceiptEscapesDefaultHaircut() external {
    uint256 amount = 10_000 * ONE_SCALE;
    address attacker = makeAddr('attacker');
    address victim = makeAddr('victim');
    _depositWithUser(attacker, amount, true);
    _depositWithUser(victim, amount, true);

    // Epoch 0 ends with lower APR -> instant withdraws enabled
    _startEpochAndCheckPrices(0);
    _stopEpochAndCheckPrices(0, initialProvidedApr / 2, _expectedFundsEndEpoch());

    // Attacker requests instant withdraw in epoch 1
    vm.prank(attacker);
    uint256 receipt1 = cdoEpoch.requestWithdraw(0, address(AAtranche));

    // Epoch 1: strategy cash only partially covers the instant queue
    _startEpochAndCheckPrices(1);
    vm.warp(block.timestamp + cdoEpoch.instantWithdrawDelay() + 1);
    // borrower only funds part of pendingInstantWithdraws
    IdleCreditVault cv = IdleCreditVault(address(strategy));
    uint256 pending = cv.pendingInstantWithdraws();
    deal(defaultUnderlying, borrower, pending / 2);
    vm.prank(manager);
    cdoEpoch.getInstantWithdrawFunds(); // partially funded, pendingInstantWithdraws > 0
    // attacker does NOT claim; receipt1 stays in instantWithdrawsRequestsByEpoch[epoch1]

    // Epoch 2 ends with low APR again -> second instant request allowed
    _stopEpochAndCheckPrices(1, initialProvidedApr / 4, _expectedFundsEndEpoch());
    vm.prank(attacker);
    cdoEpoch.requestWithdraw(0, address(AAtranche)); // stacks into epoch-2 bucket

    _startEpochAndCheckPrices(2);
    // borrower defaults -> finalizeDefault with recoveryRatio < 1
    _stopEpochAndCheckPrices(2, initialProvidedApr / 4, 0);
    uint256 basis = cv.defaultPendingClaimBasis();
    // BUG: basis only includes instantWithdrawClaimsByEpoch[epoch2],
    // the unfunded part of receipt1 (epoch1 bucket) is missing.
    deal(defaultUnderlying, manager, basis * 7e17 / 1e18);
    vm.startPrank(manager);
    underlying.approve(address(strategy), type(uint256).max);
    cdoEpoch.finalizeDefault(basis * 7e17 / 1e18, manager);
    vm.stopPrank();

    // Attacker claims: epoch-2 bucket is haircut at 70%,
    // but the stale epoch-1 remainder is paid at PAR via _transferFundedClaim.
    uint256 balPre = underlying.balanceOf(attacker);
    vm.prank(attacker);
    cdoEpoch.claimInstantWithdrawRequest();
    uint256 got = underlying.balanceOf(attacker) - balPre;
    // got > fullBasis * defaultRecoveryPrice / RECOVERY_FULL
    // the excess is drawn from funds backing victim's funded claims.
}
```

Key assertion: `cv.instantWithdrawsRequestsByEpoch(attacker, epoch1)` is never consulted during finalization or claim, so the attacker is paid `receipt1` in full while `defaultRecoveryPrice < RECOVERY_FULL`, directly violating loss socialization and reducing funds available to other claimants.

### Citations

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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L644-649)
```text
  function defaultPendingClaimBasis() public view returns (uint256 basis) {
    basis = pendingWithdraws;
    if (pendingInstantWithdraws != 0) {
      basis += instantWithdrawClaimsByEpoch[epochNumber];
    }
  }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L716-723)
```text
  function _defaultPrefundedInstantReserve() internal view returns (uint256 prefundedReserve) {
    uint256 pendingInstant = pendingInstantWithdraws;
    if (pendingInstant == 0) return prefundedReserve;
    uint256 instantBasis = instantWithdrawClaimsByEpoch[epochNumber];
    if (instantBasis > pendingInstant) {
      prefundedReserve = instantBasis - pendingInstant;
    }
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
