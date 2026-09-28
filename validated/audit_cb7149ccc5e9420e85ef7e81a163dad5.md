### Title
Pending withdraw receipts recorded in a pre-default epoch bypass the recovery haircut and are paid at par (or freeze) after `finalizeDefaultRecovery` - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`IdleCreditVault` tracks withdraw receipts both as an aggregate (`withdrawsRequests[_user]`, `pendingWithdraws`) and per epoch (`withdrawsRequestsByEpoch[_user][epoch]`, keyed by `lastWithdrawRequest`). After a borrower default is finalized, `claimWithdrawRequest` only applies the recovery haircut to receipts stored under `defaultRecoveryEpoch`. Receipts recorded under an earlier epoch — the normal case, since `requestWithdraw` writes the bucket for the epoch in which the request was made while `defaultRecoveryEpoch` is the (later) epoch at finalization — fall through to `_claimFundedWithdrawRequest` and are paid at par even though they were counted into the haircutted claim basis. This is the on-chain analog of the TensorFlow duplicated-`AttrDef` bug: the same logical receipt exists under two keys, and the claim path picks the wrong (un-haircutted) one.

### Finding Description
In `requestWithdraw`, a user's receipt is booked under `currentEpoch = epochNumber` at request time (`withdrawsRequestsByEpoch[_user][currentEpoch] += _amount`, `lastWithdrawRequest[_user] = currentEpoch`) and added to `pendingWithdraws` (contracts/strategies/idle/IdleCreditVault.sol:260-294).

When the borrower defaults, `finalizeDefaultRecovery` sets `defaultRecoveryEpoch = epochNumber` (line 693) and computes `defaultRecoveryPrice` over `totalBasis = activeBasis + defaultPendingClaimBasis()`, where `defaultPendingClaimBasis()` includes the entire aggregate `pendingWithdraws` (lines 644-649, 679-692). So all pending receipts — regardless of which epoch bucket they live in — dilute the recovery reserve at `defaultRecoveryPrice`.

On claim, `claimWithdrawRequest` calls `_claimDefaultedWithdrawRequest`, which calls `_clearWithdrawClaimForEpoch(_user, defaultRecoveryEpoch, true)` (line 774). That clears only `withdrawsRequestsByEpoch[_user][defaultEpoch]`. A user who requested during the running epoch before default has their bucket keyed at that earlier epoch, so `claimBasis == 0` there. Execution continues to `_claimFundedWithdrawRequest` (line 313), which pays the whole `withdrawsRequests[_user]` at par through `_transferFundedClaim`.

Because the unfunded receipt was never backed by a `collectWithdrawFunds` transfer, two bad outcomes exist:
- If the strategy holds any non-reserve underlying (e.g., directly-sent/donated tokens not yet skimmed, or a partially prefunded amount), the user extracts up to `withdrawsRequests[_user]` at 100% while the reserve was priced assuming they receive only `defaultRecoveryPrice` — direct theft from other recovery claimants.
- Otherwise `_transferFundedClaim`'s reserve guard (`balance - reserve < _amount` reverts, lines 897-907) makes the claim revert permanently, freezing that user's recovery even though their basis already diluted everyone else's price — permanent freezing of unclaimed recovery.

Either way the one-receipt-one-payout invariant is broken: the receipt is simultaneously inside the haircutted `pendingBasis` (lowering `defaultRecoveryPrice` for all) and payable outside it.

### Impact Explanation
Every defaulted-epoch recovery claimant receives a strictly smaller `defaultRecoveryPrice` because `pendingWithdraws` includes the attacker's request, while the attacker either (a) drains non-reserve underlying at par — theft equal to `request_amount * (1 - defaultRecoveryPrice)` plus reserve shortfall for later claimants — or (b) finds their claim permanently bricked, since re-requesting is also blocked (`_hasWithdrawRequest` check at line 249 reverts post-default). Quantified: an attacker requesting `R` before default causes all other claimants to lose `R * (1 - price)` of recovery, and can steal up to `R` of stray strategy balance.

### Likelihood Explanation
The trigger sequence is the ordinary unhappy path, not an edge case: a KYC'd lender calls `requestWithdraw` during a running epoch (request booked at `epochNumber = N`), the borrower defaults and `finalizeDefaultRecovery` runs at epoch `N+1` (`defaultRecoveryEpoch = N+1` while the bucket key is `N`). Any unprivileged lender can be in this position; the only precondition is a pending, unfunded withdraw request at default time — precisely what the default-recovery machinery is designed for. Note a fully-funded pending request that borrower paid via `collectWithdrawFunds` has real backing, so paying it at par is correct — but that distinction is exactly what the per-epoch key is supposed to capture and fails to when request epoch ≠ default epoch.

### Recommendation
In `_claimDefaultedWithdrawRequest` / `claimWithdrawRequest`, treat all unfunded pending receipts as defaulted, not only those keyed by `defaultRecoveryEpoch`. Concretely: clear `withdrawsRequestsByEpoch` for every epoch bucket contributing to `withdrawsRequests[_user]` (or track a per-user "pending epoch list"), apply `defaultRecoveryPrice` to receipts that were pending at finalization, and route only receipts verifiably funded before default through `_transferFundedClaim` at par. A simpler fix: snapshot funded vs unfunded portions at `collectWithdrawFunds`/`finalizeDefaultRecovery` per epoch and have the funded path skip epochs with no recorded funding.

### Proof of Concept
Foundry fork PoC sketch (against the existing `test/foundry/IdleCreditVault.t.sol` harness):

```solidity
function testPreDefaultEpochReceiptEscapesHaircut() external {
    // setup: KYC'd attacker deposits, epoch running
    uint256 amt = 100e6;
    _depositWithUser(attacker, amt);
    vm.prank(manager); cdoEpoch.startEpoch();

    // attacker requests withdraw during running epoch N
    _requestWithdrawWithUser(attacker, trancheBal);

    // borrower defaults -> stopEpoch bumps epochNumber to N+1,
    // finalizeDefaultRecovery runs with defaultRecoveryEpoch = N+1
    _stopEpochAndDefault();          // cdo defaulted
    _finalizeRecovery(50e6);         // recoveryPrice < RECOVERY_FULL,
                                     // pendingWithdraws (incl. attacker) in basis

    // stray/donated underlying in strategy, e.g. direct send
    underlying.transfer(address(strategy), 200e6);

    // attacker claims: defaulted path finds bucket[N+1] == 0,
    // funded path pays full 100e6 at par from non-reserve balance
    vm.prank(attacker);
    cdoEpoch.claimWithdrawRequest(); // -> attacker receives ~100e6, not 50e6 * price

    // other claimants' recovery is now underfunded / reverts on reserve guard
}
```

Caveat: I could not fully trace `_handleBorrowerDefault`'s exact `epochNumber` bump ordering relative to `defaultRecoveryEpoch` assignment within the iteration budget, so the PoC assumes `defaultRecoveryEpoch` is strictly greater than the request epoch — consistent with `lastWithdrawRequest` being set at request time and `epochNumber` incrementing at `stopEpoch`/`deposit` during epoch rollover (lines 607-614). If default finalization can occur at the same `epochNumber` as the request epoch, the bug instead manifests for requests pending across a prior buffer boundary.