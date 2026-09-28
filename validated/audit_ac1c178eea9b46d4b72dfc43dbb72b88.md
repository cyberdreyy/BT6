### Title
Instant-withdraw receipts pay full face value regardless of funded share, letting the first claimant drain underlyings earmarked for other pending/funded claims - ([File: contracts/strategies/idle/IdleCreditVault.sol](contracts/strategies/idle/IdleCreditVault.sol))

### Summary

The kernel bug is an *uninitialized buffer*: a freshly allocated region is consumed as if it were fully populated, so whatever (stale) bytes happen to be there get returned to the caller. The credit-vault analog is a *partially-funded claim bucket consumed as if fully funded*: `requestInstantWithdraw` mints each user a full-face receipt and records the request globally, but funding arrives separately and only in aggregate through `collectInstantWithdrawFunds`. `claimInstantWithdrawRequest` then pays each user the **entire** `instantWithdrawsRequests[_user]` receipt — the "uninitialized" portion of the bucket is treated as funded — and pulls from the strategy's shared underlying balance, which also backs normal funded withdraw claims.

### Finding Description

Relevant flow in `contracts/strategies/idle/IdleCreditVault.sol`:

```solidity
// line 356: receipt minted at full face value
instantWithdrawsRequests[_user] += _amount;
instantWithdrawsRequestsByEpoch[_user][currentEpoch] += _amount;
instantWithdrawClaimsByEpoch[currentEpoch] += _amount;
pendingInstantWithdraws += _amount;
```

```solidity
// line 398: funding is aggregate only; per-user amounts untouched
function collectInstantWithdrawFunds(uint256 _amount) external {
    _onlyIdleCDO();
    if (_amount == 0) return;
    pendingInstantWithdraws -= _amount;   // global unfunded remainder only
    underlyingToken.safeTransferFrom(idleCDO, address(this), _amount);
}
```

```solidity
// line 380: claim pays the FULL receipt, no per-user/epoch funded-share check
function claimInstantWithdrawRequest(address _user) external {
    _onlyIdleCDO();
    if (defaultRecoveryFinalized && defaultInstantWithdrawsFinalized) {
        _claimDefaultedInstantWithdrawRequest(_user);
    }
    uint256 amount = instantWithdrawsRequests[_user];
    _burn(_user, amount);
    instantWithdrawsRequests[_user] = 0;
    _transferFundedClaim(_user, amount);   // pays from shared balance
}
```

The code itself acknowledges that partial pre-funding is a real state: `_defaultPrefundedInstantReserve` (line 716) computes `instantWithdrawClaimsByEpoch[epochNumber] - pendingInstantWithdraws` as "already-held" reserve, which is only non-zero when instant requests were funded for less than their aggregate basis. Yet in the non-defaulted path nothing reconciles `instantWithdrawsRequests[_user]` against the funded share — the claim pays face value from `underlyingToken.balanceOf(address(this))`, the same balance that backs `_claimFundedWithdrawRequest` payouts for normal withdraw receipts and settled APR0 claims.

`_transferFundedClaim` (line 897) only protects `defaultRecoveryReserve`; before default finalization that reserve is zero, so there is no guard at all separating instant-claim payouts from funds earmarked for normal funded receipts.

### Impact Explanation

Whenever the CDO collects less underlying than the aggregate instant basis (partial pre-funding — e.g., limited liquidity moved at `startEpoch`/`getInstantWithdrawFunds`, leaving `pendingInstantWithdraws > 0`), the claim queue becomes first-come-first-served at *par* instead of pro-rata:

- An attacker who claims first receives 100% of their receipt, consuming underlying that should have been shared across all instant claimants.
- If the funded amount is exhausted, later identical receipts revert — temporary freezing of other users' withdrawals.
- Worse, the payout draws from the strategy's whole balance, so an attacker's instant claim can consume underlyings collected via `collectWithdrawFunds` that are earmarked for other users' *already-funded* normal withdraw receipts (`withdrawsRequests`) or settled APR0 claims — direct theft of unclaimed yield, quantified as `min(attacker receipt, funded balance attributable to others)`.

