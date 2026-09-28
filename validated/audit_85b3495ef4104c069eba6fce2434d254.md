### Title
Zero epoch price sentinel lets prefunded depositors refund already-lent funds after a full tranche wipe - ([File: contracts/IdleCDOEpochQueue.sol])

### Summary
`IdleCDOEpochQueue` uses `epochPrice[_epoch] == 0` as the "epoch not yet processed" sentinel in `deleteRequest` and `claimDepositRequest`. But `processPrefundedDeposits` can legitimately store a zero price: when the minted tranche amount is 0 it falls back to `_cdo.virtualPrice(tranche)`, which returns 0 for a wiped tranche (e.g., AA after a borrower default that exceeds all NAV). In that state the epoch was processed — funds were prefunded to the borrower — yet `deleteRequest` treats it as unprocessed and refunds the depositor's `userDepositsEpochs` from whatever underlying the queue still holds.

### Finding Description
The zero-as-unset bug class from the report maps here directly: `0` means both "not processed" and "processed at price 0".

In `processPrefundedDeposits`, the epoch price is recorded with a fallback to `virtualPrice` when no shares were minted:

```solidity
// contracts/IdleCDOEpochQueue.sol:270
epochPrice[_epoch] = _prefundedMinted == 0 ? _cdo.virtualPrice(tranche) : _prefunded * ONE_TRANCHE / _prefundedMinted;
epochPrefundedDeposits[_epoch] = 0;
```

`virtualPrice` legitimately returns `0` for an economically wiped tranche — `_virtualPriceAux` explicitly preserves a saved zero price when `_lastNAV == 0 && _nav == 0` with nonzero supply (IdleCDOCreditVault.sol:298). After a borrower default where loss exceeds total NAV, `virtualPrice(AATranche) == 0`, so `epochPrice[_epoch] = 0`.

`deleteRequest` then sees the epoch as never processed:

```solidity
// contracts/IdleCDOEpochQueue.sol:187
_checkNotAllowed(epochPrice[_requestEpoch] != 0 || epochPrefundedDeposits[_requestEpoch] != 0);
uint256 amount = userDepositsEpochs[msg.sender][_requestEpoch];
...
IERC20Detailed(underlying).safeTransfer(msg.sender, amount);
```

`userDepositsEpochs[msg.sender][epoch]` is never cleared by `processPrefundedDeposits` (only the aggregate `epochPrefundedDeposits` is zeroed), and `epochPrefundedDeposits` is 0 after processing, so both guard terms are 0 and the refund executes — even though the underlying was already sent to the borrower by `processDepositsToBorrower` (line 179).

Note the asymmetry with the withdraw path: `processWithdrawalClaims` already stores an explicit `isEpochWithdrawZero` flag (lines 360–361) precisely because a processed epoch can yield price 0. The deposit path has no equivalent flag, so the sentinel ambiguity remains there.

### Impact Explanation
A depositor whose prefunded deposit epoch was settled at `epochPrice == 0` can call `deleteRequest(epoch)` and receive `userDepositsEpochs` underlying back from the queue, double-spending funds that were already lent to the borrower. The stolen amount is paid from any underlying sitting in the queue — later epochs' `epochPendingDeposits`, underlying received from `processWithdrawalClaims` awaiting user claims, or residual balances. Loss is up to the depositor's queued amount, capped by queue holdings; the invariant "one deposit, one payout" and queue solvency for other users' pending deposits/claims are broken. The attacker only needs to be a KYC-passing lender who deposited into the AA prefunded queue; the triggering default requires no malicious privileged action — an honest borrower default followed by honest manager `stopEpoch`/`finalizeDefault` calls is sufficient.

