### Title
Loss-adjusted withdraw receipts keyed under wrong epoch via `epochNumber` desync, letting underfunded receipts claim at par — (`contracts/strategies/idle/IdleCreditVault.sol`)

### Summary
`IdleCreditVault` tracks the same "withdraw request epoch" through two representations: per-receipt keys written at request time (`withdrawsRequestsByEpoch[user][epochNumber]`, `apr0Users[user].principalEpoch`, `lastWithdrawRequest`) and per-epoch loss prices written at funding time (`lossRecoveryPriceByEpoch[epochNumber]`). `epochNumber` is incremented inside `deposit()` whenever `isEpochRunning` is still true (i.e., inside `stopEpoch`), while `collectWithdrawFunds` writes `lossRecoveryPriceByEpoch[epochNumber]` at whatever `epochNumber` is current at that moment. If `epochNumber` is bumped before `collectWithdrawFunds` records the haircut, the haircut lands on a key that no pending receipt actually uses — a key-path desynchronization directly analogous to the toml-node bug where a comma-joined guard path and a dot-joined lookup path for the same key let a duplicate write slip past the duplicate-key guard.

### Finding Description
- Requests made during the buffer/idle phase record `lastWithdrawRequest[user] = epochNumber` and `withdrawsRequestsByEpoch[user][epochNumber]` (IdleCreditVault.sol:282-293).
- `deposit()` increments `epochNumber` while `isEpochRunning` is still set, i.e., during `stopEpoch` (IdleCreditVault.sol:607-611).
- `collectWithdrawFunds` stores a partial-funding haircut under `lossRecoveryPriceByEpoch[epochNumber]` (IdleCreditVault.sol:414-421).
- The duplicate-key guard in `requestWithdraw` only checks `lossRecoveryPriceByEpoch[lastWithdrawRequest[user]]` (IdleCreditVault.sol:261-271), and `_claimLossAdjustedWithdrawRequest` only reads `lossRecoveryPriceByEpoch[lastWithdrawRequest[user]]` (IdleCreditVault.sol:789-794).

If the haircut is stored under `N+1` while the affected receipts are keyed under `N`, both the request-time guard and the claim-time haircut lookup silently miss (they read key `N`, which is 0 = "no loss-adjusted epoch"). The underfunded receipts then fall through to `_claimFundedWithdrawRequest` (IdleCreditVault.sol:319-350), which pays `withdrawsRequests[user]` at par even though `collectWithdrawFunds` pulled less underlying than `pendingWithdraws`.

### Impact Explanation
Receipt holders whose requests were haircutted claim their full basis instead of the loss-adjusted amount. Since the strategy only received `amount < pendingBasis`, the shortfall is paid out of underlyings backing other funded claims or the recovery reserve, directly stealing value from other claimants / tranche holders and breaking the "one receipt, one haircutted payout" invariant. Quantified loss equals `pendingBasis - fundedAmount` (the unrecognized loss), claimable by the first receipts to withdraw.

### Likelihood Explanation
An unprivileged tranche-token holder triggers the payoff simply by calling `requestWithdraw` during a buffer phase and then `claimWithdrawRequest` after a `stopEpoch` where the borrower underfunds pending withdrawals (`stopEpochWithDuration` loss path). The only external precondition is that the epoch ends with a realized loss while pending receipts exist — a normal protocol path, not an attacker-induced one. Likelihood depends on the exact call ordering inside `IdleCDOEpochVariant.stopEpoch` (whether the `deposit()` that bumps `epochNumber` precedes `collectWithdrawFunds`), which should be verified in the PoC; if `collectWithdrawFunds` runs before the bump, this path is consistent and the issue does not manifest.

### Recommendation
Record the loss haircut under the epoch key that the pending receipts were actually written to — i.e., the epoch in effect when the requests were made (the pre-bump `epochNumber`, or a dedicated `pendingReceiptsEpoch` captured at request time). Alternatively, capture `pendingEpoch = epochNumber` before any bump inside `stopEpoch` and write `lossRecoveryPriceByEpoch[pendingEpoch]`, so the write key and the `lastWithdrawRequest`/`withdrawsRequestsByEpoch` lookup keys can never diverge.

### Proof of Concept
Foundry fork test (extend `test/foundry/IdleCreditVault.t.sol`):

```solidity
function testLossReceiptEpochDesync() external {
    // 1. Lender deposits AA and requests withdraw during buffer (epoch E).
    //    -> lastWithdrawRequest[user] = E, withdrawsRequestsByEpoch[user][E] = amt
    // 2. Borrower repays at stopEpoch with a loss such that
    //    collectWithdrawFunds(funded < pendingWithdraws) runs AFTER deposit()
    //    bumped epochNumber to E+1.
    //    -> lossRecoveryPriceByEpoch[E+1] = price < 1e18, pendingWithdraws = 0
    // 3. Assert the desync:
    assertEq(strategy.lossRecoveryPriceByEpoch(E), 0);
    assertGt(strategy.lossRecoveryPriceByEpoch(E + 1), 0);
    // 4. claimWithdrawRequest: _claimLossAdjustedWithdrawRequest reads key E -> 0,
    //    falls through to _claimFundedWithdrawRequest paying full basis at par.
    cdoEpoch.claimWithdrawRequest();
    //    expected haircutted payout = amt * lossRecoveryPriceByEpoch[E+1] / 1e18
    //    actual payout = amt  -> excess paid out of other claimants' funded reserve
}
```

Caveat: the exploitability hinges on the ordering of `deposit()` (which bumps `epochNumber`, IdleCreditVault.sol:610) versus `collectWithdrawFunds` inside `IdleCDOEpochVariant.stopEpoch`, which could not be fully verified within the available context. If `collectWithdrawFunds` executes before the `deposit()` that increments `epochNumber`, the keys match and no vulnerability exists on this path.