### Title
Instant-withdraw claims pay unfunded receipts — a request can be sandwiched between an earlier funded request and its claim to steal the funded underlying - ([File: contracts/strategies/idle/IdleCreditVault.sol])

### Summary
`IdleCreditVault.claimInstantWithdrawRequest` pays out the user's entire `instantWithdrawsRequests` balance and burns the corresponding receipt tokens, but it never checks whether the underlying for those requests has actually been collected from the borrower. Funding is decoupled: `requestInstantWithdraw` only records the request and bumps the global `pendingInstantWithdraws`, while `collectInstantWithdrawFunds` (called by the CDO when the borrower sends funds) is the step that actually transfers underlying into the strategy. Because claims are tracked per-user as a single aggregate counter with no funded/unfunded split and no epoch/funding gate, any receipt holder can claim at par against whatever underlying happens to sit in the strategy — including funds collected for *other* users' still-unclaimed instant withdrawals. This is the direct on-chain analog of the report's TOCTOU: a check-less "insert" (claim) executing on state that was updated for a different logical request.

### Finding Description
The flow in `IdleCreditVault`:

- `requestInstantWithdraw` burns the CDO's strategy tokens, mints the user a receipt (`_mint(_user, _amount)`), and only *records* `instantWithdrawsRequests[_user] += _amount` and `pendingInstantWithdraws += _amount` (lines 356–375). No underlying moves.
- `collectInstantWithdrawFunds` later decrements `pendingInstantWithdraws` and pulls underlying from the CDO into the strategy (lines 398–403). It is not tied to any user or epoch.
- `claimInstantWithdrawRequest` (lines 380–393) reads `amount = instantWithdrawsRequests[_user]`, burns it, resets the counter to 0, and calls `_transferFundedClaim(_user, amount)` — with no check that `collectInstantWithdrawFunds` ever ran for this user's request. The CDO-side wrapper `IdleCDOEpochVariant.claimInstantWithdrawRequest` only checks `allowInstantWithdraw` (IdleCDOEpochVariant.sol:975–979); there is no epoch-maturity or funded flag.

So a user holding a fresh, unfunded receipt can drain underlying that was collected to back a different user's matured request (or any funded balance sitting in the strategy, while `defaultRecoveryReserve` is the only bucket `_transferFundedClaim` appears to protect). The minted receipt tokens are held by the user, so `_burn(_user, amount)` always succeeds.

### Impact Explanation
Direct theft of other users' funded withdrawal proceeds. Attacker B requests an instant withdraw of amount X (burning X of tranche value — a real cost) but immediately claims X in underlying that was collected for user A's earlier request. A's own subsequent claim then reverts on insufficient strategy balance or is paid short — A permanently loses X, which is now capped only by A's funded amount. If B's claim size exceeds A's funded amount, the transfer reverts, bounding the theft at whatever is sitting collected-but-unclaimed; still a real, quantified loss of user funds, not just an accounting discrepancy. The net effect: B mints a receipt against principal the borrower never repaid for B's request and converts it into A's repaid principal.

### Likelihood Explanation
Requires `allowInstantWithdraw` enabled and a window where the strategy holds collected-but-unclaimed instant-withdraw funds — i.e., another user's request was funded by `collectInstantWithdrawFunds` but not yet claimed. That window exists for every matured instant withdrawal until its owner claims, and claims are user-initiated with no deadline. The attacker needs tranche tokens to burn (cost = their share's value at the current price), so profitability depends on the funded amount exceeding the attacker's receipt cost — they can simply request with exactly the tranche balance they hold and net the difference between receipt face value (paid 1:1 in underlying) and their deposited principal only if claim value exceeds deposit cost; in the honest case this is break-even, so the pure theft scenario is when the funded pool pays them for an epoch where the borrower never funded *their* request — which is precisely what the missing check allows. Unprivileged tranche holder, no privileged collusion needed.

### Recommendation
Track the funded portion of instant withdrawals separately from the requested amount — e.g., a per-epoch funded flag or `fundedInstantWithdraws` counter incremented inside `collectInstantWithdrawFunds` and consumed per-claim in claim order — and have `claimInstantWithdrawRequest` pay only the funded remainder of `instantWithdrawsRequests[_user]` (or revert/queue the unfunded portion). Alternatively, move underlying atomically inside `requestInstantWithdraw` (pull from borrower/CDO in the same call) so a receipt can never exist unfunded, mirroring the fix applied to `register.ts` (commit `08c6149`), which moved the limit check and record insertion into one atomic unit.

### Proof of Concept
Foundry fork-style sequence (standard epoch variant, `allowInstantWithdraw = true`, single-tranche AA-only or AA pool):

```solidity
// setup: user A and attacker B each hold AA tranches; epoch running.
uint256 amount = 100_000e6;

// 1) A requests instant withdraw during epoch N.
vm.prank(A);
cdoEpoch.requestInstantWithdraw(amountA, AATranche);

// 2) Epoch rolls; borrower/keeper path funds A's request.
//    CDO calls strategy.collectInstantWithdrawFunds(amountA) ->
//    strategy now holds amountA underlying, pendingInstantWithdraws -= amountA.

// 3) B requests instant withdraw of amountB <= amountA (unfunded).
vm.prank(B);
cdoEpoch.requestInstantWithdraw(amountB, AATranche);
// strategy balance unchanged; pendingInstantWithdraws += amountB.

// 4) B claims immediately. claimInstantWithdrawRequest pays
//    instantWithdrawsRequests[B] = amountB from A's funded underlying.
uint256 balPre = underlying.balanceOf(B);
vm.prank(B);
cdoEpoch.claimInstantWithdrawRequest();
assertEq(underlying.balanceOf(B) - balPre, amountB); // B paid from A's money

// 5) A's claim now fails (insufficient strategy balance) or pays short.
vm.prank(A);
vm.expectRevert(); // safeTransfer underflow / insufficient funds
cdoEpoch.claimInstantWithdrawRequest();
```

Key assertions to add: `strategy`'s underlying balance after step 2 equals `amountA`; after step 4 it equals `amountA - amountB` while `pendingInstantWithdraws` still shows B's unfunded `amountB`, proving the claim consumed another user's funded basis.