### Title
Stale instant-withdraw receipt data reused after claim inflates default recovery reserve and permanently freezes recovery funds - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`claimInstantWithdrawRequest` clears the aggregate `instantWithdrawsRequests[_user]` but never clears `instantWithdrawsRequestsByEpoch[_user][epoch]` or `instantWithdrawClaimsByEpoch[epoch]`. If the same epoch later defaults, `finalizeDefaultRecovery` re-reads these stale entries via `_defaultPrefundedInstantReserve` and `defaultPendingClaimBasis`, counting already-claimed-and-paid receipts as both claim basis and "prefunded" reserve. This is the direct analog of a use-after-free: receipt bookkeeping is consumed ("freed") at claim time, then reused by the default-finalization merge path.

### Finding Description
In `claimInstantWithdrawRequest` (IdleCreditVault.sol:380-393), a funded claim burns the user's receipt tokens and zeroes `instantWithdrawsRequests[_user]`, but leaves `instantWithdrawsRequestsByEpoch[_user][currentEpoch]` and `instantWithdrawClaimsByEpoch[currentEpoch]` untouched.

Later, if the borrower defaults in that same epoch and `finalizeDefaultRecovery` runs (lines 661-710):

- `defaultPendingClaimBasis` (644-649) adds `instantWithdrawClaimsByEpoch[epochNumber]` whenever `pendingInstantWithdraws != 0`, including the already-claimed receipts.
- `_defaultPrefundedInstantReserve` (716-723) computes `instantBasis - pendingInstant` as "already-held underlying" and adds it to `reserveAmount` — but those underlyings were already paid out to the claimer. This is phantom reserve.
- `recoveryPrice = reserveAmount * RECOVERY_FULL / totalBasis` is therefore computed against funds the strategy does not hold, while `defaultRecoveryReserve` is set to the inflated `reserveAmount`.

Additionally, when the already-paid user (or the CDO on their behalf) calls `claimInstantWithdrawRequest` after finalization, `_claimDefaultedInstantWithdrawRequest` (842-856) executes `instantWithdrawsRequests[_user] -= claimBasis` where the aggregate is already 0, causing an underflow revert.

Attacker path (all unprivileged): a KYC-passed lender calls `requestInstantWithdraw` during buffer/running phase; the honest manager's `startEpoch` only partially collects instant funds (`collectInstantWithdrawFunds`, lines 398-403, accepts any `_amount <= pendingInstantWithdraws`); the attacker claims the funded portion; the epoch then defaults via the honest `_handleBorrowerDefault` flow and `finalizeDefault`/`finalizeDefaultRecovery` runs.

### Impact Explanation
`defaultRecoveryPrice` is inflated by the phantom prefunded reserve. Early recovery claimers (including the attacker via defaulted-epoch normal receipts or a second position) are paid from a reserve that is smaller on-chain than `defaultRecoveryReserve` records. Once the real balance is exhausted, `_transferDefaultRecovery` reverts on the ERC20 transfer for all remaining claimers — permanent freezing of their unclaimed default recovery. Quantified loss = `instantWithdrawClaimsByEpoch[E] - pendingInstantWithdraws` for the already-claimed portion, i.e. the full amount of instant receipts claimed in the defaulted epoch.

### Likelihood Explanation
Requires: instant withdraws enabled (`allowInstantWithdraw`), at least one instant receipt claimed in an epoch, a partially unfunded instant queue (`pendingInstantWithdraws != 0`) so `defaultInstantWithdrawsFinalized` is set, and a borrower default in that epoch. Defaults are a designed-for state in this codebase and partial instant funding is explicitly supported (`pendingInstantWithdraws` is the "unfunded remainder"). No privileged misbehavior needed; the attacker only needs an ordinary instant-withdraw claim timed before the default.

### Recommendation
In `claimInstantWithdrawRequest`, clear per-epoch receipt data for funded claims (decrement `instantWithdrawClaimsByEpoch[requestEpoch]` and zero `instantWithdrawsRequestsByEpoch[_user][requestEpoch]`), or track the funded/unfunded split per receipt so `defaultPendingClaimBasis` and `_defaultPrefundedInstantReserve` only count receipts still held by the user.

### Proof of Concept
Foundry fork test outline:

```solidity
// Epoch E running, instant withdraws enabled.
// 1. Alice deposits into AA, then requests instant withdraw of 100e18.
vm.prank(alice); cdo.requestInstantWithdraw(100e18, AA);

// 2. Bob also requests 40e18 instant.
vm.prank(bob); cdo.requestInstantWithdraw(40e18, AA);

// 3. startEpoch collects only 100e18 of the 140e18 instant queue
//    (collectInstantWithdrawFunds(100e18) -> pendingInstantWithdraws = 40).
vm.prank(manager); cdo.startEpoch();

// 4. Alice claims her fully-funded receipt: strategy pays 100e18.
//    instantWithdrawsRequests[alice] = 0 but
//    instantWithdrawsRequestsByEpoch[alice][E] and
//    instantWithdrawClaimsByEpoch[E] stay 100/140.
vm.prank(alice); cdo.claimInstantWithdrawRequest();

// 5. Borrower defaults in epoch E; manager finalizes.
vm.prank(manager); cdo.stopEpochWithDuration(...); // or default path
cdo.finalizeDefault(...); // -> finalizeDefaultRecovery

// 6. Assertions:
//    _defaultPrefundedInstantReserve counted 100e18 as held reserve,
//    but only 40e18 (or less) of actual underlying backs it:
assertGt(strategy.defaultRecoveryReserve(),
         underlying.balanceOf(address(strategy)) /* for recovery claims */);
//    defaultRecoveryPrice is inflated; last recovery claimer reverts:
vm.expectRevert(); // ERC20 insufficient balance
vm.prank(lastClaimer); cdo.claimInstantWithdrawRequest();
```