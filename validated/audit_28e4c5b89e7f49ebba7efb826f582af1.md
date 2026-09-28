### Title
`_updateAccounting` reverts with an unhandled panic on BB-wiping loss, permanently blocking loss crystallization - (File: contracts/IdleCDOCreditVault.sol)

### Summary

The external report describes a PMM swap that computes an adjusted target value (B0/Q0), uses it for the swap math, but then persists only one of the two targets — leaving the other stale and corrupting all future pricing. The analog in `IdleCDOCreditVault._updateAccounting` is the mirror image of the same bug class: the code computes an adjusted tranche NAV (a BB NAV that must be clamped to zero when the junior tranche is wiped out), uses the raw unclamped value, and never gets the chance to persist the correct adjusted state because the `uint256(int256)` conversion reverts first. Unlike the base `IdleCDO._updateAccounting`, which explicitly writes `lastNAVBB = 0` in the default branch, the credit-vault override unconditionally writes `lastNAVBB = uint256(int256(_lastNAVBB) + _totalBBGain)` before the wipe check — so any loss exceeding BB NAV reverts with a generic panic instead of reaching `revert Default()` / `_emergencyShutdown(true)`.

### Finding Description

In `contracts/IdleCDOCreditVault.sol`:

```solidity
(uint256 _priceAA, int256 _totalAAGain) = _virtualPriceAux(AATranche, nav, _lastNAV, _lastNAVAA, _aprSplitRatio);
(uint256 _priceBB, int256 _totalBBGain) = _virtualPriceAux(BBTranche, nav, _lastNAV, _lastNAVBB, _aprSplitRatio);
lastNAVAA = uint256(int256(_lastNAVAA) + _totalAAGain);
lastNAVBB = uint256(int256(_lastNAVBB) + _totalBBGain);   // panics when _totalBBGain < -_lastNAVBB

if ((_totalBBGain < 0 && -_totalBBGain >= int256(_lastNAVBB)) || (_lastNAV != 0 && nav == 0)) {
  shutdown = true;
  if (!skipDefaultCheck) revert Default();
  if (nav == 0) _priceAA = 0;
  _emergencyShutdown(true);
}
```

Compare with the base implementation `contracts/IdleCDO.sol` lines 285–308, which assigns `lastNAVAA`, then checks the BB-wipe condition **first** and writes `lastNAVBB = 0` inside the branch — the "adjusted target" is persisted instead of the raw sum.

When a strategy loss (e.g., borrower loss socialized via `stopEpochWithDuration(_lossAmount)` → `burnStrategyTokens` → `_forceUpdateAccounting`, or a direct strategy-token value drop) is larger than `lastNAVBB` but smaller than total NAV, `_totalBBGain < -_lastNAVBB` and line 236 reverts with a `Panic(0x11)` — not `Default()`. Crucially:

- The guarded path is dead code: `revert Default()` and `_emergencyShutdown(true)` are unreachable in exactly the scenario they exist for.
- The owner/guardian escape hatch fails too: `updateAccounting()` → `_forceUpdateAccounting()` → `_updateAccounting()` hits the same panic, even with `skipDefaultCheck = true`, because the revert happens *before* the `skipDefaultCheck` check.
- `lastNAVBB` is never updated, so the stale NAV remains; every subsequent `_updateAccounting()` (called from `requestWithdraw`, `_deposit`, `depositDuringEpoch`, `harvest`) re-computes the same oversized loss and reverts again.

### Impact Explanation

Permanent freezing of all user funds. Once a loss exceeding BB NAV is realized in `getContractValue()` (which requires no privileged action — e.g., an unprivileged holder's withdraw request triggers `_updateAccounting` after a real strategy loss, or the strategy-token balance is burned via the loss path), every deposit, withdraw request, claim-driving accounting call, and the owner's own `updateAccounting()`/`finalizeDefault` prerequisites revert. Withdrawal requests are gated on `_updateAccounting()` in `requestWithdraw` (IdleCDOEpochVariant.sol:750), and `finalizeDefault` calls `_forceUpdateAccounting` (line 213), so even the default-recovery path cannot proceed past the panic. The loss can never be crystallized, `lastNAVBB` stays stale (the exact "adjusted value never saved" bug class), and AA principal is locked in the vault/strategy indefinitely.

Loss magnitude: full active NAV (AA + BB) becomes unreachable, versus the intended behavior of crystallizing the loss, zeroing `lastNAVBB`, and letting AA holders withdraw at the haircut price.

### Likelihood Explanation

The trigger is an ordinary tail-risk event the contract is explicitly designed for: a loss larger than the junior tranche (a partial borrower default or `stopEpochWithDuration` with `_lossAmount > lastNAVBB`). No malicious privileged role is needed — an unprivileged lender calling `requestWithdraw` after the strategy loss is enough to hit the panic, and the state is then unrecoverable because the privileged recovery paths (`updateAccounting` with `skipDefaultCheck`, `finalizeDefault`) revert on the same line. The base contract handled this case; the credit-vault refactor (which moved the `lastNAVBB` write above the branch to keep both NAVs written unconditionally) reintroduced it.

### Recommendation

Mirror the base `IdleCDO._updateAccounting` ordering: compute `_totalBBGain`, detect the wipe condition, and write `lastNAVBB = 0` (and clamp `lastNAVAA` to `nav - unclaimedFees` headroom) inside the shutdown branch instead of converting a negative `int256` to `uint256`. E.g., move both NAV writes below the wipe check, or guard line 236 with the same condition evaluated beforehand.

### Proof of Concept

```solidity
// Foundry fork test against contracts/IdleCDOCreditVault.sol
function testBBWipePanicBlocksAccounting() external {
  uint256 amountAA = 9_000 * ONE_SCALE;
  uint256 amountBB = 1_000 * ONE_SCALE;
  idleCDO.depositAA(amountAA);
  idleCDO.depositBB(amountBB);

  // Realize a loss larger than lastNAVBB but smaller than total NAV
  // (e.g., burn strategy tokens so getContractValue() drops by ~2x lastNAVBB).
  _createLoss(2 * cdoEpoch.lastNAVBB() * FULL_ALLOC / (amountAA + amountBB) / 1);

  // Any user action reverts with panic, NOT Default()
  vm.expectRevert(); // Panic(0x11), never IdleCDO.Default.selector
  idleCDO.depositAA(1);

  // Owner recovery path also reverts: skipDefaultCheck branch is unreachable
  vm.prank(owner);
  vm.expectRevert(); // Panic(0x11)
  IdleCDO(address(cdoEpoch)).updateAccounting();

  // lastNAVBB was never adjusted — stale value persists forever
  assertEq(cdoEpoch.lastNAVBB(), amountBB);
  // All funds permanently frozen: every accounting entry point reverts.
}
```