### Likelihood Explanation
Requires (a) a prefunded-queue CDO variant, (b) a queued+prefunded deposit epoch settled in the same `stopEpoch` where the tranche's `virtualPrice` is 0, and (c) residual underlying in the queue at delete time. Condition (b) needs a catastrophic default wiping the AA tranche — rare, but this is exactly the tail-risk scenario the prefunded flow is designed to settle through (`_prefundedEpochToProcess` explicitly handles `defaulted()`). The same "price can legitimately be 0" reasoning already motivated the `isEpochWithdrawZero` fix on the withdraw side, confirming the outcome is reachable. Because `claimDepositRequest` also reverts on `epochPrice == 0`, honest users of a zero-price epoch cannot claim either way, so the refund path is the only exit — making accidental or deliberate drainage of the queue likely once reached.

### Recommendation
Mirror the withdraw-side fix: add an explicit `isEpochDepositProcessed`/`isEpochDepositZero` flag (or store `epochPrice` sentinel as nonzero) set in `processDeposits`/`processPrefundedDeposits`, and gate `deleteRequest`/`claimDepositRequest` on that flag rather than on `epochPrice[_epoch] != 0`. For example:

```diff
+ mapping(uint256 => bool) public isEpochDepositProcessed;
  ...
  epochPrice[_epoch] = _prefundedMinted == 0 ? _cdo.virtualPrice(tranche) : _prefunded * ONE_TRANCHE / _prefundedMinted;
+ isEpochDepositProcessed[_epoch] = true;
  ...
- _checkNotAllowed(epochPrice[_requestEpoch] != 0 || epochPrefundedDeposits[_requestEpoch] != 0);
+ _checkNotAllowed(isEpochDepositProcessed[_requestEpoch] || epochPrefundedDeposits[_requestEpoch] != 0);
```

A zero-price epoch should still allow `claimDepositRequest` to clear `userDepositsEpochs` without payout (transferring 0 tranche tokens), same as `isEpochWithdrawZero` does for withdraws.

### Proof of Concept
Foundry fork PoC outline (extend `test/foundry/IdleCDOEpochQueue.t.sol` harness using the prefunded variant):

```solidity
function testDeleteRequestAfterZeroPricePrefundedEpoch() external {
    _usePrefundedEpochVariant(); // AA queue registered as epochQueue on prefunded CDO

    // Epoch 0 buffer: user1 queues a prefundable AA deposit
    address user1 = makeAddr('user1');
    deal(address(underlying), user1, 100e6);
    vm.startPrank(user1);
    underlying.approve(address(queue), 100e6);
    queue.requestDeposit(100e6); // queued for epoch 1
    vm.stopPrank();

    // Manager prefunds epoch-1 deposits to the borrower during epoch 0
    vm.warp(cdoEpoch.epochEndDate() - prefundedWindow); // inside cutoff
    vm.prank(manager);
    queue.processDepositsToBorrower(); // epochPrefundedDeposits[1] = 100e6, funds -> borrower

    _stopCurrentEpoch();
    vm.prank(manager);
    cdoEpoch.startEpoch(); // epoch 1 running, borrower holds the prefunded funds

    // Borrower defaults with loss exceeding total NAV -> AA virtualPrice becomes 0
    _forceBorrowerDefaultTotalLoss(); // defaulted() == true, virtualPrice(AATranche) == 0
    vm.prank(manager);
    cdoEpoch.stopEpoch(0, type(uint256).max); // stop path calls processPrefundedDeposits
    // -> epochPrice[1] = virtualPrice(AA) = 0; epochPrefundedDeposits[1] = 0

    assertEq(queue.epochPrice(1), 0, 'epoch settled at zero price');

    // Seed the queue with someone else's underlying (e.g., next-epoch pending deposits
    // or withdraw claim proceeds already pulled into the queue)
    deal(address(underlying), address(queue), 100e6);

    // user1's deposit was lent to the borrower, but the epoch looks unprocessed:
    // deleteRequest refunds the full queued amount -> double spend
    uint256 balPre = underlying.balanceOf(user1);
    vm.prank(user1);
    queue.deleteRequest(1);
    assertEq(underlying.balanceOf(user1) - balPre, 100e6, 'refunded already-lent deposit');
}
```

Key assertion: `queue.deleteRequest(1)` succeeds and pays out despite `epochPrice[1] == 0` resulting from a *processed* prefunded epoch, draining underlying that belongs to other queue users.