### Title
Small withdrawals round to zero: `requestWithdraw` burns tranche tokens without minting any receipt — (File: contracts/IdleCDOEpochVariant.sol)

### Summary
`IdleCDOEpochVariant.requestWithdraw` converts tranche tokens to underlyings via `_trancheToUnderlyings(_amount) = _amount * _tranchePrice / ONE_TRANCHE_TOKEN`, which rounds down. For small `_amount` (roughly a tranche position worth less than 1 wei of underlying), `_underlyings` becomes `0`. `IdleCreditVault.requestWithdraw` then early-returns on `_amount == 0` without minting a receipt or recording `lastWithdrawRequest`, but back in the CDO, `_withdrawOps` still burns the user's tranche tokens. The user permanently loses the tranche tokens and receives nothing. The same class appears in `IdleCDOEpochQueue.claimWithdrawRequest`, where `amount * _withdrawPrice / ONE_TRANCHE` rounds a small queued claim to `0` while the receipt is cleared and the funded underlyings stay stranded in the queue contract.

### Finding Description
In `requestWithdraw` (contracts/IdleCDOEpochVariant.sol, ~L739-791):

```solidity
_underlyings = _trancheToUnderlyings(_amount, _tranche);   // L756: rounds to 0 for dust
...
uint256 principal = _underlyings;
(uint256 interest, int256 diff) = _calcInterestWithdrawRequest(_underlyings, _tranche);
uint256 totalFees = _totalWithdrawFees(principal, interest);
_underlyings = principal + interest - totalFees;
...
creditVault.requestWithdraw(_underlyings, msg.sender, principal);  // L788
_withdrawOps(_amount, principal, _tranche);                        // L790: burns tranche tokens
```

`IdleCreditVault.requestWithdraw` (contracts/strategies/idle/IdleCreditVault.sol, L243-246) begins with:

```solidity
if (_amount == 0) return;
```

So when `_underlyings == 0` no strategy-token receipt is minted, `pendingWithdraws` is unchanged, and `lastWithdrawRequest[user]` is never set — yet `_withdrawOps` burns the user's `_amount` tranche tokens and the function returns `0`. There is no minimum-amount guard anywhere in this path (`_checkTranche`, `isWalletAllowed`, `_skimDonatedAssets`, `_updateAccounting` do not stop it).

The instant-withdraw branch (L761-769) has the identical issue: `requestInstantWithdraw(0, msg.sender)` burns/mints zero and records a zero request, then `_withdrawOps` still burns the tranche tokens.

In the queue flow, `claimWithdrawRequest` (contracts/IdleCDOEpochQueue.sol, L393-411) clears `userWithdrawalsEpochs[msg.sender][_epoch]` and pays `amount * _withdrawPrice / ONE_TRANCHE`, which is `0` whenever `amount * _withdrawPrice < 1e18`. The tranche tokens were already burned by `processWithdrawRequests`, and the share of underlyings the queue actually received for that user (`epochPendingClaims` was funded pro-rata) remains locked in the queue contract forever.

### Impact Explanation
Permanent loss of user funds: a KYC-passing lender who requests withdrawal of a small tranche balance has the tranche tokens burned and gets a zero-valued receipt (or no receipt at all). In the queue variant, the underlying funding corresponding to the zero-rounded claim has already been paid to the queue and becomes permanently stranded there. Loss per request is capped at just under 1 wei of underlying per rounding-to-zero, but the burn applies to the full requested tranche amount whenever `_amount * price < 1e18`, and there is no recovery path since `userWithdrawalsEpochs`/receipt state is cleared.

### Likelihood Explanation
Requires no attacker action beyond a user holding a dust tranche position and requesting withdrawal — a normal, permitted action. Any user can end up with dust balances via partial withdrawals or transfers. No privileged role, default, or specific epoch phase is needed; the rounding happens unconditionally in the standard buffer/epoch flow. The queue variant additionally triggers whenever `_withdrawPrice` is depressed (e.g., after `processWithdrawalClaims` rebases the price downward per L355-357), widening the range of amounts that round to zero.

### Recommendation
- In `IdleCDOEpochVariant.requestWithdraw`, revert (or no-op without burning) when `_trancheToUnderlyings(_amount, _tranche)` computes `0`, e.g. `_checkNotAllowed(_underlyings == 0)` before calling `_withdrawOps`; apply the same guard to the instant-withdraw branch.
- In `IdleCDOEpochQueue.claimWithdrawRequest`, either revert on a zero computed payout (so the receipt is not cleared and the claim can be retried) or carry the rounding remainder into a per-user dust balance instead of stranding it.
- Consider rounding-up semantics for user-facing claim math, consistent with `previewMint`'s round-up convention used elsewhere.

### Proof of Concept
Foundry fork PoC sketch (setup mirrors `test/foundry/IdleCreditVault.t.sol` / `IdleCDOEpochQueue.t.sol` helpers):

```solidity
function testSmallWithdrawBurnsTrancheWithZeroReceipt() external {
    // pool running a fixed-APR epoch; user is a KYC'd lender
    uint256 tranchePrice = cdoEpoch.virtualPrice(address(AAtranche));
    // choose dust tranche amount whose underlying value rounds to 0
    uint256 dust = (ONE_TRANCHE_TOKEN - 1) / tranchePrice; // dust*price/1e18 == 0

    uint256 trancheBalBefore = AAtranche.balanceOf(user);
    uint256 stratBefore = strategy.balanceOf(user);

    vm.prank(user);
    uint256 out = cdoEpoch.requestWithdraw(dust, address(AAtranche));

    assertEq(out, 0);
    assertEq(strategy.balanceOf(user), stratBefore);          // no receipt minted
    assertEq(strategy.withdrawsRequests(user), 0);
    assertEq(strategy.lastWithdrawRequest(user), 0);
    assertEq(AAtranche.balanceOf(user), trancheBalBefore - dust); // tranches burned anyway
}

function testQueueSmallClaimRoundsToZero() external {
    // user requests withdraw of `dust` tranches via queue during running epoch
    _requestWithdrawWithUser(user, dustTranches);
    _stopCurrentEpochWithApr(10e18);
    vm.prank(manager);
    queue.processWithdrawRequests();
    uint256 epoch = strategy.epochNumber();
    // next epoch; funds claimed to queue
    vm.prank(manager); cdoEpoch.startEpoch();
    _stopCurrentEpochWithApr(10e18);
    queue.processWithdrawalClaims(epoch);

    uint256 balPre = underlying.balanceOf(user);
    vm.prank(user);
    queue.claimWithdrawRequest(epoch);
    // dust * epochWithdrawPrice / 1e18 == 0 -> user gets nothing,
    // receipt cleared, queue keeps the funded remainder
    assertEq(underlying.balanceOf(user), balPre);
    assertEq(queue.userWithdrawalsEpochs(user, epoch), 0);
    assertGt(underlying.balanceOf(address(queue)), 0);
}
```

Caveat: I verified the rounding and the early-return/burn ordering from indexed code, but did not read `_withdrawOps` directly; the burn behavior is corroborated by tests asserting tranche balances decrease after `requestWithdraw` (e.g., `test/foundry/IdleCreditVault.t.sol:2885`). The per-request loss is dust-bounded (< 1 wei of underlying per rounding), so the finding is strongest as a systematic user-loss/precision issue rather than an exploitable theft.