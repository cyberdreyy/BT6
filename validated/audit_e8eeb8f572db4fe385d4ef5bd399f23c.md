### Title
Stale `lastWithdrawRequest` lets a user bypass the per-epoch loss haircut and claim a defaulted/loss-adjusted receipt at par - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
Directory traversal is about escaping an intended boundary to reach resources outside it. The analog here is a withdrawal-request boundary escape: `IdleCreditVault` prices a loss-adjusted receipt per request epoch (`lossRecoveryPriceByEpoch[epoch]`), but the claim path resolves the epoch through a single mutable pointer, `lastWithdrawRequest[_user]`. By making a second withdraw request, an attacker moves that pointer past the loss epoch, causing `_claimLossAdjustedWithdrawRequest` to look up the wrong epoch (price 0 → skipped) while the aggregate `withdrawsRequests[_user]` still contains the haircutted amount. `_claimFundedWithdrawRequest` then pays the whole aggregate at par, stealing the haircut difference from other claimants' funded reserve.

### Finding Description
On `stopEpochWithDuration` losses, `collectWithdrawFunds` funds only `pendingBasis * lossRecoveryPrice / RECOVERY_FULL` and records `lossRecoveryPriceByEpoch[epochNumber]` (`IdleCreditVault.sol:411-430`). Users are meant to claim that epoch's receipt through `_claimLossAdjustedWithdrawRequest`, which pays `claimBasis * lossRecoveryPrice` (`IdleCreditVault.sol:789-801`).

The bug: `_claimLossAdjustedWithdrawRequest` derives the epoch from `lastWithdrawRequest[_user]` (`IdleCreditVault.sol:790`), but `requestWithdraw` overwrites `lastWithdrawRequest[_user] = currentEpoch` on every new request (`IdleCreditVault.sol:282`). Meanwhile `withdrawsRequests[_user]` is a flat aggregate incremented per request (`IdleCreditVault.sol:292`) and only decremented when `_clearWithdrawClaimForEpoch` actually runs for that epoch (`IdleCreditVault.sol:815-820`).

Sequence:
1. Epoch N running, attacker calls `requestWithdraw` (amount A) → `withdrawsRequestsByEpoch[N] += A`, `withdrawsRequests += A`, `lastWithdrawRequest = N`, receipt tokens minted 1:1.
2. `stopEpochWithDuration(_lossAmount)` underfunds receipts; `collectWithdrawFunds` stores `lossRecoveryPriceByEpoch[N] = p < RECOVERY_FULL` and transfers only `ΣclaimBasis * p` into the vault.
3. Epoch N+1 buffer: attacker calls `requestWithdraw` again (amount B) → `lastWithdrawRequest = N+1`, `withdrawsRequests += B`, more receipt tokens minted.
4. After epoch N+1 ends, attacker calls `IdleCDOEpochVariant.claimWithdrawRequest()` → `IdleCreditVault.claimWithdrawRequest(attacker)` (`IdleCreditVault.sol:301-314`):
   - `_claimLossAdjustedWithdrawRequest`: `lossRecoveryPriceByEpoch[N+1] == 0` → returns 0, epoch-N entry untouched.
   - `_claimFundedWithdrawRequest`: epoch check passes (`epochNumber > lastWithdrawRequest = N+1`), and `amount = withdrawsRequests[user] = A + B` is paid at par via `_transferFundedClaim` (`IdleCreditVault.sol:338-349`).

The attacker receives `A` instead of `A * p`. The vault only ever received `A * p` for that receipt, so the excess `(A * (1 - p))` is paid out of underlying funded for other users' claims.

### Impact Explanation
Direct theft / insolvency: the attacker escapes the per-epoch loss haircut and drains `A * (RECOVERY_FULL - p) / RECOVERY_FULL` of underlying that was reserved for other requesters' funded claims. Subsequent claimants' `claimWithdrawRequest`/`claimInstantWithdrawRequest` underflow or their payouts are short, so the last withdrawers are permanently unable to claim (broken "one receipt one haircutted payout" invariant, and solvency of the funded-claim reserve). Loss magnitude scales with the attacker's epoch-N request size and the severity of the realized loss; with a large loss (`p` near 0) the attacker recovers nearly the full pre-haircut amount.

