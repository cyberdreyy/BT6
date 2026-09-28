### Title
Instant-withdraw claims pay out of the vault's aggregate underlying balance — including funds collected for normal `pendingWithdraws` receipts — because `claimInstantWithdrawRequest` never checks that the caller's receipt was actually funded - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`IdleCreditVault.claimInstantWithdrawRequest` burns `instantWithdrawsRequests[_user]` receipt tokens and transfers that full amount of underlying via `_transferFundedClaim`, whose only guard is the `defaultRecoveryReserve` boundary. It does not verify that `pendingInstantWithdraws` was actually collected for this receipt via `collectInstantWithdrawFunds`, nor does it protect the underlying balance that `collectWithdrawFunds` pulled in to back normal `withdrawsRequests`. The only gate is the CDO-level `allowInstantWithdraw` flag, which is set once by `getInstantWithdrawFunds` and is not tied to a per-epoch/per-user funding resolution. Like the rsync symlink race — where a path is resolved against one namespace and the syscall then executes against a swapped target — the vault resolves "this instant claim is payable" against the global flag/balance, then executes the transfer against a balance that belongs to a different claim bucket (normal withdraw receipts funded at `stopEpoch`).

### Finding Description
Flow:

- `requestInstantWithdraw` mints receipt tokens and increases `instantWithdrawsRequests[_user]` and `pendingInstantWithdraws` (lines 356–375). `instantWithdrawsRequests` is only ever decreased by the claim itself, never by funding.
- `collectInstantWithdrawFunds` decreases `pendingInstantWithdraws` and pulls tokens in (lines 398–403); `collectWithdrawFunds` pulls in funding for normal receipts and stores haircuts in `lossRecoveryPriceByEpoch` (lines 411–430). Both fundings land in the same undifferentiated `underlyingToken.balanceOf(address(this))`.
- `claimInstantWithdrawRequest` pays `instantWithdrawsRequests[_user]` at par out of that aggregate balance; `_transferFundedClaim` only reserves `defaultRecoveryReserve` (lines 897–907), leaving normal-withdraw funding unprotected.
- CDO side: `claimInstantWithdrawRequest` only checks `allowInstantWithdraw` (IdleCDOEpochVariant.sol:975–979), which stays `true` across `stopEpoch` (test at IdleCreditVault.t.sol:2716 shows `allowInstantWithdraw == true` after a defaulted epoch where instant was funded) and across `startEpoch` (test at 4784 claims "right away" in the next epoch when the vault happens to hold liquidity).

