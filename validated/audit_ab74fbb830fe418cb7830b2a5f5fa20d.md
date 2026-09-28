### Title
Unfunded instant-withdraw receipts can drain already-funded instant withdrawals, leaving earlier claimants frozen or haircut - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`IdleCreditVault` tracks instant withdrawals with two separate ledgers: `pendingInstantWithdraws` (the still-unfunded remainder the borrower must supply via `collectInstantWithdrawFunds`) and per-user `instantWithdrawsRequests[_user]` (the aggregate receipt across all epochs). When `getInstantWithdrawFunds`/`collectInstantWithdrawFunds` pull funded underlying into the vault, the vault does not earmark it for the specific funded receipts. `claimInstantWithdrawRequest` then pays out the caller's *entire* `instantWithdrawsRequests` balance — including later, never-funded requests — from whatever underlying the vault happens to hold. A later instant-withdraw requester can therefore consume underlying that was already funded for earlier claimants, causing their claims to revert on insufficient balance or to be haircutted if the pool defaults before the new pending amount is funded.

### Finding Description
The instant-withdraw lifecycle:

1. During buffer, `IdleCDOEpochVariant.requestWithdraw` routes to `IdleCreditVault.requestInstantWithdraw`, which mints the user a 1:1 strategy-token receipt and increments both `instantWithdrawsRequests[_user]` and the global `pendingInstantWithdraws` (IdleCreditVault.sol:356-375).
2. After the new epoch starts and `instantWithdrawDelay` passes, the manager calls `getInstantWithdrawFunds`, which pulls `pendingInstantWithdraws` underlying from the borrower into the vault via `collectInstantWithdrawFunds` and zeroes `pendingInstantWithdraws` (IdleCreditVault.sol:398-403).
3. `claimInstantWithdrawRequest` then burns `instantWithdrawsRequests[_user]` and pays `_transferFundedClaim(_user, amount)` — the full aggregate receipt — with no check that the caller's receipt corresponds to a funded epoch (IdleCreditVault.sol:380-393). The CDO-side wrapper only checks the global `allowInstantWithdraw` flag (IdleCDOEpochVariant.sol:975-979).

The broken invariant is one-receipt-one-funded-payout: funding is tracked globally (`pendingInstantWithdraws`, `instantWithdrawClaimsByEpoch`), but payout is tracked per-user aggregate (`instantWithdrawsRequests`). A receipt created *after* the funding pull increases `instantWithdrawsRequests[user]` without any corresponding vault balance, yet is paid from the vault immediately.

