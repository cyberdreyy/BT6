### Title
Dust queued deposit causes permanent `processDeposits` revert, freezing all depositors' funds for the epoch - (File: contracts/IdleCDOEpochQueue.sol)

### Summary
A `CHECK`-fail / assertion-style bug class: a single unprivileged dust `requestDeposit` makes `processDeposits` revert forever via division by zero (`_trancheMinted == 0`) at `IdleCDOEpochQueue.sol:244`, leaving `epochPendingDeposits` nonzero and bricking `claimDepositRequest` for every depositor in that epoch.

### Finding Description
`requestDeposit` (IdleCDOEpochQueue.sol:103-127) accepts any positive amount from any KYC-allowed wallet into `epochPendingDeposits[nextEpoch]`. During the buffer period the manager calls `processDeposits`, which deposits the aggregate `_pending` into the CDO and records:

```
epochPrice[_epoch] = _pending * ONE_TRANCHE / _trancheMinted;
```

If `_pending` is small relative to the tranche's virtual price, `depositAA`/`depositBB` mints zero tranche tokens, so `_trancheMinted == 0` and the division reverts (and if the CDO reverts on zero-mint inside `depositAA`, the result is the same). There is no `_trancheMinted == 0` fallback in the legacy path — the prefunded variant explicitly added one at `IdleCDOEpochQueue.sol:268-270` ("it's possible that the tranche minting results in 0 shares due to rounding"), confirming the bug class while leaving this path unprotected.

Once it reverts, `epochPendingDeposits[_epoch]` is never reset and `epochPrice[_epoch]` stays 0. `claimDepositRequest` (lines 373-379) reverts whenever `epochPrice[_epoch] == 0 || epochPendingDeposits[_epoch] != 0`, so all queued underlying for that epoch — including honest users' deposits — is locked in the queue contract. Retrying `processDeposits` keeps reverting because the virtual price does not move during the buffer period, and only the attacker can remove the dust via `deleteRequest`.

### Impact Explanation
Permanent freezing of all users' queued deposits for the affected epoch: every honest depositor's underlying sits in the queue with no recovery path, since the epoch can never be priced. Broken invariants: fair mint (zero-share deposits accepted) and deposit liveness. Loss equals the full `epochPendingDeposits[_epoch]` balance, which can include arbitrarily large honest deposits made before or after the dust deposit.

### Likelihood Explanation
The attacker needs only to be KYC-allowed and to call `requestDeposit(1)` (or any dust amount whose minted tranche rounds to zero) during an epoch; the request lands in the next epoch's bucket. The freeze triggers as soon as the manager processes deposits in the buffer period — a routine, honest call. No privileged misbehavior is required. Cost is dust.

### Recommendation
Guard the zero-mint case in `processDeposits` the same way `processPrefundedDeposits` does: e.g. `epochPrice[_epoch] = _trancheMinted == 0 ? _cdo.virtualPrice(tranche) : _pending * ONE_TRANCHE / _trancheMinted;`, or enforce a minimum deposit amount in `requestDeposit` so a queued deposit always mints at least one tranche token.

### Proof of Concept
```solidity
// Foundry fork test, based on test/foundry/IdleCDOEpochQueue.t.sol helpers
function testDustDepositFreezesEpochDeposits() external {
    _useStandardEpochVariant();
    _stopCurrentEpoch(); // enter epoch #1 buffer context as in existing tests

    address attacker = makeAddr('attacker');
    address honest = makeAddr('honest');
    // both wallets KYC-allowed per _checkAllowed mock setup

    // attacker queues a dust deposit (1 wei underlying -> 0 tranche minted at price ~1e18)
    _requestDepositWithUser(attacker, 1);
    // honest user queues a real deposit in the same epoch
    _requestDepositWithUser(honest, 10_000 * 1e6);

    // buffer period: manager processes deposits -> division by zero
    vm.expectRevert(); // panic: division by zero at epochPrice assignment
    vm.prank(manager);
    queue.processDeposits();

    // epochPendingDeposits stays nonzero, epochPrice stays 0:
    // honest user's claim is permanently bricked
    vm.expectRevert(abi.encodeWithSelector(NotAllowed.selector));
    vm.prank(honest);
    queue.claimDepositRequest(strategy.epochNumber());
    // honest underlying remains locked in the queue contract
}
```