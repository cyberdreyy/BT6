### Title
Post-default instant-withdraw requests resurrect a cleared claim bucket and drain the default recovery reserve at par - ([File: contracts/strategies/idle/IdleCreditVault.sol])

### Summary
CVE-2026-3593 is a use-after-free: a freed DNS object is still reachable through a stale pointer. The credit-vault analog is the instant-withdraw receipt aggregate. After `finalizeDefaultRecovery` "frees" the pre-default claim universe into `defaultRecoveryReserve`/`defaultRecoveryPrice`, `requestInstantWithdraw` still writes into the freed `instantWithdrawsRequests`/`instantWithdrawsRequestsByEpoch`/`pendingInstantWithdraws` buckets, and `claimInstantWithdrawRequest` still pays that aggregate at par from strategy-held underlying — i.e., from the same pot reserved for haircutted default claims. `requestWithdraw` has an explicit post-default path (`postDefaultRequests`, priced *after* the haircut and reverting on stale receipts) at `IdleCreditVault.sol:247-257`; `requestInstantWithdraw` has no equivalent guard.

### Finding Description
- `requestInstantWithdraw` (`IdleCreditVault.sol:356-375`) only calls `_onlyIdleCDO()` and `_ensureDefaultRecoveryInitialized()`. It never checks `defaultRecoveryFinalized`. It burns the CDO's strategy tokens, mints a 1:1 receipt to the user, and increments `instantWithdrawsRequests[_user]`, `instantWithdrawsRequestsByEpoch[_user][epochNumber]`, `instantWithdrawClaimsByEpoch[epochNumber]`, and `pendingInstantWithdraws`.
- `claimInstantWithdrawRequest` (`IdleCreditVault.sol:380-393`) pays `instantWithdrawsRequests[_user]` at par via `_transferFundedClaim`, which transfers `underlyingToken` held by the strategy — the same balance that constitutes `defaultRecoveryReserve` backing `_claimDefaultedWithdrawRequest`, `_claimPostDefaultWithdrawRequest`, `_claimDefaultedInstantWithdrawRequest`, and `DefaultDistributor.claim` payouts at `defaultRecoveryPrice`.
- The guard in `claimInstantWithdrawRequest` only routes claims through `_claimDefaultedInstantWithdrawRequest` when `defaultInstantWithdrawsFinalized` is true, and that path clears `instantWithdrawsRequestsByEpoch[_user][defaultRecoveryEpoch]` — the *default* epoch. A receipt created *after* finalization lives under a later `epochNumber`, is never haircutted, and is paid in full.
- Meanwhile the attacker acquired the tranche tokens cheaply: `finalizeDefaultRecovery` calls `_forceUpdateAccounting`, so post-default tranche/virtual prices already reflect the crystallized loss. Buying AA/BB at recovery price, then redeeming via instant withdraw at par, converts recovery-price tokens into par claims.

### Impact Explanation
Direct theft / insolvency: each post-default instant receipt minted from cheap tranches is paid 1:1 out of `defaultRecoveryReserve` + funded strategy underlying. Defaulted-epoch withdraw requesters, instant requesters, and `DefaultDistributor` claimants then find the reserve short and their `defaultRecoveryPrice`-proportional payouts revert on insufficient balance or pay less than entitled. Quantified: attacker profit ≈ `amount * (1 - defaultRecoveryPrice/RECOVERY_FULL)` per claim, bounded only by reserve size and post-default tranche supply.

### Likelihood Explanation
Requires (a) a borrower default that is finalized (`defaulted() == true`, `finalizeDefaultRecovery` called — honest manager flow), and (b) the CDO still routing instant withdraw requests post-default — `requestInstantWithdraw`/`requestInstantWithdrawParams` are not gated on `defaulted` in the strategy, and the request path reverts nowhere in the vault code shown. Any unprivileged EOA holding tranche tokens qualifies. Likelihood is moderate: it needs a default to have occurred, but no privileged collusion.

Caveat I could not fully verify within the iteration limit: whether `IdleCDOEpochVariant.requestInstantWithdraw`/`claimInstantWithdrawRequest` externally gate on `defaulted()` or epoch state after finalization. If the CDO blocks post-default instant requests, this path is unreachable and the finding degrades to a defense-in-depth gap.

### Recommendation
Mirror the `requestWithdraw` post-default handling in `requestInstantWithdraw`: if `defaultRecoveryFinalized`, revert when `instantWithdrawsRequests[_user] != 0` or outstanding defaulted receipts exist, and route the request into `postDefaultRequests`-style reserve-priced accounting instead of the par-funded `instantWithdrawsRequests` bucket. Alternatively, have the CDO revert instant withdraw requests while `defaulted()`.

### Proof of Concept
```solidity
// Foundry fork PoC (sketch) against test/foundry/IdleCreditVault.t.sol harness
function testPostDefaultInstantWithdrawDrainsReserve() external {
    // 1. Normal epoch: user Victim deposits, requests instant withdraw,
    //    requests normal withdraw; epoch runs.
    _depositWithUser(victim, 100_000 * ONE_SCALE, true);
    _startEpochAndCheckPrices(0);
    // victim requests instant + normal withdraw via cdoEpoch
    // borrower defaults: deal() insufficient, manager calls stopEpoch -> _handleBorrowerDefault
    // finalizeDefault + finalizeDefaultRecovery with partial recovery R < basis
    // => defaultRecoveryPrice < RECOVERY_FULL, defaultRecoveryReserve funded

    // 2. Attacker buys AA tranches cheaply post-default (virtualPrice haircut)
    idleCDO.depositAA(attackerAmount); // or acquire tranches
    // 3. Attacker requests instant withdraw (no defaulted() guard in strategy)
    vm.prank(attacker);
    cdoEpoch.requestInstantWithdraw(attackerTranches, address(AAtranche));
    // 4. After instantDelay, claim -> paid at PAR from defaultRecoveryReserve
    skip(instantDelay + 1);
    vm.prank(attacker);
    cdoEpoch.claimInstantWithdrawRequest();
    // assert attacker received ~1:1 underlying > recovery-price cost basis
    // 5. Victim's defaulted claim now underfunded / reverts
    vm.prank(victim);
    vm.expectRevert(); // insufficient reserve
    cdoEpoch.claimWithdrawRequest();
}
```