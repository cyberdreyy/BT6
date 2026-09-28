### Title
Funded instant-withdraw claims never clear `instantWithdrawClaimsByEpoch`, inflating default-recovery basis and reserve — (`contracts/strategies/idle/IdleCreditVault.sol`)

### Summary
When an instant-withdraw receipt is funded and claimed through the normal path, `claimInstantWithdrawRequest` zeroes `instantWithdrawsRequests[_user]` but leaves both `instantWithdrawsRequestsByEpoch[_user][epoch]` and the aggregate `instantWithdrawClaimsByEpoch[epoch]` set. If the pool later defaults while another instant request is still unfunded (`pendingInstantWithdraws != 0`), `finalizeDefaultRecovery` treats already-paid-out claims as both additional claim basis *and* prefunded reserve. The recovery price is then computed against phantom underlying, overpaying early claimants and leaving the reserve short for later ones — a cleanup-path reference leak directly analogous to CVE-2015-8953.

### Finding Description
The instant-withdraw lifecycle has three ledgers written at request time (`requestInstantWithdraw`, `IdleCreditVault.sol:366-374`): per-user aggregate `instantWithdrawsRequests`, per-user-per-epoch `instantWithdrawsRequestsByEpoch`, and per-epoch aggregate `instantWithdrawClaimsByEpoch`. The unfunded remainder is tracked in `pendingInstantWithdraws`, which is decremented only by `collectInstantWithdrawFunds` (`:401`).

The cleanup path is asymmetric:

- `claimInstantWithdrawRequest` (`:387-392`) clears only `instantWithdrawsRequests[_user]`. It does **not** decrement `instantWithdrawClaimsByEpoch[epochNumber]` and does **not** clear `instantWithdrawsRequestsByEpoch[_user][epoch]`.
- The only place `instantWithdrawClaimsByEpoch` / `instantWithdrawsRequestsByEpoch` are decremented is the defaulted-epoch path `_claimDefaultedInstantWithdrawRequest` (`:842-855`) — i.e., cleanup is deferred to a path that assumes the receipts were never claimed.

At default finalization, `defaultPendingClaimBasis` (`:644-649`) adds `instantWithdrawClaimsByEpoch[epochNumber]` to the recovery basis whenever `pendingInstantWithdraws != 0`, and `_defaultPrefundedInstantReserve` (`:716-723`) counts `instantWithdrawClaimsByEpoch[epoch] - pendingInstantWithdraws` as underlying *already held* by the strategy. Both are wrong for claims that were funded and already paid out: those tokens left the strategy in `_transferFundedClaim`, yet they are still counted as reserve and as claim basis in `finalizeDefaultRecovery` (`:679-692`).

### Impact Explanation
Broken invariant: solvency / one-receipt-one-payout of the recovery reserve.

Concretely, with a claimed funded instant receipt `X` and a still-unfunded instant receipt `Y` in the default epoch:

- `pendingBasis` is inflated by `X` (`defaultPendingClaimBasis`), and `prefundedReserve` is inflated by `X` (`_defaultPrefundedInstantReserve`), while the strategy holds `X` less underlying than accounted.
- `defaultRecoveryPrice = reserveAmount * RECOVERY_FULL / totalBasis` is therefore computed as if `X` extra underlying were present. Early claimants (defaulted withdraw receipts via `_claimDefaultedWithdrawRequest`, `:772-784`, and instant receipts via `:842-855`) are paid `claimBasis * defaultRecoveryPrice` from `defaultRecoveryReserve`, which is short by `X`. Once `_transferDefaultRecovery` drains the real reserve, subsequent claims revert — **permanent freezing of unclaimed recovery** proportional to `X`.
- Additionally, the stale `instantWithdrawsRequestsByEpoch[_user][defaultEpoch]` lets an already-paid user re-enter `_claimDefaultedInstantWithdrawRequest` for the same receipt (double claim of `X * recoveryPrice`), or traps them in a revert (`instantWithdrawsRequests[_user] -= claimBasis` underflows on a zeroed aggregate), depending on whether they hold other instant receipts.

Loss is quantified as the full value of funded-and-claimed instant receipts in the default epoch, either stolen (double claim) or causing a reserve shortfall that freezes the tail claimants' recovery.

### Likelihood Explanation
Medium. The trigger requires only unprivileged actions plus honest role sequencing:

