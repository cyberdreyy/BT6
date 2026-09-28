### Title
Claimed instant-withdraw receipts stay in `instantWithdrawClaimsByEpoch`/`instantWithdrawsRequestsByEpoch`, inflating default-recovery basis and insolventing the reserve - (contracts/strategies/idle/IdleCreditVault.sol)

### Summary
When a user claims a funded instant-withdraw receipt, `claimInstantWithdrawRequest` burns the receipt and zeroes `instantWithdrawsRequests[_user]`, but it never decrements the per-epoch accounting added by `requestInstantWithdraw`: `instantWithdrawsRequestsByEpoch[_user][epoch]` and `instantWithdrawClaimsByEpoch[epoch]`. If the same epoch later defaults while `pendingInstantWithdraws != 0` (partial funding), `finalizeDefaultRecovery` counts the already-paid-out receipt a second time — once in `defaultPendingClaimBasis()` and again in `_defaultPrefundedInstantReserve()` — diluting `defaultRecoveryPrice` and leaving phantom claims the reserve can never pay.

### Finding Description
`requestInstantWithdraw` records three cumulative counters: `instantWithdrawsRequests[_user]`, `instantWithdrawsRequestsByEpoch[_user][currentEpoch]`, and `instantWithdrawClaimsByEpoch[currentEpoch]` (`IdleCreditVault.sol:366-374`). On a normal claim, `claimInstantWithdrawRequest` only clears `instantWithdrawsRequests[_user]` (`IdleCreditVault.sol:387-392`). The two per-epoch counters are only cleared in the dedicated post-default path `_claimDefaultedInstantWithdrawRequest` (`IdleCreditVault.sol:842-856`).

The leak/double-allocation sequence:

