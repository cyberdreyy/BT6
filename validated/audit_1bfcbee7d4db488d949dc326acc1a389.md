### Title
Instant-withdraw claims pay the unfunded aggregate receipt, letting a user drain other claimants' funded reserves - ([File: contracts/strategies/idle/IdleCreditVault.sol])

### Summary
`claimInstantWithdrawRequest` pays out the *entire* per-user aggregate `instantWithdrawsRequests[_user]` at par and burns the same amount of receipt tokens, with no check that the newly requested portion has actually been funded via `collectInstantWithdrawFunds`. The strategy tracks the unfunded remainder in `pendingInstantWithdraws` but never subtracts it at claim time. A user who holds an already-funded instant receipt can add a fresh instant request during a running epoch and immediately claim the combined amount, spending underlying that is reserved for other users' funded claims — the same class of bug as the macroquad advisory: shared mutable state (the aggregate receipt counter) lets a "freed"/unfunded balance be used as if it were funded.

### Finding Description
In `requestInstantWithdraw` the user's balance is accumulated into a single aggregate and the unfunded remainder into `pendingInstantWithdraws` (`IdleCreditVault.sol:356-375`). The claim path ignores `pendingInstantWithdraws` entirely:

```solidity
uint256 amount = instantWithdrawsRequests[_user];
_burn(_user, amount);
instantWithdrawsRequests[_user] = 0;
_transferFundedClaim(_user, amount);
```

(`IdleCreditVault.sol:380-393`). Funds for instant requests only arrive later, when the manager calls `getInstantWithdrawFunds` → `collectInstantWithdrawFunds` (`IdleCreditVault.sol:398-403`). Between `requestInstantWithdraw` and that collection, the new request is unfunded yet fully payable at par.

Attack sequence (instant-withdraw mode, epoch running):
1. Attacker deposits (KYC'd lender) in the buffer phase, epoch starts.
2. Attacker calls `requestInstantWithdraw(amount_A)`; manager's `getInstantWithdrawFunds` funds it — the vault now holds `amount_A` underlying earmarked for the attacker.
3. Next epoch: attacker calls `requestInstantWithdraw(amount_B)` again (allowed while a previous receipt is unclaimed — `instantWithdrawsRequests[_user] += amount` accumulates, `pendingInstantWithdraws` grows, and the vault has not yet collected `amount_B` from the CDO/borrower).
4. Attacker calls `claimInstantWithdrawRequest` via the CDO (`IdleCDOEpochVariant.sol:975-978`, gated only by `allowInstantWithdraw`). The vault burns `amount_A + amount_B` receipts and transfers `amount_A + amount_B` underlying, even though only `amount_A` (plus other users' funded claims) is present.
5. `amount_B` is paid out of underlying belonging to *other* claimants whose funded receipts sit in the vault. When those users claim, the vault is short → revert / insolvency. `pendingInstantWithdraws` still reads `amount_B`, so the borrower's funding obligation is unchanged while the cash is already gone.

The per-epoch maps (`instantWithdrawsRequestsByEpoch`, `instantWithdrawClaimsByEpoch`) exist only for default accounting; the non-default claim path has no funded-vs-unfunded split, and `_transferFundedClaim`'s reserve guard only applies after `defaultRecoveryReserve != 0`, so nothing stops the overpayment.

### Impact Explanation
Direct theft of other withdrawers' funded claims: the attacker withdraws `amount_B` more than was funded. Victims' subsequent `claimInstantWithdrawRequest`/`claimWithdrawRequest` calls revert on insufficient vault balance (permanent freezing of their payouts until the shortfall is somehow covered). Loss is bounded by the attacker's second request size times replayability across epochs.

### Likelihood Explanation
Requires `allowInstantWithdraw` enabled and the manager to fund instant requests while the attacker re-requests in a later epoch before claiming — all achievable by an unprivileged KYC'd lender with no privileged collusion. The trigger is a normal user-flow sequence, not an edge condition.

### Recommendation
Track the funded portion of each user's instant receipts (or settle per request epoch like normal receipts). At minimum, in `claimInstantWithdrawRequest`, cap the payable amount to the funded balance: e.g. pay `min(instantWithdrawsRequests[_user], fundedInstantBalance)` or subtract the user's share of `pendingInstantWithdraws`, and only burn receipts for the funded portion.

### Proof of Concept
```solidity
// Foundry fork PoC (pseudo-test over existing harness in test/foundry/IdleCreditVault.t.sol)
function testInstantWithdrawUnfundedAggregateTheft() external {
    // allowInstantWithdraw = true; instantDelay elapsed pattern as in testClaimWithdrawRequestWithInstantDefault
    uint256 a = 100e6; uint256 b = 50e6;
    address attacker = makeAddr('attacker');
    _depositWithUser(attacker, a + b, true);

    // epoch 0: request a, get it funded
    vm.prank(attacker); cdoEpoch.requestInstantWithdraw? // via CDO request path for instant
    vm.prank(manager); cdoEpoch.getInstantWithdrawFunds(); // collects 'a' into vault
    // do NOT claim yet

    // epoch 1: request b while epoch running; pendingInstantWithdraws += b, no funds collected
    vm.prank(attacker); cdoEpoch.requestInstantWithdraw(/*b*/);

    // claim immediately: vault pays a + b though only a + others' funded cash exists
    uint256 pre = underlying.balanceOf(attacker);
    vm.prank(attacker); cdoEpoch.claimInstantWithdrawRequest();
    assertEq(underlying.balanceOf(attacker) - pre, a + b); // overpaid by b
    // victim's funded claim now reverts / vault insolvent by b
}
```