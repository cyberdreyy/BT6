### Title
Instant-withdraw claims pay the entire aggregate receipt instead of the funded portion, letting one user drain underlying reserved for other claimants - ([File: contracts/strategies/idle/IdleCreditVault.sol])

### Summary
The kernel bug authorized a whole 16MB binner BO as the DMA target while the actual slot was only 512KB — a "container size vs slot size" mismatch that let writes spill into memory owned by other jobs. The same shape exists in `IdleCreditVault.claimInstantWithdrawRequest`: funding for instant withdrawals is tracked per-epoch and can be collected only partially (`collectInstantWithdrawFunds` decrements `pendingInstantWithdraws` by whatever amount the CDO actually transferred), but the claim pays out the *entire aggregate* `instantWithdrawsRequests[_user]` at par, ignoring both the unfunded remainder and which epoch's receipts were actually funded. The strategy's underlying balance is the shared "container"; each user's funded portion is the "slot".

### Finding Description
- `requestInstantWithdraw` mints a 1:1 receipt and accumulates `instantWithdrawsRequests[_user]`, `instantWithdrawsRequestsByEpoch[user][epoch]` and the global `pendingInstantWithdraws` (`contracts/strategies/idle/IdleCreditVault.sol:356-375`).
- Funding arrives via `collectInstantWithdrawFunds(_amount)`, which reduces `pendingInstantWithdraws` by exactly `_amount` — explicitly permitting partial funding, since `pendingInstantWithdraws` is documented as "the still-unfunded remainder" and `_defaultPrefundedInstantReserve()` computes `instantBasis - pendingInstant` as already-held cash for the current epoch (`contracts/strategies/idle/IdleCreditVault.sol:398-403, 716-723`).
- `claimInstantWithdrawRequest` then does `amount = instantWithdrawsRequests[_user]; _burn(_user, amount); _transferFundedClaim(_user, amount)` with no check that the strategy actually holds collected funds for that claim and no per-epoch scoping (`contracts/strategies/idle/IdleCreditVault.sol:380-393`). The only epoch-aware logic runs after default finalization (`_claimDefaultedInstantWithdrawRequest`); in the normal path the payout "size" is the whole aggregate receipt, not the funded slot.
- Contrast with normal withdraws: `_claimFundedWithdrawRequest` enforces `epochNumber > lastWithdrawRequest[_user]` so a request cannot be paid before its epoch funded it (`IdleCreditVault.sol:326-328`). Instant claims have no equivalent guard in the strategy.

### Impact Explanation
A tranche holder who requested an instant withdraw can claim the full receipt while only part of the epoch's instant queue was collected into the strategy. The excess is paid out of underlying held in the strategy that backs *other* users' instant receipts, funded normal-withdraw claims, or the default-recovery reserve. Concretely: user A and user B each request 100 instant withdrawals in epoch N; the epoch stop collects only 100 (partial funding, `pendingInstantWithdraws = 100`). A claims first and receives 100 — correct — but if A instead had a 100 receipt while only 50 of it was funded (mixed epochs or partial collection), A receives the full 100, leaving B's claim undercollateralized by the stolen remainder. Broken invariant: one receipt, one (funded) payout; solvency of the receipt pool. Loss is bounded by the unfunded remainder `instantWithdrawsRequests[user] - funded share`, up to the whole claim.

### Likelihood Explanation
Requires the epoch flow to leave instant requests partially funded — the code explicitly supports this state (prefunded-reserve accounting at default exists precisely because "startEpoch moved the CDO's available cash to the strategy but that cash covered only part of the instant queue"). The attack needs an unprivileged tranche holder able to call `IdleCDOEpochVariant.claimInstantWithdrawRequest` while `pendingInstantWithdraws > 0` and before default finalization. The residual uncertainty is whether the CDO-side entry point gates claims until full funding; nothing in the strategy itself enforces it, and the design treats partially-funded instant queues as a normal reachable state, so the guard — if any — relies entirely on the caller rather than on the accounting that knows the funded size.

### Recommendation
In `claimInstantWithdrawRequest`, bound the payout to the funded portion: track per-user/per-epoch funded basis (analogous to `lossRecoveryPriceByEpoch` for normal withdraws), e.g. only pay `instantWithdrawsRequestsByEpoch[_user][e]` for epochs whose claims were fully collected, or maintain a `instantFundedByEpoch`/`instantRecoveryPriceByEpoch` ratio and pay `claimBasis * price`, decrementing the reserve. Do not authorize the aggregate `instantWithdrawsRequests[_user]` as the payout size.

### Proof of Concept
Foundry fork PoC sketch (against `IdleCDOEpochVariant` + `IdleCreditVault`, mirroring `test/foundry/IdleCreditVault.t.sol` setup with `pendingUser`/`pendingAAUser`):

```solidity
// 1. Users A and B deposit and each request an instant withdraw of 100e18 underlying.
vm.prank(A); cdoEpoch.requestInstantWithdraw(100e18, address(AAtranche));
vm.prank(B); cdoEpoch.requestInstantWithdraw(100e18, address(AAtranche));

// 2. stopEpoch so that only 100e18 of the 200e18 instant queue is collected
//    (strategy/borrower liquidity limited to 100e18).
//    pendingInstantWithdraws == 100e18, strategy holds 100e18 collected.

// 3. A claims. Expected funded payout: A's share of collected funds.
//    Actual: claimInstantWithdrawRequest pays instantWithdrawsRequests[A] = 100e18 in full.
vm.prank(A); cdoEpoch.claimInstantWithdrawRequest();
assertEq(underlying.balanceOf(A), /* initial + */ 100e18);

// 4. B's claim now reverts on transfer / drains funds backing CDO NAV,
//    because the strategy no longer holds B's funded share.
vm.prank(B); cdoEpoch.claimInstantWithdrawRequest(); // insolvent / overdrawn
```

Note: I could not fully verify the CDO-side gating in `IdleCDOEpochVariant.claimInstantWithdrawRequest` within this pass — if that entry point already reverts whenever `pendingInstantWithdraws > 0`, the strategy-level mismatch is unreachable and the finding reduces to defense-in-depth. The strategy accounting itself, however, unconditionally authorizes the aggregate receipt as the payout.