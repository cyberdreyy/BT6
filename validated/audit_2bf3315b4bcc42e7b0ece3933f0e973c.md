### Title
Claimed instant-withdraw receipts stay in per-epoch accounting and inflate default-recovery price — (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`claimInstantWithdrawRequest` burns the user's receipt tokens and zeroes `instantWithdrawsRequests[_user]`, but never clears `instantWithdrawsRequestsByEpoch[_user][epoch]` or decrements `instantWithdrawClaimsByEpoch[epoch]`. If the borrower defaults in the same epoch while `pendingInstantWithdraws != 0` (partially funded instant queue), `defaultPendingClaimBasis` and `_defaultPrefundedInstantReserve` still count the already-claimed-and-paid amounts as live claim basis and prefunded reserve. `finalizeDefaultRecovery` then computes `recoveryPrice` on phantom tokens, paying remaining claimants and active-LP recovery at an inflated ratio and draining real underlying from the recovery reserve — a use-after-free/double-count analog of the kernel race where a freed `cmd_sync_work_list` entry is still consumed by `hci_cmd_sync_work`.

### Finding Description
The instant-withdraw lifecycle keeps two parallel ledgers:

- aggregate: `instantWithdrawsRequests[_user]` / `pendingInstantWithdraws`
- per-epoch: `instantWithdrawsRequestsByEpoch[_user][epoch]` / `instantWithdrawClaimsByEpoch[epoch]`

`requestInstantWithdraw` writes both (`IdleCreditVault.sol:366-374`). `collectInstantWithdrawFunds` decrements only `pendingInstantWithdraws` (`IdleCreditVault.sol:401`). `claimInstantWithdrawRequest` zeroes only the aggregate (`IdleCreditVault.sol:387-392`). The per-epoch entries are cleared only inside `_claimDefaultedInstantWithdrawRequest` (`IdleCreditVault.sol:844-853`), i.e. only on the default path.

During default finalization (`finalizeDefaultRecovery`, `IdleCreditVault.sol:661-710`):

1. `defaultPendingClaimBasis` adds `instantWithdrawClaimsByEpoch[epochNumber]` whenever `pendingInstantWithdraws != 0` (`:644-648`). Already-claimed receipts are included.
2. `_defaultPrefundedInstantReserve` returns `instantBasis - pendingInstant` (`:716-723`), counting already-paid-out instant funds as reserve still held by the strategy.
3. `recoveryPrice = reserveAmount * RECOVERY_FULL / totalBasis` is therefore computed with both an inflated numerator (phantom prefunded reserve) and inflated denominator (phantom claim basis).

Broken invariant: one receipt one payout — claimed receipts remain in the "pending work" list after being consumed, exactly mirroring the freed list entry still being processed in `hci_cmd_sync_work`.

### Impact Explanation
Direct theft / insolvency of the default-recovery reserve. The phantom prefunded reserve inflates `reserveAmount` in `recoveryPrice`'s numerator without any real tokens backing it. Every claimant paid through `_transferDefaultRecovery` and every active tranche holder whose NAV is repriced via `defaultBBNav * recoveryPrice` receives more underlying than the actual reserve funds. Because real token balances don't match the inflated price, later claimants or active-LP recovery claims are left underfunded — earlier claimants effectively steal recovery funds belonging to others. For the attacker who already claimed, `_claimDefaultedInstantWithdrawRequest` reverts (`instantWithdrawsRequests[_user] -= claimBasis` underflows against the zeroed aggregate), permanently freezing that user's call path, but the mispriced recovery still executes via other users' claims.

Quantified: with a claimed instant amount `C` already paid out, prefunded reserve is overstated by up to `C` and total basis by `C`; for a reserve of `R` real tokens the price error is roughly `C / totalBasis`, transferring that fraction of remaining claims from honest claimants to whoever claims first.

### Likelihood Explanation
Requires a specific but plausible sequence, all driven by honest actors plus one unprivileged lender:

1. Epoch N buffer: attacker (any KYC-passed tranche holder) calls `requestWithdraw` in instant mode (APR dropped: `lastEpochApr > unscaledApr + instantWithdrawAprDelta`), creating an instant receipt.
2. `startEpoch` collects instant funds via `collectInstantWithdrawFunds`; attacker calls `claimInstantWithdrawRequest` and is paid — per-epoch entries now stale.
3. Another user's instant request remains unfunded, keeping `pendingInstantWithdraws != 0`.
4. Honest borrower fails to repay → `_handleBorrowerDefault`/`finalizeDefaultRecovery` runs in the same `epochNumber` (epoch was running, never stopped, so `epochNumber` unchanged).

No privileged misbehavior required; the only attacker action is a normal withdraw request and claim. Likelihood is moderate: it needs a partially funded instant queue coinciding with a same-epoch default.

### Recommendation
In `claimInstantWithdrawRequest`, clear the per-epoch records for the current epoch (or track claimable epochs): set `instantWithdrawsRequestsByEpoch[_user][epochNumber] = 0` for the claimed portion and decrement `instantWithdrawClaimsByEpoch[epochNumber]` accordingly, so `defaultPendingClaimBasis` and `_defaultPrefundedInstantReserve` only see live receipts. Symmetrically, consider tracking the funded-vs-unfunded split per epoch so a later `collectInstantWithdrawFunds` does not re-inflate the funded remainder.

### Proof of Concept
Foundry fork PoC outline (based on the harness in `test/foundry/IdleCreditVault.t.sol`):

```solidity
function testStaleInstantReceiptInflatesRecovery() external {
    // epoch N-1 ends; APR drops so instant mode is enabled
    _stopEpochAndCheckPrices(N - 1, initialProvidedApr / 4, _expectedFundsEndEpoch());

    // attacker requests instant withdraw, victim requests one too
    uint256 reqA = cdoEpoch.requestWithdraw(mintedAA / 2, address(AAtranche)); // attacker
    vm.prank(victim);
    uint256 reqB = cdoEpoch.requestWithdraw(mintedBB / 2, address(BBtranche));

    // startEpoch funds only attacker's portion (partial funding) ->
    // pendingInstantWithdraws == reqB != 0
    _startEpochPartialInstantFunding(reqA);

    // attacker claims at par; per-epoch entries remain stale
    cdoEpoch.claimInstantWithdrawRequest();

    // borrower defaults mid-epoch; manager/keeper finalizes recovery
    _handleBorrowerDefaultAndFinalize(recoveredAmount);

    // assert: instantWithdrawClaimsByEpoch[epochNumber] still includes reqA
    // assert: defaultRecoveryPrice is inflated vs actual reserve / actual basis
    // assert: victim/active-LP claims overdraw -> later claimant receives less than priced
}
```

Key assertions: `instantWithdrawClaimsByEpoch[epochNumber]` still contains the claimed `reqA`; `recoveryPrice` exceeds `actualReserve / actualBasis`; the last claimant's `_transferDefaultRecovery` either underpays or reverts on insufficient balance — demonstrating theft/freezing of recovery funds.

Caveat: the exact same-epoch timing of `_handleBorrowerDefault` vs `epochNumber` was verified only through `defaultRecoveryEpoch = epochNumber` at `IdleCreditVault.sol:693`; the CDO-side default entry point (`_handleBorrowerDefault` in `IdleCDOEpochVariant.sol`) was not fully read, so the PoC harness wiring for step 4 may need adjustment to match how `defaulted()` is set.