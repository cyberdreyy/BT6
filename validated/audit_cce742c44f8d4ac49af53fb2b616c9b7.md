### Title
Funded instant-withdraw claims never decrement `instantWithdrawClaimsByEpoch`/`instantWithdrawsRequestsByEpoch`, inflating the default-recovery basis and prefunded reserve so `finalizeDefaultRecovery` mints unbacked strategy tokens and overpays recovery claimants - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`requestInstantWithdraw` records each receipt three ways: `instantWithdrawsRequests[user]`, `instantWithdrawsRequestsByEpoch[user][epoch]`, and the global `instantWithdrawClaimsByEpoch[epoch]` (contracts/strategies/idle/IdleCreditVault.sol:366-374). The normal funded claim path `claimInstantWithdrawRequest` only clears `instantWithdrawsRequests[user]`; the per-epoch entries stay populated forever (IdleCreditVault.sol:387-392). Both stale aggregates are later consumed by `defaultPendingClaimBasis` (IdleCreditVault.sol:644-649) and `_defaultPrefundedInstantReserve` (IdleCreditVault.sol:716-723) inside `finalizeDefaultRecovery`. This is the same bug class as the external report: a "withdrawn" ledger is not reduced when a payout completes, so a later share-of-funds computation (`defaultRecoveryPrice = reserve / basis`) is corrupted.

### Finding Description
`claimInstantWithdrawRequest` pays the user at par and burns their receipt, but leaves `instantWithdrawsRequestsByEpoch[user][epochNumber]` and `instantWithdrawClaimsByEpoch[epochNumber]` untouched (IdleCreditVault.sol:387-392). If the borrower then defaults in the same `epochNumber` while another user's instant receipt is still unfunded (`pendingInstantWithdraws != 0`), `finalizeDefaultRecovery` computes:

- `defaultPendingClaimBasis()` adds `instantWithdrawClaimsByEpoch[epochNumber]`, which still contains the already-paid amount `P` → basis is overstated by `P`.
- `_defaultPrefundedInstantReserve()` returns `instantBasis - pendingInstant`, which assumes all non-pending instant basis is cash still held by the strategy. For already-claimed receipts that cash is gone → `prefundedReserve` is overstated by `P` (IdleCreditVault.sol:716-723).
- `defaultRecoveryPrice = (recovered + prefundedReserve + defaultRecoveryReserve) / totalBasis` is therefore set with phantom cash in the numerator (IdleCreditVault.sol:686-692).
- `activeFinalNAV = activeBasis * recoveryPrice` is minted to the CDO via `_mint(idleCDO, ...)` (IdleCreditVault.sol:699-705) — unbacked strategy tokens that flow into tranche prices through `_forceUpdateAccounting`, and can be monetized by post-default withdraw requests paid 1:1 from `defaultRecoveryReserve` (`_claimPostDefaultWithdrawRequest`, IdleCreditVault.sol:760-767; `requestWithdraw` post-default branch, IdleCreditVault.sol:247-257).

Neither `_skimDonatedAssets`, the `NotAllowed` epoch guards, nor the underflow checks stop this: the already-claimed user's own re-claim path reverts on underflow, but the price inflation itself is silent and the theft is executed by any post-default claimant.

### Impact Explanation
Solvency and fair-recovery invariants break. The recovery price is computed against cash that was already paid out, so (a) the CDO is minted unbacked strategy tokens that inflate `virtualPrice` for tranche holders who then withdraw post-default at inflated NAV, and (b) every defaulted claim is paid `claimBasis * defaultRecoveryPrice` from a reserve that does not contain the phantom `P`. Early claimants drain the reserve; later claimants and honest tranche holders absorb the shortfall. Loss magnitude is bounded by the total instant receipts claimed before the default within the same epoch, which can be the entire instant queue.

### Likelihood Explanation
Requires: instant withdrawals enabled (`allowInstantWithdraw`), a user claiming a funded instant receipt, another instant receipt still pending (`pendingInstantWithdraws != 0`), and a borrower default before the next successful `stopEpoch` (which would bump `epochNumber`). All actors are unprivileged or honest: the attacker only claims their own funded receipt and later files ordinary post-default withdraw requests. The default can arise naturally from a failed `getInstantWithdrawFunds`/stopEpoch funding pull — no privileged misbehavior needed.

### Recommendation
In `claimInstantWithdrawRequest`, clear the per-epoch ledger alongside the aggregate: decrement `instantWithdrawsRequestsByEpoch[_user][epochOfRequest]` and `instantWithdrawClaimsByEpoch[epochOfRequest]` by the claimed amount (mirroring `_claimDefaultedInstantWithdrawRequest` at IdleCreditVault.sol:847-853). Since `lastWithdrawRequest`-style per-epoch tracking for instant claims is keyed on the request epoch, store the request epoch or iterate it so the funded claim reduces the same bucket that `defaultPendingClaimBasis` and `_defaultPrefundedInstantReserve` read.

### Proof of Concept
Foundry fork PoC (extend `test/foundry/IdleCreditVault.t.sol` setup):

```solidity
function testStaleInstantBasisInflatesRecovery() external {
    // epoch running, instant withdraws enabled
    _startEpochAndCheckPrices(0);
    vm.warp(cdoEpoch.instantWithdrawDeadline() + 1);

    // attacker + victim both request instant withdraws in the same epoch
    uint256 amtA = 50_000 * ONE_SCALE; // attacker
    uint256 amtV = 50_000 * ONE_SCALE; // victim
    // ... requestInstantWithdraw for both via cdoEpoch.requestWithdraw instant path ...

    // manager funds only the attacker's receipt: borrower honest, partial funding
    // getInstantWithdrawFunds pulls amtA -> collectInstantWithdrawFunds(amtA)
    // attacker claims at par:
    cdoEpoch.claimInstantWithdrawRequest(); // instantWithdrawsRequests=0, but ByEpoch entries stay

    // borrower defaults with victim's pendingInstantWithdraws still unfunded
    // -> _handleBorrowerDefault -> finalizeDefault -> finalizeDefaultRecovery
    // inside: instantWithdrawClaimsByEpoch[epoch] still = amtA + amtV
    // prefundedReserve = amtA + amtV - amtV = amtA  (phantom: amtA already paid out)
    // defaultRecoveryPrice is computed as if amtA cash is still in the strategy

    // assert: defaultRecoveryPrice > realReserve / realBasis
    // assert: strategyToken.balanceOf(idleCDO) exceeds actual underlying backing
    // attacker files postDefaultRequests and drains reserve; last claimant reverts/underpays
}
```

Key assertion: after `claimInstantWithdrawRequest`, `instantWithdrawClaimsByEpoch[epochNumber]` is still `amtA + amtV` instead of `amtV`, directly demonstrating the non-decremented "withdrawn" counter that corrupts `finalizeDefaultRecovery`.

Caveat: the exact wiring of `instantWithdrawDeadline`/`allowInstantWithdraw` timing in tests may need adjustment, but the missing decrements in `claimInstantWithdrawRequest` (IdleCreditVault.sol:387-392) versus the epoch-bucket reads in `defaultPendingClaimBasis` (IdleCreditVault.sol:644-649) and `_defaultPrefundedInstantReserve` (IdleCreditVault.sol:716-723) are directly verifiable in the code above.