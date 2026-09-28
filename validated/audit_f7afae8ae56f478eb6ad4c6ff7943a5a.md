### Title
Pending instant-withdraw receipts become unclaimable once `allowInstantWithdraw` is disabled - (File: contracts/IdleCDOEpochVariant.sol)

### Summary
Analogous to the auction bug where the creator claiming proceeds flips the lot status to `Claimed` and permanently blocks bidders from calling `claimBids`, `IdleCDOEpochVariant.claimInstantWithdrawRequest()` gates the *claim* path on the `allowInstantWithdraw` flag. That flag is meant to control whether *new* instant withdrawals are available, but it also blocks settlement of already-created receipts. If the manager disables instant withdrawals (or the flag is otherwise toggled off) while users hold pending `instantWithdrawsRequests`, those users' claims revert and their receipt strategy tokens plus the reserved underlying remain locked in `IdleCreditVault`.

### Finding Description
- `requestInstantWithdraw` in `IdleCreditVault` burns the CDO's strategy tokens and mints the user a receipt (`instantWithdrawsRequests[_user] += _amount`, `pendingInstantWithdraws += _amount`) at `contracts/strategies/idle/IdleCreditVault.sol:356-375`.
- The funding side pulls the underlying from the borrower via `getInstantWithdrawFunds`/`collectInstantWithdrawFunds` (`contracts/strategies/idle/IdleCreditVault.sol:398-403`).
- The user then claims through `IdleCDOEpochVariant.claimInstantWithdrawRequest()`, which executes:
  ```solidity
  _checkNotAllowed(!allowInstantWithdraw);
  IdleCreditVault(strategy).claimInstantWithdrawRequest(msg.sender);
  ```
  at `contracts/IdleCDOEpochVariant.sol:975-979`.
- `claimInstantWithdrawRequest` in the vault (`contracts/strategies/idle/IdleCreditVault.sol:380-393`) is the only path that burns the receipt and calls `_transferFundedClaim`. There is no alternative claim path for funded instant receipts — even the post-default route (`_claimDefaultedInstantWithdrawRequest`) is reached only through this same externally gated function.

So a single honest privileged toggle (`setInstantWithdrawParams(..., false)`) transitions the "auction status" to a state where all pending instant-withdraw claimants revert with `NotAllowed`, mirroring `_revertIfLotNotSettled` reverting bidders once the lot is `Claimed`. The user's principal was already burned from the CDO and exists only as a receipt token; if the flag is never re-enabled the freeze is permanent.

### Impact Explanation
Temporary-to-permanent freezing of user funds. Every holder of a pending instant-withdraw receipt is unable to redeem underlying that has already been collected into the vault for them. Loss is quantified as `instantWithdrawsRequests[user]` per affected user (up to `pendingInstantWithdraws` aggregate), and persists until governance/manager re-enables the flag — which may never happen (e.g., feature sunset or the flag being disabled as part of wind-down).

### Likelihood Explanation
Requires only a legitimate admin action: disabling instant withdrawals is a normal operational lever (`setInstantWithdrawParams`), plausibly used during risk-off periods, low-liquidity conditions, or deprecation of the feature — exactly when pending receipts are most likely to exist. No malicious actor is needed; the same "honest privileged sequencing" pattern as the auction creator claiming proceeds before bidders.

### Recommendation
Do not gate the claim path on `allowInstantWithdraw`. Restrict the flag to `requestInstantWithdraw` (new requests) and always allow `claimInstantWithdrawRequest` to settle existing funded receipts — the same fix pattern as Axis-Fi replacing the `Claimed` status with a boolean so settled auctions stay claimable. Concretely, remove `_checkNotAllowed(!allowInstantWithdraw)` from `claimInstantWithdrawRequest` in `IdleCDOEpochVariant.sol` (or move the check into the request path only), since the vault-level function already no-ops for users with zero receipts.

### Proof of Concept
Foundry fork PoC (schematic, against a deployed/harnessed `IdleCDOEpochVariant` + `IdleCreditVault`):

```solidity
function test_InstantClaimBlockedAfterFlagDisabled() external {
    // setup: deposit AA, run an epoch where APR delta makes instant withdraws available
    _startEpochAndCheckPrices(0);
    _stopEpochAndCheckPrices(0, apr, _expectedFundsEndEpoch());

    uint256 mintedAA = idleCDO.depositAA(10_000 * ONE_SCALE);
    deal(defaultUnderlying, borrower, 1_000_000 * ONE_SCALE);

    // user requests instant withdraw (epoch running path per allowInstantWithdraw)
    _startEpochAndCheckPrices(1);
    vm.prank(user);
    cdoEpoch.requestWithdraw(mintedAA / 2, address(AAtranche));

    // borrower funds instant withdrawals
    vm.warp(block.timestamp + cdoEpoch.instantWithdrawDelay() + 1);
    vm.prank(manager);
    cdoEpoch.getInstantWithdrawFunds();

    // BEFORE user claims, manager disables instant withdraws (honest op)
    vm.prank(manager);
    cdoEpoch.setInstantWithdrawParams(delay, minAmount, false); // allowInstantWithdraw = false

    // user's funded receipt is now unclaimable
    vm.prank(user);
    vm.expectRevert(abi.encodeWithSelector(NotAllowed.selector));
    cdoEpoch.claimInstantWithdrawRequest();

    // receipt still exists in the vault, funds locked
    assertGt(strategy.instantWithdrawsRequests(user), 0);
}
```

Note on confidence: I verified the gating check at `IdleCDOEpochVariant.sol:977` and that the vault claim (`IdleCreditVault.sol:380-393`) is the sole redemption path for instant receipts. I was not able to fully confirm whether `requestInstantWithdraw` shares the same flag gate or whether a separate documented "rescue" path exists for stranded instant receipts; if the flag disabling is an explicitly intended freeze (rather than just a new-request gate), this degrades to a design trade-off rather than a bug.