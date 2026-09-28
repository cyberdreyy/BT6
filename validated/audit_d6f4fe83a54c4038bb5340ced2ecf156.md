One more targeted check on how instant-withdraw funding interacts with epoch transitions.### Title
Instant-withdraw receipts stranded in a prior epoch escape the default haircut and drain the recovery reserve - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`IdleCreditVault` indexes instant-withdraw claims by the epoch in which they were requested (`instantWithdrawClaimsByEpoch[epochNumber]`, `instantWithdrawsRequestsByEpoch[user][epoch]`), but tracks funding with a single global counter (`pendingInstantWithdraws`). When an instant request made in epoch E is only partially funded and the pool later defaults in epoch E+1, `defaultPendingClaimBasis()` looks up `instantWithdrawClaimsByEpoch` at the *new* `epochNumber`, sees zero, and excludes the stale-epoch receipts from the recovery basis. At claim time, those receipts fall through `_claimDefaultedInstantWithdrawRequest` (keyed on `defaultRecoveryEpoch`) untouched and are then paid **at par** by `_transferFundedClaim`, draining balance that was sized for haircutted claims and eventually bricking `_transferDefaultRecovery` (`defaultRecoveryReserve -= _amount` underflows) for legitimate claimants. This is the on-chain analog of the CVE's use-of-freed-lookup bug: per-epoch accounting referenced after the epoch it belonged to has been recycled.

### Finding Description
The relevant mechanics in `contracts/strategies/idle/IdleCreditVault.sol`:

1. `requestInstantWithdraw` records receipts under `instantWithdrawsRequestsByEpoch[_user][epochNumber]` and `instantWithdrawClaimsByEpoch[epochNumber]`, and increments the epoch-agnostic `pendingInstantWithdraws` (lines 366–374).
2. `collectInstantWithdrawFunds` only decrements `pendingInstantWithdraws` — it never touches the per-epoch maps (lines 398–403). The code comments explicitly acknowledge `pendingInstantWithdraws` can stay non-zero after `startEpoch` when collected cash "covered only part of the instant queue" (lines 636–640), and `epochNumber` is incremented on the next epoch rollover via `deposit()` (lines 607–610).
3. `defaultPendingClaimBasis()` adds only `instantWithdrawClaimsByEpoch[epochNumber]` — the *current* epoch (lines 644–649). A stale-epoch instant claim contributes nothing to `totalBasis` in `finalizeDefaultRecovery` (line 679), so `recoveryPrice` and `defaultRecoveryReserve` are computed as if those receipts did not exist.
4. On claim, `claimInstantWithdrawRequest` (lines 380–393) clears only `instantWithdrawsRequestsByEpoch[_user][defaultRecoveryEpoch]` via `_claimDefaultedInstantWithdrawRequest` (line 844). A receipt whose entry lives under the *old* epoch is not cleared or haircutted. Execution then continues: `amount = instantWithdrawsRequests[_user]` still contains the full stale claim, the receipt tokens are burned, and `_transferFundedClaim` pays the user 1:1 in underlying (lines 387–392).
5. `_transferFundedClaim` (lines 897–907) only enforces `balance - defaultRecoveryReserve >= amount`; it does not know the stale claim was supposed to be haircutted. Meanwhile `pendingInstantWithdraws` remains artificially > 0, keeping `defaultInstantWithdrawsFinalized` true while the matching per-epoch basis is zero.

Sequence:

- Epoch E (running, instant withdraws enabled): victim and attacker each call `requestInstantWithdraw`. Borrower/`stopEpoch` funding only partially covers the queue, so `pendingInstantWithdraws > 0` while `instantWithdrawClaimsByEpoch[E]` holds the full claim total.
- Epoch E+1 starts (`epochNumber` → E+1). Borrower repays nothing; `stopEpoch` yields zero interest → `defaulted()`.
- `finalizeDefault`/`finalizeDefaultRecovery` runs: `defaultPendingClaimBasis()` returns `pendingWithdraws + instantWithdrawClaimsByEpoch[E+1]` = `pendingWithdraws + 0`. The stale-epoch instant receipts are excluded from `totalBasis`, inflating `defaultRecoveryPrice` above its true pro-rata value, and `defaultInstantWithdrawsFinalized = true`.
- Attacker (any holder of a stale-epoch instant receipt — an unprivileged lender) calls `claimInstantWithdrawRequest` → `_claimDefaultedInstantWithdrawRequest` clears nothing → full `instantWithdrawsRequests[attacker]` paid at par from strategy balance.
- Other claimants' `_transferDefaultRecovery` calls underflow `defaultRecoveryReserve` (line 915) and revert → permanent freezing of their recovery share; alternatively the par payment itself over-consumes the reserve envelope, i.e., direct theft of recovery funds.

