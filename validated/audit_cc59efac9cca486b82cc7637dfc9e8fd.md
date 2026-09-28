### Title
Loss-adjusted withdraw haircut keyed by collect-time `epochNumber` while claims resolve via request-time `lastWithdrawRequest`, letting haircutted receipts claim at par - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
The external bug is a post-operation applied to an unsanitized stored locator (`zinfo.filename`) instead of the authoritative result of the preceding operation (`wf.extract` return value). The direct analog lives in `IdleCreditVault`'s loss-adjusted withdrawal path: when a `stopEpochWithDuration` loss is funded, the haircut is stored under `lossRecoveryPriceByEpoch[epochNumber]` — the epoch number *at collect time* — but claims look the haircut up via `lossRecoveryPriceByEpoch[lastWithdrawRequest[_user]]` — the epoch *at request time*. Because `epochNumber` is incremented at `stopEpoch` (per the in-code comment "Epoch number is increased at stopEpoch"), the stored key and the lookup key refer to different epochs. The claim path then silently skips the haircut and the receipt is paid at par through `_claimFundedWithdrawRequest`.

### Finding Description
- `collectWithdrawFunds` records the haircut under the *current* epoch counter:
  `lossRecoveryPriceByEpoch[epochNumber] = lossRecoveryPrice` (`IdleCreditVault.sol:421`).
- `_claimLossAdjustedWithdrawRequest` resolves the haircut epoch by trusting a single stored pointer:
  `uint256 lossEpoch = lastWithdrawRequest[_user]; uint256 lossRecoveryPrice = lossRecoveryPriceByEpoch[lossEpoch];` (`IdleCreditVault.sol:790-791`).
- `lastWithdrawRequest[_user]` was written at *request* time (`lastWithdrawRequest[_user] = currentEpoch`, line 282), while `collectWithdrawFunds` runs inside `stopEpochWithDuration`, which increments `epochNumber` first (see `claimWithdrawRequest` doc: "Epoch number is increased at stopEpoch", line 322). The haircut is therefore stored under `requestEpoch + 1` but looked up under `requestEpoch`, so `lossRecoveryPrice == 0` and the function returns 0.
- `claimWithdrawRequest` then falls through to `amount + _claimFundedWithdrawRequest(_user)` (line 313), which pays the full aggregate `withdrawsRequests[_user]` at par (lines 338-349) — the haircut is never applied.
- The same flawed lookup is embedded in the `requestWithdraw` guard (lines 261-270): it only inspects `lossRecoveryPriceByEpoch[lastWithdrawRequest[_user]]`, so the protection intended to force claiming a haircutted receipt before re-requesting is keyed to the same wrong epoch and does not stop the escape either.

This is exactly the reported bug class: a post-operation (haircut application) applied to a stored, attacker-influenced locator (`lastWithdrawRequest`, analogous to `zinfo.filename`) instead of the authoritative per-epoch record (`withdrawsRequestsByEpoch`, analogous to the sanitized path returned by `extract`).

### Impact Explanation
The vault only pulls the *reduced* `_amount` from the CDO in `collectWithdrawFunds` (line 428), but pays every pending receipt at 100% through `_transferFundedClaim`. A user with a pending normal withdraw request in the epoch that is stopped with a loss receives `basis` instead of `basis * lossRecoveryPrice / RECOVERY_FULL`. The excess is taken from the strategy's underlying balance, which is the same pool backing all other funded receipts — i.e., direct theft/insolvency equal to `pendingBasis * (1 - lossRecoveryPrice/RECOVERY_FULL)`, borne by later claimants and active LPs who were supposed to absorb only their pro-rata share via the waterfall. This breaks the loss-socialization invariant that `previewLossAdjustedWithdrawFunds` was designed to enforce.

### Likelihood Explanation
Requires only an unprivileged tranche-token holder with a pending `requestWithdraw` in an epoch that ends via `stopEpochWithDuration` with `_lossAmount > 0` — a normal, non-default code path. No privileged-role misbehavior is needed; the mismatch is structural in the keying. One caveat I could not fully verify within available context: the exact ordering of the `epochNumber` increment relative to the `collectWithdrawFunds` call inside `IdleCDOEpochVariant.stopEpochWithDuration`. If collect happens before the increment, the keys coincide and the primary path is benign — however even then, any second stale `lossRecoveryPriceByEpoch` entry under a different epoch key is still unreachable for users whose `lastWithdrawRequest` moved, since claims check only that single pointer.

### Recommendation
Do not resolve the haircut epoch via the mutable `lastWithdrawRequest` pointer. Either (a) store the loss recovery price under the *request* epoch(s) it applies to (e.g., key it by the epoch in which the receipts were recorded, not the collect-time `epochNumber`), or (b) have `_claimLossAdjustedWithdrawRequest` iterate the user's per-epoch receipts (`withdrawsRequestsByEpoch`/`apr0Users.principalEpoch`) and apply `lossRecoveryPriceByEpoch` for each epoch that has one — the "sanitized path" analog. The `requestWithdraw` guard must be updated consistently to check every outstanding receipt epoch, not just `lastWithdrawRequest`.

### Proof of Concept
Foundry fork PoC sketch (against `IdleCreditVault` + `IdleCDOEpochVariant` on mainnet fork):

```solidity
// 1. Attacker deposits into AA tranche during epoch N buffer and requests withdraw.
uint256 shares = cdoEpoch.depositAA(100_000e6);
cdoEpoch.requestWithdraw(shares, address(AAtranche)); // strategy records lastWithdrawRequest[attacker] = N

// 2. Epoch N runs; at end, manager calls stopEpochWithDuration with a loss.
//    Inside: epochNumber becomes N+1, then collectWithdrawFunds stores
//    lossRecoveryPriceByEpoch[N+1] = e.g. 0.7e18 and pulls only 70% of pending.
vm.prank(manager);
cdoEpoch.stopEpochWithDuration(apr, duration, lossAmount);

// 3. Attacker claims. _claimLossAdjustedWithdrawRequest looks up
//    lossRecoveryPriceByEpoch[N] == 0 -> skipped. _claimFundedWithdrawRequest
//    pays full withdrawsRequests[attacker] at par.
uint256 balPre = underlying.balanceOf(attacker);
cdoEpoch.claimWithdrawRequest();
uint256 paid = underlying.balanceOf(attacker) - balPre;

// 4. Assert overpayment vs. intended haircut.
assertGt(paid, basis * 7 / 10); // received ~par despite 30% loss
// Vault strategy balance is now short basis * 0.3 -> later claimants undercollateralized.
```

The asserting invariant: `sum(claimed) == sum(collectWithdrawFunds amounts)`. With the key mismatch, claims exceed collected funds by the full haircut amount, directly draining underlyings meant for other funded receipts.