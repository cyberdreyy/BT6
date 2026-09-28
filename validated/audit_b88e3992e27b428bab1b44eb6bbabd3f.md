### Title
Loss-adjusted withdraw claims only haircut the latest request epoch — earlier same-user receipts are paid at par, draining the vault - ([File: contracts/strategies/idle/IdleCreditVault.sol])

### Summary
The external bug is a heap over-read: an index (`voutf`'s buffer) is trusted beyond its real bounds. The on-chain analog is `lastWithdrawRequest[_user]`, a single-epoch "pointer" used to index `lossRecoveryPriceByEpoch` and per-epoch receipt ledgers, while the haircut funding in `collectWithdrawFunds` is computed over the *aggregate* `pendingWithdraws` spanning all epochs. When a user holds pending withdraw receipts in multiple epochs and a `stopEpochWithDuration` loss is applied, `_claimLossAdjustedWithdrawRequest` applies the haircut only to the receipt stored at `lastWithdrawRequest`, and `_claimFundedWithdrawRequest` then pays all remaining receipts at par — even though the borrower only funded `pendingBasis * lossRecoveryPrice`. The invariant "one receipt, one haircut-adjusted payout" breaks, causing strategy insolvency.

### Finding Description
- `requestWithdraw` stacks receipts per epoch: `withdrawsRequestsByEpoch[_user][currentEpoch] += _amount` and overwrites `lastWithdrawRequest[_user] = currentEpoch` (`IdleCreditVault.sol:282-293`). The only guard (lines 261-271) blocks a new request if the *previous* `lastWithdrawRequest` epoch already has a `lossRecoveryPriceByEpoch` entry — it does not prevent accumulating receipts across several epochs before any loss occurs.
- On a lossy stop, `collectWithdrawFunds(_amount < pendingBasis)` stores `lossRecoveryPriceByEpoch[epochNumber] = _amount * 1e18 / pendingBasis` where `pendingBasis = pendingWithdraws` covers *all* epochs' receipts (lines 411-421). The vault is therefore funded with only `aggregate × recoveryPrice`.
- On claim, `claimWithdrawRequest` calls `_claimLossAdjustedWithdrawRequest`, which reads `lossEpoch = lastWithdrawRequest[_user]` and clears only `withdrawsRequestsByEpoch[_user][lossEpoch]` via `_clearWithdrawClaimForEpoch`, paying `claimBasis_epochN × recoveryPrice` and resetting `lastWithdrawRequest` to 0 (lines 789-800, 811-836).
- Execution then falls into `_claimFundedWithdrawRequest` (lines 301-349): `epochNumber <= lastWithdrawRequest` check passes (it's now 0), `_settleApr0` runs, and the *entire remaining* `withdrawsRequests[_user]` — including the older-epoch receipt that was already haircut in the funding computation — is paid 1:1 at par via `_transferFundedClaim`.

Net payout = `basisNew × price + basisOld × 1.0`, while the vault only received `(basisNew + basisOld) × price`. The excess is stolen from recovery reserve / other users' funded claims, and once the strategy balance is drained, later claimants' transactions revert — permanent freezing of their unclaimed funds.

### Impact Explanation
Direct insolvency and theft: an attacker (any KYC-allowed tranche holder) requests withdraws in two consecutive buffer phases, waits for one `stopEpochWithDuration` loss event (an honest manager/borrower action), then claims. With equal receipts of size `B` in epochs N-1 and N and recovery price `p`, the attacker receives `B·p + B` while the vault holds `2B·p` — extracting `B·(1−p)` more than funded, at the expense of every other pending claimant. The broken invariant is fair burn/payout: aggregate haircut basis vs. per-epoch claim indexing.

### Likelihood Explanation
Requires only unprivileged actions (deposit → requestWithdraw twice across epochs → claimWithdrawRequest) plus an honest lossy `stopEpochWithDuration`, which is a normal operating mode explicitly built into the vault. No malicious privileged role, default, or oracle manipulation is needed. Loss events are expected in a credit vault, so trigger conditions are realistic.

### Recommendation
Track all epochs contributing to a user's pending basis, or key the loss haircut to the aggregate: e.g. store per-user list/bitmap of request epochs, or on `collectWithdrawFunds` record the haircut epoch and, in `_claimLossAdjustedWithdrawRequest`, apply `lossRecoveryPrice` to the *entire* `withdrawsRequests[_user] + apr0` basis that was pending at that epoch rather than only `withdrawsRequestsByEpoch[_user][lastWithdrawRequest]`. Alternatively, reject `requestWithdraw` whenever the user already has any unfunded pending receipt, mirroring the post-default guard at lines 247-251.

### Proof of Concept
Foundry fork PoC (matches `test/foundry/IdleCreditVault.t.sol` harness conventions):

```solidity
function testLossHaircutOnlyAppliesToLatestEpoch() external {
    // pool running, user deposits and gets AA tranches
    uint256 amount = 10000 * ONE_SCALE;
    uint256 mintedAA = idleCDO.depositAA(amount);

    // Epoch 1 buffer: request withdraw of half
    uint256 req1 = cdoEpoch.requestWithdraw(mintedAA / 2, address(AAtranche));
    manager.startEpoch(); manager.stopEpoch(0, interest);   // epochNumber++

    // Epoch 2 buffer: request withdraw of the rest — allowed, guard passes
    // because lossRecoveryPriceByEpoch[1] == 0 at this point
    uint256 req2 = cdoEpoch.requestWithdraw(mintedAA / 2, address(AAtranche));
    manager.startEpoch();

    // Honest lossy stop: borrower only funds pendingWithdraws * p (e.g. p = 0.5)
    uint256 loss = pendingWithdraws / 2;
    manager.stopEpochWithDuration(loss, ...);   // sets lossRecoveryPriceByEpoch[2] = 0.5e18

    // Attacker claim: epoch-2 receipt paid at 0.5x, epoch-1 receipt paid at PAR
    uint256 balPre = underlying.balanceOf(attacker);
    cdoEpoch.claimWithdrawRequest();
    uint256 got = underlying.balanceOf(attacker) - balPre;

    // funded = (req1+req2) * 0.5 ; got = req2*0.5 + req1  > funded
    assertGt(got, (req1 + req2) / 2);
    // vault insolvent: subsequent users' claims revert on ERC20 transfer
    vm.expectRevert();
    victimCdoEpoch.claimWithdrawRequest();
}
```

Relevant code: `requestWithdraw` guard and per-epoch accounting `contracts/strategies/idle/IdleCreditVault.sol:259-294`; aggregate haircut `collectWithdrawFunds` `:411-430`; single-epoch claim `_claimLossAdjustedWithdrawRequest` `:789-800` and `_clearWithdrawClaimForEpoch` `:811-836`; par payout `_claimFundedWithdrawRequest` `:319-350`.