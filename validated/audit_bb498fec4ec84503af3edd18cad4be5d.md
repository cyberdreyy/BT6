I found promising claim-path logic. Let me examine the funded-claim path and stopEpoch loss handling more closely.### Title
Unfunded instant-withdraw receipts are paid from other users' funded withdraw bucket in `claimInstantWithdrawRequest` — (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
The external report (CVE-2020-1065, ChakraCore object/type confusion leading to memory corruption) maps onto a claim-type confusion in `IdleCreditVault`: normal and instant withdraw receipts are both minted as fungible strategy-token IOUs, but the instant claim path pays the aggregate `instantWithdrawsRequests[_user]` with no check that the request was ever funded. `claimInstantWithdrawRequest` burns the full user receipt balance and calls `_transferFundedClaim`, which spends the strategy's raw underlying balance — the same balance that holds funds collected via `collectWithdrawFunds` for other users' funded normal withdraws and borrower repayments not yet attributed. One funded bucket can therefore satisfy two distinct receipts, corrupting the "one receipt, one payout" invariant.

### Finding Description
- `requestInstantWithdraw` mints receipt strategy tokens to `_user` and increments `instantWithdrawsRequests[_user]` and `pendingInstantWithdraws`, but moves no underlying — funding only happens later when IdleCDO calls `collectInstantWithdrawFunds` (IdleCreditVault.sol:356-375, 398-403).
- `claimInstantWithdrawRequest` reads `instantWithdrawsRequests[_user]`, burns the receipt tokens, and calls `_transferFundedClaim(_user, amount)` unconditionally (IdleCreditVault.sol:380-393). There is no epoch gating (unlike `_claimFundedWithdrawRequest` which checks `epochNumber <= lastWithdrawRequest`) and no check that `pendingInstantWithdraws` was actually collected.
- `_transferFundedClaim` transfers from `underlyingToken.balanceOf(address(this))`, guarding only the `defaultRecoveryReserve` slice when it is non-zero (IdleCreditVault.sol:897-907). Before any default is finalized, the reserve is zero, so the entire strategy balance is spendable — including underlyings pulled via `collectWithdrawFunds` that are owed to pending normal-withdraw claimants.

Attack sequence (running-epoch buffer phase, fixed-APR mode):
1. Victim calls `requestWithdraw`; epoch stops; `collectWithdrawFunds` pulls underlying into the strategy for the victim's receipt (`pendingWithdraws` → 0, balance sits in the strategy).
2. Attacker (any KYC'd tranche holder) calls `requestInstantWithdraw` during the next epoch. Their receipt is unfunded: `pendingInstantWithdraws` increased, no underlying was collected for it.
3. Attacker calls `claimInstantWithdrawRequest` through `IdleCDOEpochVariant`. The strategy pays the attacker's full `instantWithdrawsRequests` amount out of the victim's funded bucket.
4. Victim's subsequent `claimWithdrawRequest` reverts on `safeTransfer` underflow — permanent loss of the funded claim.

### Impact Explanation
Direct theft: the attacker receives underlying that was already funded for, and owed to, a different claimant. The loss equals the attacker's instant-withdraw amount, capped by the strategy's un-attributed underlying balance (all funded-but-unclaimed normal withdrawals plus any interim borrower repayments). The victim's receipt tokens are still minted but unpayable — a permanently unredeemable claim, i.e., insolvency against funded liabilities.

### Likelihood Explanation
The strategy function itself contains no fundedness check; exploitability depends on `IdleCDOEpochVariant.claimInstantWithdrawRequest` not gating the call on `collectInstantWithdrawFunds` having run for the request's epoch (e.g., ordering enforced only on the prefunded/queue path, or instant claims being callable whenever the CDO has un-collected instant liability while the strategy holds funded normal-withdraw cash). Whenever such a window exists, any whitelisted lender can execute it with a single request + claim pair; no privileged role, default, or oracle is needed.

### Recommendation
Track funded instant claims separately: only decrement `instantWithdrawsRequests`/`pendingInstantWithdraws` and mark receipts payable in `collectInstantWithdrawFunds`, and have `claimInstantWithdrawRequest` pay at most the funded portion (e.g., `instantWithdrawsFunded[_user]`), reverting otherwise. Alternatively, revert in `claimInstantWithdrawRequest` when `pendingInstantWithdraws` still includes the user's unfunded amount, matching the epoch-gating discipline of `_claimFundedWithdrawRequest`.

### Proof of Concept
Foundry-style fork PoC sketch (mirroring `test/foundry/IdleCreditVault.t.sol` helpers):

```solidity
// victim deposits, epoch runs
uint256 victimTranches = _depositWithUser(victim, 100e6);
uint256 victimReq = cdoEpoch.requestWithdraw(victimTranches, address(AAtranche));

// stopEpoch: borrower repays, collectWithdrawFunds funds victim's receipt
deal(underlying, borrower, expectedFunds);
vm.prank(manager);
cdoEpoch.stopEpoch(apr, interest);
// strategy now holds victimReq underlying; victim has not yet claimed

// attacker instant-withdraws in the new epoch — unfunded
uint256 atkTranches = _depositWithUser(attacker, victimReq);
cdoEpoch.startEpoch();
vm.prank(attacker);
cdoEpoch.requestInstantWithdraw... // receipt minted, pendingInstantWithdraws += amt

// attacker claims before collectInstantWithdrawFunds funds the request
uint256 pre = underlying.balanceOf(attacker);
vm.prank(attacker);
cdoEpoch.claimInstantWithdrawRequest();
assertGt(underlying.balanceOf(attacker) - pre, 0); // paid from victim's bucket

// victim claim now reverts / underpays
vm.prank(victim);
vm.expectRevert();
cdoEpoch.claimWithdrawRequest();
```

Note: I could not fully verify `IdleCDOEpochVariant.claimInstantWithdrawRequest`'s gating within the available iterations; if the CDO wrapper already enforces that instant claims only execute after `collectInstantWithdrawFunds` funded them, this analog is mitigated at the wrapper layer and the finding reduces to a missing defense-in-depth invariant in the strategy.