### Impact Explanation
Direct theft plus permanent freezing of unclaimed recovery: stale-epoch instant claimants receive 100% instead of `recoveryPrice`, consuming underlying that was distributed pro-rata across `totalBasis`. Once the envelope is exhausted, `_transferDefaultRecovery`'s unchecked subtraction reverts for every remaining claimant (defaulted withdraw receipts, post-default receipts, other instant receipts), permanently locking their recovery. Loss is quantified as `(1 - defaultRecoveryPrice) × instantWithdrawClaimsByEpoch[staleEpoch]` in direct theft, plus the frozen remainder.

### Likelihood Explanation
Requires: (a) instant withdraws enabled with a partially funded queue surviving an epoch rollover — explicitly anticipated by the code's own comments for `startEpoch` partial coverage; (b) a subsequent borrower default. Both are normal operating conditions, not privileged misbehavior. Any user holding an instant receipt from the stale epoch (a KYC-passing lender qualifies as attacker) can trigger it with a single `claimInstantWithdrawRequest` call. The gap is purely a stale epoch-indexed lookup — the same bug class as the advisory's use of freed authz state.

### Recommendation
Make the default-recovery path epoch-agnostic for unfunded instant claims:

- In `defaultPendingClaimBasis()`, add `pendingInstantWithdraws` (the unfunded remainder, which already survives epochs) instead of `instantWithdrawClaimsByEpoch[epochNumber]`, and derive the prefunded portion consistently in `_defaultPrefundedInstantReserve()`.
- In `claimInstantWithdrawRequest`/`_claimDefaultedInstantWithdrawRequest`, treat *any* remaining `instantWithdrawsRequests[_user]` that was unfunded at finalization as a defaulted claim at `defaultRecoveryPrice`, not only the `defaultRecoveryEpoch`-indexed slice; e.g., snapshot funded vs. unfunded per-user shares at finalization or store the request epoch per user (`lastInstantRequestEpoch`) and check `lossRecoveryPriceByEpoch`-style.
- Add a Foundry test reproducing: instant request in epoch E → partial `collectInstantWithdrawFunds` → `startEpoch`/`stopEpoch` default in E+1 → `finalizeDefault` → stale receipt must be paid at `defaultRecoveryPrice`, not par.

### Proof of Concept
Foundry fork sketch (mirrors `test/foundry/IdleCreditVault.t.sol` helpers):

```solidity
// Epoch E: enable instant withdraws, two users request instant withdrawals
cdoEpoch.setInstantWithdrawParams(delay, 1000, false); // manager
cdoEpoch.requestInstantWithdraw(amount, attacker);     // via CDO entrypoint
cdoEpoch.requestInstantWithdraw(amount, victim);
// Borrower/CDO only partially collects -> pendingInstantWithdraws > 0,
// instantWithdrawClaimsByEpoch[E] == amount*2

// Epoch E+1: startEpoch rolls epochNumber -> E+1; borrower repays nothing
cdoEpoch.stopEpoch(0, 0); // default

// Owner finalizes default with `recovered` pulled from manager
cdoEpoch.finalizeDefault(recovered, manager);

// Attacker claims: _claimDefaultedInstantWithdrawRequest clears only epoch E+1
// entries (=0), then full instantWithdrawsRequests[attacker] paid at par
uint256 balPre = underlying.balanceOf(attacker);
cdoEpoch.claimInstantWithdrawRequest(); // attacker prank
assertEq(underlying.balanceOf(attacker) - balPre, amount); // paid 1:1, not * recoveryPrice

// Victim's haircutted claim through _transferDefaultRecovery now reverts on
// defaultRecoveryReserve -= amount (underflow) -> recovery permanently frozen
vm.expectRevert();
cdoEpoch.claimInstantWithdrawRequest(); // victim prank
```

Uncertainty noted: I could not fully trace `IdleCDOEpochVariant`'s instant-funding call sites (the grep matched but returned no context in this pass) to confirm every path that leaves `pendingInstantWithdraws > 0` across an epoch boundary; however, the strategy's own comments at lines 636–640 document that partial funding persists through `startEpoch`, which is the condition the PoC relies on.