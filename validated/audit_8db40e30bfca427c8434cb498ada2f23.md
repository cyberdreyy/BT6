### Title
Post-default withdraw receipts are paid at par from the shared `defaultRecoveryReserve`, double-spending backing already priced at `defaultRecoveryPrice` — (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
The external bug is a double-free: two owners treat the same memory as theirs and both release it. The analog in `IdleCreditVault` is that after `finalizeDefaultRecovery` crystallizes a `defaultRecoveryPrice < RECOVERY_FULL`, a user calling `requestWithdraw` post-default gets a 1:1-funded `postDefaultRequests` receipt that is paid out of `defaultRecoveryReserve` at par, while the strategy-token basis that was burned to create it was only worth `recoveryPrice` of that reserve. The same reserve therefore backs two claimants: the post-default requester and the defaulted-epoch/active claimants it was sized for — a double spend of the recovery pool.

### Finding Description
`finalizeDefaultRecovery` computes `reserveAmount = _recoveredAmount + prefundedReserve + defaultRecoveryReserve` and sets `defaultRecoveryPrice = reserveAmount * RECOVERY_FULL / totalBasis`, where `totalBasis` includes `activeBasis` (the CDO's strategy-token balance plus default-epoch interest) and `pendingBasis` (pending receipts) (`IdleCreditVault.sol:679-699`). The reserve is denominated to satisfy *all* claims at `recoveryPrice`, i.e., each unit of strategy-token basis is entitled to `recoveryPrice` units of reserve.

After finalization, `requestWithdraw` routes to the post-default branch (`IdleCreditVault.sol:247-257`):

```solidity
_burn(msg.sender, _amount);        // burns CDO's strategy tokens
_mint(_user, _amount);             // mints receipt 1:1
postDefaultRequests[_user] = _amount;
```

`_claimPostDefaultWithdrawRequest` then pays the full `_amount` via `_transferDefaultRecovery`, which does `defaultRecoveryReserve -= _amount` and transfers underlying (`IdleCreditVault.sol:760-767`, `912-917`).

The mismatch: the user burned tranche tokens whose underlying value was already haircut — `_amount` is the post-haircut figure — and the CDO burned only `_amount` strategy tokens. But the reserve share attributable to `_amount` units of basis at price `recoveryPrice` is only `_amount * recoveryPrice / RECOVERY_FULL`. Paying `_amount` at par spends `_amount` of reserve while releasing only `_amount * recoveryPrice / RECOVERY_FULL` worth of backing. The excess `_amount * (1 - recoveryPrice/RECOVERY_FULL)` is taken from reserve earmarked for defaulted-epoch receipt holders (`_claimDefaultedWithdrawRequest`, `_claimDefaultedInstantWithdrawRequest`) and any other claimants drawing on the same `defaultRecoveryReserve`. When recovery is below par, the last claimants' `_transferDefaultRecovery` either underflows on `defaultRecoveryReserve -= _amount` or the strategy's underlying balance runs dry — the classic double-free outcome: the pool is released twice.

### Impact Explanation
Direct theft / insolvency of recovery funds. With `defaultRecoveryPrice = 50%`, a post-default request for `X` withdraws `X` underlying but frees only `0.5X` of backing; the remaining `0.5X` is stolen from other recovery claimants. Since the CDO passes an already-haircut `_amount`, an attacker can repeatedly deposit cheap tranche tokens (priced at `recoveryPrice`), call `requestWithdraw`, then `claimWithdrawRequest`, draining the reserve until it is exhausted. Loss is bounded by `defaultRecoveryReserve` and quantified as up to `reserve * (RECOVERY_FULL - defaultRecoveryPrice) / RECOVERY_FULL` extracted beyond entitlement, leaving defaulted receipt holders permanently underpaid.

### Likelihood Explanation
Requires only that the pool has defaulted and recovery was finalized below par — a normal lifecycle state, not an edge case. The attacker needs to be a KYC-passed lender holding post-default tranche tokens (mintable via `depositAA`/`depositBB` after finalization, when tranche price reflects the haircut). The guard in the post-default branch (`_hasWithdrawRequest`, `instantWithdrawsRequests`, `postDefaultRequests` all zero) only forces sequential requests and does not protect the reserve. `_transferFundedClaim`'s reserve guard explicitly excludes `defaultRecoveryReserve`, but `_transferDefaultRecovery` has no equivalent check that the claim is backed by released basis. No privileged-role misbehavior needed; owner/manager calls (`finalizeDefault`, epoch ops) are just environmental sequencing.

### Recommendation
In `_claimPostDefaultWithdrawRequest`, do not pay from `defaultRecoveryReserve` at par. Either:
- pay post-default claims via `_transferFundedClaim` sourced from the underlying released by the burn (i.e., deposit the haircut proceeds into the strategy at request time rather than spending the reserve), or
- debit the reserve by only the basis-share released (`_amount * defaultRecoveryPrice / RECOVERY_FULL` equivalent accounting), i.e., burn `_amount / defaultRecoveryPrice`-scaled strategy tokens so reserve outflow equals backing released.

More robustly, track `defaultRecoveryReserve` as `Σ claim payouts ≤ Σ (claimBasis * price)` and revert if a post-default claim would exceed the backing contributed by its burn.

### Proof of Concept
Foundry fork PoC sketch (following `testProcessPostDefaultWithdrawalAsNormalClaim` patterns in `test/foundry/IdleCDOEpochQueue.t.sol`):

```solidity
// 1. Deposit AA, run epoch 0, stop, borrower defaults (stopEpoch(0,0)).
// 2. manager.finalizeDefault(recovered, manager) with recovered < totalBasis
//    => defaultRecoveryPrice < RECOVERY_FULL, defaultRecoveryReserve set.
// 3. Attacker: depositAA(X * recoveryPrice worth) -> receives tranche tokens
//    priced at haircut; cdoEpoch.requestWithdraw(trancheBal, AA) ->
//    strategy mints postDefaultRequests[attacker] = haircut amount.
// 4. cdoEpoch.claimWithdrawRequest() ->
//    _claimPostDefaultWithdrawRequest pays attacker X underlying 1:1
//    from defaultRecoveryReserve.
// 5. Assert attacker received X while only X*price backing was released;
//    assert remaining reserve < sum of remaining defaulted claims * price,
//    i.e. a second defaulted claimant's claimWithdrawRequest() reverts on
//    underflow / pays less than claimBasis * defaultRecoveryPrice.
```

The invariant broken is "one receipt, one payout against its own backing": the recovery reserve is doubly owned by the post-default receipt and by the defaulted-epoch claims it was provisioned for.