### Likelihood Explanation
Fully attacker-driven and permissionless: requires only being a KYC-passing lender with tranche tokens in an epoch where `stopEpochWithDuration` realizes a loss (a normal, honest-manager code path, not a default), then one extra `requestWithdraw` and a one-epoch wait. No privileged cooperation is needed; the sequence ordering is entirely under attacker control. The existing guards don't catch it: the epoch-wait check in `_claimFundedWithdrawRequest` (`IdleCreditVault.sol:326`) passes because the new request's epoch has also elapsed, and `_clearWithdrawClaimForEpoch` correctly scopes per-epoch data — the flaw is that the loss-adjusted path is keyed solely by the overwritten `lastWithdrawRequest` pointer, so older haircutted epochs are silently folded into the par-valued aggregate.

### Recommendation
- Track loss-adjusted claims per epoch explicitly instead of via `lastWithdrawRequest`: in `claimWithdrawRequest`, iterate or check `withdrawsRequestsByEpoch[_user]`/`apr0Users[_user].principalEpoch` against `lossRecoveryPriceByEpoch` for every epoch with a non-zero price, not only `lastWithdrawRequest[_user]`.
- Alternatively, prevent `lastWithdrawRequest[_user]` from being overwritten while `withdrawsRequestsByEpoch[_user][lastWithdrawRequest[_user]]` still has an unclaimed loss-adjusted balance, or store a per-user list/set of epochs with pending receipts.
- Add a regression test: request in epoch N → `stopEpochWithDuration` partial funding → request again in epoch N+1 → claim; assert payout equals `A * p + B`, not `A + B`.

### Proof of Concept
Foundry fork PoC sketch (modeled on `test/foundry/IdleCreditVault.t.sol` helpers `_depositWithUser`, `_startEpochAndCheckPrices`, `_stopEpochAndCheckPrices`):

```solidity
function testLossHaircutBypassViaSecondRequest() external {
    _setFeeParams(TL_MULTISIG, 0, FULL_ALLOC, cdoEpoch.managementFee());
    IdleCreditVault vault = IdleCreditVault(address(strategy));

    address attacker = makeAddr("attacker");
    uint256 amount = 10_000 * ONE_SCALE;
    _depositWithUser(attacker, amount, true);
    idleCDO.depositAA(10_000 * ONE_SCALE); // other LP liquidity to fund residual

    // Epoch 0 runs; attacker requests withdraw of half during buffer of epoch 1
    _startEpochAndCheckPrices(0);
    _stopEpochAndCheckPrices(0, initialProvidedApr, _expectedFundsEndEpoch());
    uint256 trancheBal = IERC20(AAtranche).balanceOf(attacker);
    vm.prank(attacker);
    uint256 reqA = cdoEpoch.requestWithdraw(trancheBal / 2, address(AAtranche));

    // Epoch 1 ends with a partial loss: collectWithdrawFunds stores
    // lossRecoveryPriceByEpoch[1] = p < RECOVERY_FULL and funds only p fraction.
    _startEpochAndCheckPrices(1);
    uint256 loss = vault.pendingWithdraws() / 4; // 25% loss on pending receipts
    _stopEpochAndCheckPrices(1, initialProvidedApr, _expectedFundsEndEpoch() - loss);
    uint256 p = vault.lossRecoveryPriceByEpoch(1);
    assertLt(p, 1e18);

    // Buffer of epoch 2: attacker files a second request, moving lastWithdrawRequest
    vm.prank(attacker);
    uint256 reqB = cdoEpoch.requestWithdraw(0, address(AAtranche));

    _startEpochAndCheckPrices(2);
    _stopEpochAndCheckPrices(2, initialProvidedApr, _expectedFundsEndEpoch());

    // Claim: loss-adjusted path resolves epoch via lastWithdrawRequest (=2, price 0),
    // funded path pays the full aggregate A + B at par.
    uint256 balPre = underlying.balanceOf(attacker);
    vm.prank(attacker);
    cdoEpoch.claimWithdrawRequest();
    uint256 paid = underlying.balanceOf(attacker) - balPre;

    // Expected honest payout: reqA * p / 1e18 + reqB
    assertGt(paid, reqA * p / 1e18 + reqB, "haircut bypassed: paid at par");
}
```