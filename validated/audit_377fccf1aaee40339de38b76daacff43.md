### Title
Instant-withdraw receipts pay out unfunded requests, letting a user drain other users' funded instant-withdraw claims - ([File: contracts/strategies/idle/IdleCreditVault.sol])

### Summary
`requestInstantWithdraw` accumulates every new request into a single per-user aggregate `instantWithdrawsRequests[_user]` and into the global `pendingInstantWithdraws`, but `claimInstantWithdrawRequest` pays out the entire aggregate while only decrementing `instantWithdrawsRequests[_user]` — it never checks that the requested amount was actually collected from the borrower via `collectInstantWithdrawFunds`. A user can therefore claim underlying for instant requests that were never funded, paid out of the pooled vault balance that backs other users' funded receipts.

### Finding Description
In `IdleCreditVault.sol:356-375`, `requestInstantWithdraw` burns the CDO's strategy tokens, mints a receipt to the user, and increases `instantWithdrawsRequests[_user]` and `pendingInstantWithdraws`. Separately, `collectInstantWithdrawFunds` (`IdleCreditVault.sol:398-403`) is what actually moves underlying from the CDO into the vault and decrements `pendingInstantWithdraws`.

`claimInstantWithdrawRequest` (`IdleCreditVault.sol:380-393`) then does:

```solidity
uint256 amount = instantWithdrawsRequests[_user];
_burn(_user, amount);
instantWithdrawsRequests[_user] = 0;
_transferFundedClaim(_user, amount);
```

There is no check that `amount` is covered by previously collected funds. The vault's underlying balance is a shared pool: it holds funded receipts belonging to all users whose requests were collected by `getInstantWithdrawFunds` but not yet claimed. Because `pendingInstantWithdraws` is untouched on the claim path, nothing distinguishes "funded" from "unfunded" portions of `instantWithdrawsRequests[_user]`.

Attack sequence (running epoch, `allowInstantWithdraw` enabled):
1. Victim requests an instant withdraw of amount `V`; after `instantWithdrawDelay`, `getInstantWithdrawFunds` pulls `V` from the borrower into the vault. Victim does not claim immediately.
2. Attacker (any KYC-passing lender holding tranche tokens) calls `cdoEpoch.requestWithdraw(...)` in instant mode for amount `A`. `instantWithdrawsRequests[attacker] = A` and `pendingInstantWithdraws += A`, but no funds have been collected yet.
3. Attacker immediately calls `cdoEpoch.claimInstantWithdrawRequest()`. The vault burns `A` receipt tokens and transfers `A` underlying — taken from the vault balance that includes the victim's funded `V` (and any other funded unclaimed receipts).
4. The victim's later claim reverts on insufficient balance: their funded receipt is permanently unpaid, having been consumed by the attacker's unfunded claim.

The normal-withdraw path does not have this bug: `_claimFundedWithdrawRequest` is gated by epoch progression (`epochNumber <= lastWithdrawRequest` reverts) and `pendingWithdraws` is reconciled at `stopEpoch`/`collectWithdrawFunds`, including a loss-adjusted path via `lossRecoveryPriceByEpoch`. The instant path has no equivalent funded/unfunded bookkeeping on claim.

### Impact Explanation
Direct theft with quantified loss: the attacker receives `A` underlying that was never funded by the borrower, paid from underlying reserved for other users' instant-withdraw receipts. Loss equals the minimum of the attacker's tranche position value and the vault's pooled unclaimed funded balance. The victim's receipt becomes permanently unclaimable (transfer of insufficient balance reverts), so this is both theft and permanent freezing of unclaimed funds.

### Likelihood Explanation
Requires `allowInstantWithdraw` and an epoch phase where requests are accepted while the vault holds unclaimed funded receipts — a normal, recurring state since funded users can claim at any time and any delay between funding and claim opens the window. The attacker only needs a tranche position and a wallet passing `isWalletAllowed`; no privileged role, timing luck beyond the existence of unclaimed funded receipts, or external dependency is needed. Cost is one epoch of interest on the position used.

### Recommendation
Track funded vs. unfunded instant receipts separately. Either (a) decrement `pendingInstantWithdraws` in `claimInstantWithdrawRequest` and maintain a per-user `fundedInstantWithdraws` counter increased only by `collectInstantWithdrawFunds` pro rata or by epoch, allowing claims only up to the funded amount; or (b) cap each claim at `instantWithdrawClaimsByEpoch`-funded amounts recorded per epoch, mirroring the `lossRecoveryPriceByEpoch` / `withdrawsRequestsByEpoch` accounting used for normal withdraws. Rejecting claims while `pendingInstantWithdraws` attributable to the user is non-zero also works but degrades UX.

### Proof of Concept
Foundry fork PoC outline (extend `test/foundry/IdleCreditVault.t.sol` harness):

```solidity
function testInstantClaimStealsFundedReceipts() external {
    uint256 amount = 10_000 * ONE_SCALE;
    address victim = makeAddr('victim');
    address attacker = makeAddr('attacker');
    _depositWithUser(victim, amount, true);
    _depositWithUser(attacker, amount, true);

    _startEpochAndCheckPrices(0);
    _stopEpochAndCheckPrices(0, initialProvidedApr / 2, _expectedFundsEndEpoch());

    // victim requests instant withdraw
    vm.prank(victim);
    cdoEpoch.requestWithdraw(0, address(AAtranche));

    _startEpochAndCheckPrices(1);
    vm.warp(block.timestamp + cdoEpoch.instantWithdrawDelay() + 1);
    _getInstantFunds(); // borrower funds victim's request into the vault

    // attacker requests instant withdraw (NOT yet funded) and claims immediately
    uint256 attBalPre = underlying.balanceOf(attacker);
    vm.startPrank(attacker);
    cdoEpoch.requestWithdraw(type(uint256).max / 4 > 0 ? 0 : 0, address(AAtranche)); // full balance
    cdoEpoch.claimInstantWithdrawRequest();
    vm.stopPrank();

    assertGt(underlying.balanceOf(attacker), attBalPre, 'attacker paid for unfunded request');

    // victim's funded receipt is now unclaimable: vault balance was drained
    vm.prank(victim);
    vm.expectRevert(); // safeTransfer fails on insufficient vault balance
    cdoEpoch.claimInstantWithdrawRequest();
}
```

Note: I could not fully verify the exact epoch-phase gating around `getInstantWithdrawFunds` vs. `claimInstantWithdrawRequest` (whether a claim is permitted in the same phase where a new request was just made) within the available search depth. If the CDO gates claims to a phase disjoint from new requests, the same flaw still applies to back-to-back requests where a second unfunded request is added before claiming a first funded one, since both share the single `instantWithdrawsRequests` aggregate.