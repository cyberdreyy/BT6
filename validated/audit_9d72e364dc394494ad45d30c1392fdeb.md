### Title
Dust withdrawal request permanently bricks `processWithdrawRequests` via `Is0()` revert, freezing all queued epoch withdrawals - (File: contracts/IdleCDOEpochQueue.sol)

### Summary
`IdleCDOEpochQueue.processWithdrawRequests()` reverts with `Is0()` whenever the aggregate `_underlyingsRequested` returned by the CDO rounds down such that `_epochPrice = _underlyingsRequested * ONE_TRANCHE / _pending == 0`. An unprivileged, KYC-passing user can enqueue a dust-sized tranche withdrawal (a few wei of tranche tokens, which convert to 0 underlying units since the underlying has 6 decimals and tranches have 18) into `epochPendingWithdrawals`. From that point the manager/owner call to `processWithdrawRequests` always reverts, so no withdraw request for that epoch can ever be priced or claimed — all other users' queued withdrawals in the same epoch are frozen indefinitely.

### Finding Description
The queue's `requestWithdraw` path accepts any nonzero tranche amount and records it into `epochPendingWithdrawals[_epoch]` and `userWithdrawalsEpochs` (`contracts/IdleCDOEpochQueue.sol`, tested at `test/foundry/IdleCDOEpochQueue.t.sol:643-660`). There is no minimum-amount check anywhere on this path.

During the buffer period the manager calls `processWithdrawRequests` (`contracts/IdleCDOEpochQueue.sol:298-329`), which does:

```solidity
uint256 _underlyingsRequested = _cdo.requestWithdraw(_pending, tranche);
uint256 _epochPrice = _underlyingsRequested * ONE_TRANCHE / _pending;
if (_epochPrice == 0) {
  revert Is0();
}
```

`_trancheToUnderlyings` in `IdleCDOEpochVariant` computes `amount * tranchePrice / ONE_TRANCHE`; with USDC (6-dec) underlying and a tranche price of ~`1e6`, any request below ~`1e12` tranche wei yields `_underlyingsRequested == 0`. `_pending` is still nonzero, so the division produces `0` and the whole function reverts.

The defense-in-depth for the symmetric case already exists for deposits: `processPrefundedDeposits` explicitly handles `_prefundedMinted == 0` by falling back to `virtualPrice` precisely "so as not to block epoch finalization" (`contracts/IdleCDOEpochQueue.sol:268-270`). The withdraw path has the opposite policy — it hard-reverts, converting a dust request into a queue-wide blocker.

There is no clean escape hatch:
- `deleteWithdrawRequest` lets the *attacker* remove their own request, but they can re-add it each epoch at ~zero cost, and there is no privileged force-remove for other users' dust.
- `pendingClaims`/epoch gating only moves forward via `processWithdrawRequests`, so the freeze repeats every epoch the dust request exists.

### Impact Explanation
Temporary freezing of user funds: every legitimate withdraw request batched into the same `epochPendingWithdrawals[_epoch]` is unclaimable for as long as the attacker keeps a dust request active (re-requesting after any deletion). Since `epochPendingWithdrawals` is additive across users, one dust request poisons the entire epoch batch. Cost to the attacker is gas plus a few wei of tranche tokens (KYC-gated at most). Loss magnitude is bounded by the total queued withdrawal TVL for affected epochs; the freeze is temporary/renewable rather than permanent theft, matching the "temporary freezing" acceptance criterion.

### Likelihood Explanation
- Attacker profile is fully in-scope: any KYC-passing lender with tranche tokens can call `queue.requestWithdraw`.
- Trigger requires only that the dust request is present when the manager calls `processWithdrawRequests` during the buffer period — a routine, required operation.
- The only caveat is that if the CDO's `requestWithdraw` internally reverts on a zero-underlying conversion before returning (rather than returning 0), the same net effect holds: the call still reverts and the batch is still blocked. Either way the dust request bricks the epoch's processing; the `Is0()` guard confirms the zero-price path was considered reachable but handled by reverting instead of skipping/flooring the request.

### Recommendation
Enforce a minimum withdrawal size and/or handle zero-price aggregates without reverting:
- Reject dust in `IdleCDOEpochQueue.requestWithdraw` (e.g., require `_trancheToUnderlyings(_amount) > 0`, or a configurable `minWithdrawAmount`).
- In `processWithdrawRequests`, handle `_underlyingsRequested == 0` like `processPrefundedDeposits` handles zero-mint: either set `isEpochWithdrawZero[_epoch] = true` and clear the pending amount (mirroring the `processWithdrawalClaims` zero-price path at `IdleCDOEpochQueue.sol:355-362`), or refund the dust tranche amount instead of reverting.

### Proof of Concept
Foundry fork PoC outline (extends `test/foundry/IdleCDOEpochQueue.t.sol` setup):

```solidity
function testPocDustWithdrawBlocksEpoch() external {
    _stopCurrentEpoch();                       // enter epoch 1 buffer

    address victim = makeAddr('victim');
    uint256 victimTranches = _depositWithUser(victim, 100e6);
    address attacker = makeAddr('attacker');
    _depositWithUser(attacker, 1e6);           // dust deposit, enough for >1e12 wei of tranches

    vm.prank(manager);
    cdoEpoch.startEpoch();

    // victim files a normal withdraw request
    _requestWithdrawWithUser(victim, victimTranches);

    // attacker files a dust request: underlyings = dust * price / 1e18 == 0
    uint256 dust = 1;                          // 1 wei of tranche token
    vm.prank(attacker);
    queue.requestWithdraw(dust);

    _stopCurrentEpoch();                       // enter buffer of epoch 2

    vm.expectRevert(Is0.selector);
    vm.prank(manager);
    queue.processWithdrawRequests();           // whole batch reverts

    // victim's funds stay frozen: no epochWithdrawPrice set for the epoch
    vm.expectRevert(abi.encodeWithSelector(NotAllowed.selector));
    vm.prank(victim);
    queue.claimWithdrawRequest(strategy.epochNumber());
}
```

Expected result: `processWithdrawRequests` reverts on `Is0()` (or earlier inside `IdleCreditVault.requestWithdraw`), `epochWithdrawPrice` for the epoch is never set, and the victim's ~100 USDC withdrawal cannot be processed while the attacker's dust request exists. Attacker cost: gas + 1 wei of tranche tokens.