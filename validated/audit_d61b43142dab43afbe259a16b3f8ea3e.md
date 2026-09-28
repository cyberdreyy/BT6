### Title
Claim payouts are bound to the requesting address with no receiver override, permanently freezing funds when the token transfer to that address reverts - ([File: contracts/strategies/idle/IdleCreditVault.sol](contracts/strategies/idle/IdleCreditVault.sol))

### Summary
Withdraw receipts in `IdleCreditVault` are strictly address-bound: strategy tokens cannot be transferred by users (`_transfer` only allows `msg.sender == idleCDO`, and `canTransfer` is permanently disabled), and every payout path — `_transferFundedClaim`, `_transferDefaultRecovery`, `claimInstantWithdrawRequest` — pushes underlyings to `_user`, which is always the original requester forwarded by the CDO. If the underlying `safeTransfer(_user, amount)` reverts for a given user (e.g., the address is blacklisted in USDC/USDT, or is a contract whose token handling rejects the transfer), the claim reverts forever and the funded underlying can never be withdrawn, since there is no way to specify an alternate receiver or move the receipt. The same pattern exists in `IdleCDOEpochQueue.claimWithdrawRequest` / `claimDepositRequest`, which pay `msg.sender` only. Notably, `DefaultDistributor.claim(address _to)` already implements the recommended fix — a caller-specified recipient.

### Finding Description
- `IdleCreditVault.claimWithdrawRequest(_user)` and `claimInstantWithdrawRequest(_user)` are only callable by the IdleCDO, which passes `msg.sender` as `_user`. All payout helpers (`_claimFundedWithdrawRequest` → `_transferFundedClaim`, `_claimPostDefaultWithdrawRequest` / `_claimDefaultedWithdrawRequest` / `_claimDefaultedInstantWithdrawRequest` → `_transferDefaultRecovery`) do `underlyingToken.safeTransfer(_user, _amount)` at `IdleCreditVault.sol:906` and `IdleCreditVault.sol:916`.
- Receipt tokens are soulbound to the requester: `_transfer` reverts unless `msg.sender == idleCDO` (`IdleCreditVault.sol:939-942`), and `setCanTransfer` can only ever set `false` (`IdleCreditVault.sol:948-951`).
- The queue has the same shape: `claimWithdrawRequest` zeroes `userWithdrawalsEpochs[msg.sender][_epoch]` then `safeTransfer(msg.sender, ...)` (`IdleCDOEpochQueue.sol:401-410`); `claimDepositRequest` transfers tranche tokens to `msg.sender` only (`IdleCDOEpochQueue.sol:381-388`).
- Therefore a user whose address cannot receive `underlying` (blacklist) has no recourse: the accounting state is intact but every claim transaction reverts at the final `safeTransfer`, and the receipt cannot be sold or redirected.

### Impact Explanation
Permanent freezing of user funds. The quantified loss is the full claim basis of the affected user — the entire funded withdraw request (or default-recovery / post-default claim), which remains trapped in the strategy/queue contract balance. The funded claim amount is reserved for that user (`_transferFundedClaim` even guards it against the default reserve), so no other party can recover it either. This directly mirrors the referenced bug: the payout target is fixed at request time and cannot be changed when the push transfer fails for that specific recipient.

### Likelihood Explanation
Requires the requesting address to become unable to receive `underlying` after creating the receipt — most plausibly a USDC blacklist event (Circle blacklisting is an established real-world occurrence) hitting a previously KYC-passed lender between `requestWithdraw` and claim, or a smart-wallet user whose receive path breaks. Both the buffer-phase request and the post-epoch claim are normal unprivileged user flows, and no existing guard (skim, reserve checks, epoch gating) provides an escape hatch since receipts are non-transferable.

### Recommendation
Allow the receipt owner to specify a payout recipient. Concretely, add a `recipient` parameter to `IdleCDOEpochVariant.claimWithdrawRequest`/`claimInstantWithdrawRequest` and thread it through to `_transferFundedClaim`/`_transferDefaultRecovery` (or keep the burn keyed to `msg.sender` while sending to `recipient`), and similarly add a `_to` parameter to `IdleCDOEpochQueue.claimWithdrawRequest`/`claimDepositRequest` — mirroring the existing `DefaultDistributor.claim(address _to)` pattern in `DefaultDistributor.sol:35-41`.

### Proof of Concept
Foundry fork PoC (USDC as underlying):

```solidity
function testClaimFrozenForBlacklistedUser() external {
    address user = makeAddr("user");
    _depositWithUser(user, 10_000 * ONE_SCALE, true);
    uint256 bal = IERC20(AAtranche).balanceOf(user);
    vm.prank(user);
    cdoEpoch.requestWithdraw(bal / 2, address(AAtranche));

    _startEpochAndCheckPrices(0);
    _stopEpochAndCheckPrices(0, initialProvidedApr, _expectedFundsEndEpoch());

    // USDC blacklists the user after the request is funded
    vm.prank(USDC_BLACKLIST_ROLE);
    FiatTokenV2(defaultUnderlying).blacklist(user);

    // Claim is now permanently impossible: burn happens in same tx as the
    // reverting safeTransfer, and the receipt cannot be transferred or redirected
    vm.prank(user);
    vm.expectRevert(); // USDC blacklist revert
    cdoEpoch.claimWithdrawRequest();

    // No alternative path exists: _transfer reverts for non-idleCDO senders
    vm.prank(user);
    vm.expectRevert(abi.encodeWithSelector(NotAllowed.selector));
    IERC20Detailed(strategyToken).transfer(makeAddr("friend"), 1);
}
```

Equivalent PoC for `IdleCDOEpochQueue.claimWithdrawRequest`: process the user's request, blacklist the user, and observe `claimWithdrawRequest(claimEpoch)` revert permanently at `IdleCDOEpochQueue.sol:409`.

Uncertainty noted: I was unable to view the exact call sites in `IdleCDOEpochVariant.sol` confirming `msg.sender` is forwarded as `_user` (grep returned match counts but not line content), though `claimWithdrawRequest`'s `_onlyIdleCDO` guard and the `_user` parameter, plus the test at `IdleCreditVault.t.sol:4649` showing `cdoEpoch.claimWithdrawRequest()` called from the user's prank, are consistent with that wiring.