1. Instant withdrawals enabled (`setInstantWithdrawParams`), epoch running.
2. Attacker (any KYC'd lender) requests an instant withdraw; the honest manager/CDO funds it via `collectInstantWithdrawFunds`; attacker claims normally — all legitimate.
3. A second user requests an instant withdraw that remains unfunded (borrower liquidity shortage is the designed use of `pendingInstantWithdraws`).
4. Borrower defaults; honest manager calls `stopEpoch`/default path and `finalizeDefaultRecovery`.

No malicious privileged action is needed; the stale-reference state is created entirely by the normal claim cleanup path.

### Recommendation
In `claimInstantWithdrawRequest`, mirror the cleanup performed in `_claimDefaultedInstantWithdrawRequest` for the funded path: when paying a funded instant receipt, decrement `instantWithdrawClaimsByEpoch[<the request's epoch>]` and clear `instantWithdrawsRequestsByEpoch[_user][<epoch>]`. This requires the funded path to know the request epoch — e.g., track a `lastInstantWithdrawRequest[_user]` epoch marker analogous to `lastWithdrawRequest`, or iterate/clear per-epoch entries — so that `defaultPendingClaimBasis` and `_defaultPrefundedInstantReserve` only ever count receipts that are still outstanding at finalization.

### Proof of Concept
Foundry fork PoC sketch (extend `test/foundry/IdleCreditVault.t.sol` helpers; assumes standard epoch variant with instant withdraws enabled as in `testProcessWithdrawalClaimsInstantEpoch`):

```solidity
function testStaleInstantClaimInflatesDefaultRecovery() external {
    // epoch running, instant withdraws enabled
    _stopCurrentEpoch();
    vm.prank(manager);
    cdoEpoch.setInstantWithdrawParams(100, 1e18, false);
    vm.prank(manager);
    cdoEpoch.startEpoch();

    IdleCreditVault vault = IdleCreditVault(address(strategy));

    // attacker + victim deposit and request instant withdraws in same epoch
    address attacker = makeAddr('attacker');
    address victim = makeAddr('victim');
    _depositWithUser(attacker, 100e6);
    _depositWithUser(victim, 100e6);
    // attacker requests instant withdraw X = 100e6 (tranche -> strategy receipt)
    // cdoEpoch.requestInstantWithdraw(...) per CDO API; then:
    // manager/CDO funds it: collectInstantWithdrawFunds pulls underlying into vault
    // pendingInstantWithdraws becomes 0 for attacker's piece
    // attacker claims: claimInstantWithdrawRequest(attacker) -> paid 100e6
    //   instantWithdrawsRequests[attacker] == 0
    //   BUT instantWithdrawsRequestsByEpoch[attacker][epoch] == 100e6  (stale)
    //   AND instantWithdrawClaimsByEpoch[epoch] still == 200e6         (stale)

    // victim's request stays unfunded: pendingInstantWithdraws == 100e6
    assertEq(vault.instantWithdrawClaimsByEpoch(vault.epochNumber()), 200e6); // stale

    // borrower defaults: stopEpoch with insufficient repayment -> defaulted
    // owner calls finalizeDefaultRecovery(recovered, recoverySource)
    uint256 basis = vault.defaultPendingClaimBasis();
    // basis == pendingWithdraws + 200e6, but only 100e6 is a real pending claim
    // _defaultPrefundedInstantReserve() returns 200e6 - 100e6 = 100e6 phantom
    // => recoveryPrice computed on 100e6 underlying that was already paid out

    // attacker double-claims via _claimDefaultedInstantWithdrawRequest:
    // instantWithdrawsRequestsByEpoch[attacker][defaultEpoch] = 100e6 still set
    // -> attacker receives 100e6 * defaultRecoveryPrice again, or
    // reserve drains early and victim's claim reverts (frozen recovery)
    assertGt(vault.instantWithdrawsRequestsByEpoch(attacker, vault.defaultRecoveryEpoch()), 0);
}
```

The assertion that `instantWithdrawClaimsByEpoch` and `instantWithdrawsRequestsByEpoch` remain non-zero after a fully paid claim is the leaked reference; the resulting `recoveryPrice` overstatement by `X` underlying is the direct fund impact (reserve shortfall or double payout of `X * recoveryPrice`).