1. Buffer phase: attacker (any KYC'd tranche holder) calls `requestWithdraw` while `lastEpochApr > unscaledApr + instantWithdrawAprDelta`, so the CDO calls `requestInstantWithdraw`, minting a receipt and incrementing `instantWithdrawClaimsByEpoch[epoch]` by `X`.
2. `startEpoch` collects only part of the pending instant funds (`collectInstantWithdrawFunds` reduces `pendingInstantWithdraws`, e.g. `pending = Y < X`). `allowInstantWithdraw` stays false until funded, but `getInstantWithdrawFunds` after `instantWithdrawDeadline` pulls the remainder and sets `allowInstantWithdraw = true` — or full funding happens at `startEpoch` while a *second* user's request remains unfunded, keeping `pendingInstantWithdraws != 0`.
3. The attacker calls `claimInstantWithdrawRequest`, burns the receipt and receives `X`. `instantWithdrawClaimsByEpoch[epoch]` still contains `X`.
4. At `stopEpoch`, `getFundsFromBorrower` fails → `_handleBorrowerDefault` sets `defaulted`.
5. `finalizeDefault` → `finalizeDefaultRecovery`: `defaultPendingClaimBasis()` adds `instantWithdrawClaimsByEpoch[epochNumber]` because `pendingInstantWithdraws != 0` (`IdleCreditVault.sol:644-648`), and `_defaultPrefundedInstantReserve()` adds `instantBasis - pendingInstant`, i.e. counts the already-paid `X` as if it were still cash held by the strategy (`IdleCreditVault.sol:716-723`).

The paid-out funds are therefore counted twice: as claim basis *and* as reserve backing.

### Impact Explanation
Direct insolvency of the default-recovery reserve and permanent freezing of recovered funds:

- `recoveryPrice = reserveAmount / totalBasis` uses a `reserveAmount` inflated by phantom prefunded cash (`_defaultPrefundedInstantReserve` assumes `instantBasis - pendingInstant` is held, but claimed funds were transferred out) and a `totalBasis` inflated by the already-claimed `X`. Every legitimate defaulted-epoch claimant and post-finalization claimant draws against `defaultRecoveryReserve`, so later claimants hit an empty reserve and their claims revert in `_transferDefaultRecovery` — permanent freezing of their recovered share.
- Alternatively, if the claimed user tries `_claimDefaultedInstantWithdrawRequest`, `instantWithdrawsRequests[_user] -= claimBasis` underflows on their zeroed aggregate (`IdleCreditVault.sol:848`), so the stale basis entry can never be removed; the corresponding reserve slice is permanently locked as unreachable dust.
- Loss is quantified: up to the full amount of any instant receipt claimed in the defaulted epoch is double-counted, e.g. with `X` claimed and `P` real reserve, the last `min(X, P)` of honest claims is unpayable.

### Likelihood Explanation
Likelihood is moderate, driven by ordinary protocol sequencing rather than attacker privilege:

- Requires instant withdrawals enabled (`disableInstantWithdraw = false`, non-programmable borrower) and an APR decrease triggering the instant path — a documented, supported mode.
- Requires a borrower default in the same epoch where at least one instant receipt was claimed while `pendingInstantWithdraws` is still non-zero. Partial funding at `startEpoch` plus funding via `getInstantWithdrawFunds` is exactly the designed flow for large instant queues, so the overlap is realistic.
- No attacker needs special access: a tranche-token holder simply requests an instant withdraw and claims when funded; the accounting corruption occurs automatically at finalization. The manager/borrower remain honest throughout.

### Recommendation
In `claimInstantWithdrawRequest`, mirror the receipt cleanup done in `_claimDefaultedInstantWithdrawRequest`: decrement `instantWithdrawsRequestsByEpoch[_user][epochNumber]` (or track the request epoch per user) and `instantWithdrawClaimsByEpoch[epochNumber]` by the claimed amount, and subtract the funded portion from a per-epoch funded-tracking counter so `_defaultPrefundedInstantReserve` only counts cash still held. Concretely, record the epoch at request time (e.g. an `instantRequestEpoch[_user]` mapping) and clear both per-epoch counters whenever a receipt is paid or rolled into a new request, ensuring `instantWithdrawClaimsByEpoch` only ever reflects unclaimed receipts.

### Proof of Concept
Foundry fork PoC sketch (Solidity, against mainnet fork with an instantiated `IdleCDOEpochVariant` + `IdleCreditVault` pair):

```solidity
// setup: deposits done, instant withdraws enabled, APR decreased last epoch stop
// 1) attacker requests instant withdraw during buffer
vm.prank(attacker);
uint256 X = cdo.requestWithdraw(0, address(AAtranche)); // instant path taken

// 2) honest user requests instant too; total instant = X + Y
vm.prank(honest);
uint256 Y = cdo.requestWithdraw(0, address(BBtranche));

// 3) startEpoch: strategy only partially funded -> pendingInstantWithdraws stays > 0
vm.prank(manager);
cdo.startEpoch();
// or: after deadline, fund only part of the queue
vm.warp(cdo.instantWithdrawDeadline() + 1);
// borrower approves only X
vm.prank(manager);
cdo.getInstantWithdrawFunds(); // pendingInstantWithdraws = Y still owed
assertEq(strategy.pendingInstantWithdraws(), Y);

// 4) attacker claims funded X receipt
vm.prank(attacker);
cdo.claimInstantWithdrawRequest(); // paid in full; by-epoch counters NOT cleared

// 5) borrower defaults at stopEpoch
vm.warp(cdo.epochEndDate() + 1);
vm.prank(manager);
cdo.stopEpoch(0, 0); // getFundsFromBorrower fails -> defaulted = true

// 6) finalize recovery
uint256 recovered = /* partial recovery */;
vm.prank(manager);
cdo.finalizeDefault(recovered, recoverySource);

// BUG: defaultPendingClaimBasis included X (already paid), and
// _defaultPrefundedInstantReserve counted X as held cash.
uint256 basis = strategy.defaultPendingClaimBasis();
assertGt(basis, realOutstandingBasis); // inflated by X
// honest user's defaulted instant claim reverts or underpays:
vm.prank(honest);
vm.expectRevert(); // SafeERC20/underflow when reserve exhausted
cdo.claimInstantWithdrawRequest();
```

Key assertion: after step 4, `strategy.instantWithdrawClaimsByEpoch(strategy.epochNumber())` still equals `X + Y` while `instantWithdrawsRequests[attacker]` is `0`, proving the double-count that corrupts `defaultRecoveryPrice` and strands reserve funds.