Attack trace (running/buffer boundary):
1. Epoch N ends; borrower funds normal `pendingWithdraws` via `collectWithdrawFunds` → vault now holds underlying earmarked for normal receipt claimants. Instant claims were funded earlier, so `allowInstantWithdraw == true`.
2. Attacker (KYC'd lender) calls `requestWithdraw` in the buffer with instant mode enabled → `requestInstantWithdraw` mints a receipt; `pendingInstantWithdraws += X` but no funds have been collected for it yet.
3. Attacker immediately calls `claimInstantWithdrawRequest` → `_transferFundedClaim` pays X from the balance that was collected for normal withdraw receipts.
4. `pendingInstantWithdraws` remains X, so the honest manager later pulls another X from the borrower in `getInstantWithdrawFunds` — but in the meantime the normal receipt claimants' `claimWithdrawRequest` → `_claimFundedWithdrawRequest` → `_transferFundedClaim` reverts on insufficient non-reserved balance, or pays out of the next epoch's instant funding. Value is directly transferred from normal claimants to the attacker at par.

The same hole exists during a running epoch whenever the vault holds unrelated liquidity (new buffer deposits, normal-withdraw funding) and `allowInstantWithdraw` is still set: an unfunded instant receipt pays at par against someone else's funded bucket.

### Impact Explanation
Direct theft of other users' funded withdraw proceeds. The attacker swaps a freshly minted, unfunded instant receipt for underlying that the borrower already repaid to satisfy `pendingWithdraws` (or `pendingInstantWithdraws` of other users). Loss is bounded by the funded-but-unclaimed normal/instant balance sitting in the vault, which equals the aggregate `pendingWithdraws` funded at the last `stopEpoch` — potentially the entire pending withdrawal book. Victim claims then revert or are paid from subsequently collected funds, i.e., insolvency of the funded-claims bucket and a broken "one funded receipt, one payout" invariant.

### Likelihood Explanation
Requires `allowInstantWithdraw == true` (common: set after any successful `getInstantWithdrawFunds` and persisting across `stopEpoch`), an instant-mode request window (buffer period), and funded-but-unclaimed balances in the vault — all routine states. The attacker only needs a normal tranche position and KYC, both in-scope. No privileged or malicious role is involved; the honest manager's own funding calls create the vulnerable balance.

### Recommendation
Make instant claims epoch- and funding-scoped rather than balance-scoped:
- In `claimInstantWithdrawRequest`, only pay receipts whose epoch was funded — e.g., track `instantWithdrawsFundedByEpoch`/`instantWithdrawClaimsByEpoch` and require `instantWithdrawsRequestsByEpoch[_user][epoch]` to be backed by collected funds, decrementing the funded counter per claim.
- Alternatively have `collectInstantWithdrawFunds` set a per-epoch funded amount and have `claimInstantWithdrawRequest` pay `min(request, fundedShare)` while keeping `pendingInstantWithdraws` as the unfunded remainder that must be zero for a receipt to be claimable at par.
- Reset `allowInstantWithdraw` at `startEpoch`/`stopEpoch` unless all current `pendingInstantWithdraws` are funded, so an old flag cannot authorize claims against a new, unfunded receipt batch.

### Proof of Concept
Foundry fork PoC sketch (based on `test/foundry/IdleCreditVault.t.sol` helpers):

```solidity
function testInstantClaimDrainsNormalWithdrawFunding() external {
    uint256 amount = 10_000 * ONE_SCALE;
    // victim deposits and later requests a NORMAL withdraw; attacker deposits too
    _depositWithUser(victim, amount, true);          // AA
    _depositWithUser(attacker, amount, true);        // AA

    // epoch 0 runs and stops; borrower repays interest
    _startEpochAndCheckPrices(0);
    _stopEpochAndCheckPrices(0, initialProvidedApr, _expectedFundsEndEpoch());

    // victim requests a normal (non-instant) withdraw in the buffer of epoch 1
    vm.prank(victim);
    uint256 victimRequest = cdoEpoch.requestWithdraw(victimBal, address(AAtranche));

    _startEpochAndCheckPrices(1);
    // during running epoch 1, instant requests are funded once -> allowInstantWithdraw = true
    vm.warp(block.timestamp + cdoEpoch.instantWithdrawDelay() + 1);
    _getInstantFunds();

    // epoch 1 stops; borrower funds pendingWithdraws -> vault balance now earmarked for victim
    deal(defaultUnderlying, borrower, pendingWithdraws + interest);
    vm.prank(manager);
    cdoEpoch.stopEpoch(0, interest);   // collectWithdrawFunds pulls victim's funding into vault

    // attacker requests an INSTANT withdraw in the buffer (unfunded: pendingInstantWithdraws += X)
    vm.prank(attacker);
    cdoEpoch.requestWithdraw(attackerBal, address(AAtranche));

    // attacker claims immediately: allowInstantWithdraw still true from epoch 1,
    // _transferFundedClaim pays X out of the balance funded for the victim's normal receipt
    uint256 pre = underlying.balanceOf(attacker);
    vm.prank(attacker);
    cdoEpoch.claimInstantWithdrawRequest();
    assertGt(underlying.balanceOf(attacker) - pre, 0);

    // victim's funded normal claim now reverts / is underpaid: vault balance < reserve+claim
    vm.prank(victim);
    vm.expectRevert(); // NotAllowed / ERC20 insufficient balance
    cdoEpoch.claimWithdrawRequest();
}
```

Key assertions: `IdleCreditVault(strategy).pendingInstantWithdraws()` remains > 0 after the attacker is paid (the receipt was never funded), and the shortfall equals exactly the victim's `withdrawsRequests` funding consumed by `_transferFundedClaim`.