Attack sequence (unprivileged KYC'd lender):

1. Epoch N running; APR dropped so `_isInstantWithdrawEnabled()` path is active. Honest user A requests instant withdraw of 100 underlying; `instantWithdrawsRequests[A] = 100`, `pendingInstantWithdraws = 100`.
2. Manager calls `getInstantWithdrawFunds`; borrower supplies 100 → vault holds 100, `pendingInstantWithdraws = 0`, `allowInstantWithdraw = true`. A does not claim yet (the protocol explicitly supports delayed claims; see `testClaimInstantWithdrawRequestAfterAnEpoch`).
3. Attacker B deposits into a tranche, calls `requestWithdraw` on the instant path for 100. `instantWithdrawsRequests[B] = 100`, `pendingInstantWithdraws = 100` — unfunded, awaiting the next `getInstantWithdrawFunds`.
4. B immediately calls `claimInstantWithdrawRequest`. Vault burns B's 100 receipt and `_transferFundedClaim` sends the 100 underlying that was funded *for A*. B exits whole.
5. A's claim now reverts on `safeTransfer` (vault empty). If the borrower later funds B's pending request, A is merely delayed; but if the epoch stops or the borrower defaults before funding, A's funded claim is permanently frozen or pushed into the defaulted-recovery haircut (`_claimDefaultedInstantWithdrawRequest` / `defaultRecoveryPrice`), while B — who queued after funding — escaped at par with underlying that was never his.

### Impact Explanation
Direct theft / conversion of funded withdrawals: the attacker swaps an unfunded receipt for underlying already collected for earlier claimants. In the worst case (borrower default or pool close before the attacker's pending request is funded), the honest funded claimant's payout is permanently frozen or reduced to `claimBasis * defaultRecoveryPrice / RECOVERY_FULL`, i.e. loss up to the full funded instant-withdraw amount, while the attacker — who burned tranche tokens that were never backed by collected funds at claim time — keeps 100% of the payout. This mirrors the Indodax bug class: the withdrawal pipeline pays out against the wrong backing, letting one withdrawal consume another's funds.

### Likelihood Explanation
Requires (a) an instant-withdraw-enabled vault (APR decreased by more than `instantWithdrawAprDelta` between epochs, non-programmable borrower), (b) at least one funded-but-unclaimed instant withdrawal, and (c) a KYC-passing attacker holding tranche tokens. All are normal operating conditions — the delay between `getInstantWithdrawFunds` and user claims is by design (claims can happen "at any time even if a new epoch started"). No privileged cooperation needed; ordering only requires the attacker act between funding and the victim's claim, a window that lasts arbitrarily long. I was unable to verify whether `allowInstantWithdraw` is reset per epoch in `IdleCDOEpochVariant` within the available search iterations; if it is cleared and only re-armed after each funding pull, the attack still works because step 4 executes while the flag remains true from step 2.

### Recommendation
Track funded vs unfunded instant receipts per user/epoch instead of paying the aggregate. Concretely: in `claimInstantWithdrawRequest`, cap the payable amount to receipts whose epoch is strictly older than the last funded epoch (i.e. exclude `instantWithdrawsRequestsByEpoch[_user][epochNumber]` and any epoch created after `pendingInstantWithdraws` was last collected), or maintain a per-user `fundedInstantWithdraws` counter incremented only when `collectInstantWithdrawFunds` covers that epoch's `instantWithdrawClaimsByEpoch`. At minimum, gate claims so that a user cannot claim a receipt created in an epoch for which funding has not yet been pulled.

### Proof of Concept
Foundry fork test sketch (extend `test/foundry/IdleCreditVault.t.sol` helpers):

```solidity
function testPocInstantWithdrawStealsFundedClaim() external {
    _setFeeParams(TL_MULTISIG, 10000, FULL_ALLOC, cdoEpoch.managementFee());
    address alice = makeAddr("alice");   // victim
    address mallory = makeAddr("mallory"); // attacker
    uint256 amt = 10_000 * ONE_SCALE;

    _depositWithUser(alice, amt, true);
    _depositWithUser(mallory, amt, true);

    // Epoch 0, then stop with lower APR so instant withdrawals are enabled
    _startEpochAndCheckPrices(0);
    _stopEpochAndCheckPrices(0, initialProvidedApr / 2, _expectedFundsEndEpoch());

    // Alice requests instant withdraw (full balance)
    vm.prank(alice);
    cdoEpoch.requestWithdraw(0, address(AAtranche));

    // Epoch 1 starts; after instantWithdrawDelay the manager funds pending instant withdraws
    _startEpochAndCheckPrices(1);
    vm.warp(block.timestamp + cdoEpoch.instantWithdrawDelay() + 1);
    _getInstantFunds(); // vault now holds Alice's funded 10k, pendingInstantWithdraws == 0

    // Mallory requests instant withdraw AFTER funding -> unfunded receipt
    vm.prank(mallory);
    cdoEpoch.requestWithdraw(0, address(AAtranche));

    // Mallory claims immediately and receives Alice's funded underlying
    uint256 balPre = IERC20Detailed(defaultUnderlying).balanceOf(mallory);
    vm.prank(mallory);
    cdoEpoch.claimInstantWithdrawRequest();
    uint256 stolen = IERC20Detailed(defaultUnderlying).balanceOf(mallory) - balPre;
    assertGt(stolen, 0, "attacker paid from victim's funded reserve");

    // Victim's claim reverts: vault balance drained
    vm.prank(alice);
    vm.expectRevert(); // ERC20 transfer amount exceeds balance
    cdoEpoch.claimWithdrawRequest();
    cdoEpoch.claimInstantWithdrawRequest();
}
```

Expected: `stolen` equals Mallory's requested amount while Alice's funded claim can no longer be paid until the borrower funds Mallory's new `pendingInstantWithdraws`; a default in between crystallizes Alice's loss at `defaultRecoveryPrice` while Mallory keeps full proceeds.