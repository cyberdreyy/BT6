### Title
Post-default instant withdraw requests are misclassified as defaulted-epoch receipts, letting a lender claim at `defaultRecoveryPrice` and drain the recovery reserve - ([File: contracts/strategies/idle/IdleCreditVault.sol])

### Summary
`requestWithdraw` has an explicit post-default path (`postDefaultRequests`) that prevents new receipts created after `finalizeDefault` from being haircut (or over-paid) as defaulted-epoch receipts. `requestInstantWithdraw` has no equivalent branch: it unconditionally records the request into `instantWithdrawsRequestsByEpoch[_user][epochNumber]`. Because `finalizeDefault` sets `defaultRecoveryEpoch = epochNumber` and the epoch counter is not advanced afterward, any instant request opened after finalization is stored under `defaultRecoveryEpoch`. When the user calls `claimInstantWithdrawRequest`, `_claimDefaultedInstantWithdrawRequest` clears that entry and pays `claimBasis * defaultRecoveryPrice / RECOVERY_FULL` out of `defaultRecoveryReserve` — a bucket that was sized to cover only pre-default liabilities. When recovery was over-funded (`defaultRecoveryPrice > RECOVERY_FULL`, explicitly supported), the new claimant is paid more than their post-haircut basis, directly draining reserve owed to legitimate defaulted claimants, and corrupting `pendingInstantWithdraws` / `instantWithdrawClaimsByEpoch` accounting.