This breaks the "one receipt one pro-rata payout" and solvency invariants: total obligations (`instantWithdrawsRequests` + `withdrawsRequests` + settled APR0) exceed funded balance whenever instant funding was partial.

### Likelihood Explanation

- Attacker is unprivileged: any KYC-passing tranche holder can request instant withdrawals and call `cdoEpoch.claimInstantWithdrawRequest()`.
- Requires a partially-funded instant bucket (`pendingInstantWithdraws != 0` with strategy holding less than `instantWithdrawClaimsByEpoch`), which the contract explicitly supports (instant-claim delay, liquidity-dependent collection at `startEpoch`, and the prefunded-reserve math in `_defaultPrefundedInstantReserve`).
- All privileged roles remain honest; the theft is pure claim-ordering against a shared balance.
- Caveat: I could not fully verify whether `IdleCDOEpochVariant` gates `claimInstantWithdrawRequest` until the instant bucket is fully collected; the strategy code permits the unsafe state described, and `collectInstantWithdrawFunds` accepts arbitrary partial amounts, but if the CDO always collects the full `pendingInstantWithdraws` before enabling claims, the window narrows to the default-adjacent partial-funding case.

### Recommendation

Track the funded portion of instant claims per epoch (e.g., `instantFundedByEpoch` / a recovery price for instant receipts), and cap `claimInstantWithdrawRequest` at `instantWithdrawsRequests[_user] * fundedShare`, or revert claims while `pendingInstantWithdraws != 0` unless default recovery has been finalized. Additionally, segregate balances earmarked for normal funded receipts so instant claims can only spend the instant-funded bucket.

### Proof of Concept

Foundry fork PoC sketch (modeled on `test/foundry/IdleCreditVault.t.sol` / `IdleCDOEpochQueue.t.sol` instant-withdraw setup):

```solidity
function testInstantClaimDrainsSharedBalance() external {
    // APR epoch running, instant withdrawals enabled (setInstantWithdrawParams)
    _stopCurrentEpochWithApr(10e18);
    vm.prank(manager);
    cdoEpoch.setInstantWithdrawParams(100, 1e18, false);

    // attacker and victim each deposit 100 USDC in AA and request instant withdraw
    uint256 amtA = 100e6; uint256 amtV = 100e6;
    uint256 tA = _depositWithUser(attacker, amtA);
    uint256 tV = _depositWithUser(victim,  amtV);
    vm.prank(manager);
    cdoEpoch.startEpoch();
    _requestInstantWithdrawWithUser(attacker, tA);
    _requestInstantWithdrawWithUser(victim,  tV);

    // stopEpoch triggers instant path; CDO collects only HALF the instant basis
    _stopCurrentEpochWithApr(1e18);
    vm.prank(manager);
    cdoEpoch.startEpoch();
    skip(101); // pass instantDelay

    uint256 basis = strategy.instantWithdrawClaimsByEpoch(strategy.epochNumber());
    // CDO moves only basis/2 to the strategy (partial funding; pendingInstantWithdraws > 0)
    vm.prank(address(cdoEpoch));
    // emulate partial collection:
    // strategy.collectInstantWithdrawFunds(basis / 2);

    uint256 victimPre = underlying.balanceOf(victim);
    uint256 attackerPre = underlying.balanceOf(attacker);

    // attacker claims first: paid FULL receipt although only half the bucket is funded
    vm.prank(attacker);
    cdoEpoch.claimInstantWithdrawRequest();
    assertEq(underlying.balanceOf(attacker) - attackerPre, tA /* full amount */);

    // victim's identical receipt now reverts/underpays: funds were drained
    vm.prank(victim);
    vm.expectRevert(); // insufficient balance in strategy
    cdoEpoch.claimInstantWithdrawRequest();
}
```

Expected result: the attacker's claim succeeds at face value while the aggregate instant bucket was only partially funded, and the victim's claim (and/or other users' funded normal receipts held in the same balance) is left unpaid — demonstrating theft/ordering loss consistent with the "uninitialized/stale data consumed as valid" bug class.