### Finding Description
In `requestWithdraw` (line 247-257), post-finalization requests are isolated: [1](#0-0) 

But `requestInstantWithdraw` (line 356-375) performs the same accounting for pre- and post-default requests, keying the receipt to `epochNumber`: [2](#0-1) 

`claimInstantWithdrawRequest` then routes any receipt recorded under `defaultRecoveryEpoch` through `_claimDefaultedInstantWithdrawRequest`, which pays `claimBasis * defaultRecoveryPrice / RECOVERY_FULL` and decrements `defaultRecoveryReserve`, `pendingInstantWithdraws`, and `instantWithdrawClaimsByEpoch[defaultEpoch]` (lines 380-393, 842-856). A post-default request was minted at par against the already-haircut NAV, so applying `defaultRecoveryPrice` again is wrong in both directions: it underpays the user when `defaultRecoveryPrice < RECOVERY_FULL` and overpays (from reserve) when `defaultRecoveryPrice > RECOVERY_FULL`. The over-recovery case is reachable — `testFinalizeDefaultDistributesOverRecoveryProRata` asserts `defaultRecoveryPrice() > ONE_TRANCHE` is supported.

### Impact Explanation
When a default is finalized with an over-funded recovery, an unprivileged lender can request an instant withdraw post-finalization and claim `request * defaultRecoveryPrice / RECOVERY_FULL` from `defaultRecoveryReserve`, extracting more underlying than their receipt's fair value. Each such claim subtracts from the reserve funded for defaulted-epoch claimants; once drained (or once `pendingInstantWithdraws` saturates to 0), honest defaulted claimants' claims revert or are reduced — direct theft of unclaimed recovery funds and permanent freezing of remaining claims. When `defaultRecoveryPrice < RECOVERY_FULL`, the same flaw causes the post-default instant claimant to be haircut a second time (double-haircut loss to that user) and corrupts `pendingInstantWithdraws`/`instantWithdrawClaimsByEpoch` downward, breaking the `prefundedReserve` invariant used by loss/default accounting.

### Likelihood Explanation
Requires: (a) a borrower default with `finalizeDefault` called by the honest owner — an expected lifecycle event; (b) for the theft variant, an over-funded recovery (`defaultRecoveryPrice > 1`), which the protocol explicitly supports and tests; (c) the CDO still permitting `requestInstantWithdraw` after finalization — `requestInstantWithdraw` has no `defaulted`/`defaultRecoveryFinalized` gate in the vault, and no post-default routing analogous to `postDefaultRequests`. The attacker is a normal KYC-passing lender; no privileged role is needed. The reserve-misclassification variant (underpayment/accounting corruption) triggers at any recovery price.

### Recommendation
Mirror the `requestWithdraw` fix in `requestInstantWithdraw`: when `defaultRecoveryFinalized`, route new instant requests into a post-default bucket (e.g. `postDefaultInstantRequests`) that is paid 1:1 via `_transferDefaultRecovery` or `_transferFundedClaim` without touching `instantWithdrawsRequestsByEpoch[user][defaultRecoveryEpoch]`, or revert if instant withdraws are not supported post-default. `claimInstantWithdrawRequest` should consume that bucket first, then `_claimDefaultedInstantWithdrawRequest`, then the funded remainder.

### Proof of Concept
Foundry fork PoC sketch (following the existing `test/foundry/IdleCreditVault.t.sol` harness):

```solidity
function testPostDefaultInstantRequestMisclassified() external {
    address honestPending = makeAddr('honest-pending');
    address attacker = makeAddr('attacker');
    uint256 amount = 10_000 * ONE_SCALE;

    _depositWithUser(honestPending, amount, true);
    _depositWithUser(attacker, amount, true);

    // Honest user holds a defaulted instant receipt from the default epoch.
    vm.prank(honestPending);
    uint256 honestBasis = cdoEpoch.requestWithdraw(0, address(AAtranche)); // instant mode enabled

    _startEpochAndCheckPrices(0);
    _stopEpochAndCheckPrices(0, initialProvidedApr, 0);
    _checkDefault();

    // Owner finalizes with an OVER-funded recovery (price > RECOVERY_FULL),
    // as in testFinalizeDefaultDistributesOverRecoveryProRata.
    IdleCreditVault vault = IdleCreditVault(address(strategy));
    uint256 totalBasis = cdoEpoch.getContractValue() + cdoEpoch.expectedEpochInterest()
        - cdoEpoch.pendingWithdrawFees() + vault.defaultPendingClaimBasis();
    uint256 recovered = totalBasis * 11 / 10;
    deal(defaultUnderlying, manager, recovered);
    vm.startPrank(manager);
    IERC20Detailed(defaultUnderlying).approve(address(strategy), recovered);
    cdoEpoch.finalizeDefault(recovered, manager);
    vm.stopPrank();
    assertGt(vault.defaultRecoveryPrice(), ONE_TRANCHE);
    assertEq(vault.defaultRecoveryEpoch(), vault.epochNumber()); // same epoch key

    // Attacker opens an instant withdraw AFTER finalization. Because
    // requestInstantWithdraw has no postDefaultRequests path, the receipt is
    // stored under defaultRecoveryEpoch and later claimed at recoveryPrice.
    vm.startPrank(attacker);
    uint256 basis = cdoEpoch.requestInstantWithdraw(...); // via CDO entry point
    uint256 balPre = IERC20Detailed(defaultUnderlying).balanceOf(attacker);
    cdoEpoch.claimInstantWithdrawRequest();
    vm.stopPrank();

    uint256 paid = IERC20Detailed(defaultUnderlying).balanceOf(attacker) - balPre;
    // BUG: paid == basis * defaultRecoveryPrice / RECOVERY_FULL > basis,
    // withdrawn from defaultRecoveryReserve meant for defaulted claimants.
    assertGt(paid, basis);

    // Consequence: reserve is drained and honestPending's haircut claim
    // now reverts on _transferDefaultRecovery underflow / pays less than
    // honestBasis * defaultRecoveryPrice / RECOVERY_FULL.
}
```

Caveat: I was unable to fully trace `IdleCDOEpochVariant`'s user-facing `requestInstantWithdraw`/`claimInstantWithdrawRequest` entry points to confirm there is no upstream `defaulted`/`defaultRecoveryFinalized` gate; if the CDO reverts instant requests after default, only the accounting-corruption variant (via direct vault calls, which are `_onlyIdleCDO`-gated) is excluded and severity drops. The exact CDO call signature in the PoC should be matched to `IdleCDOEpochVariant.sol`.

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
