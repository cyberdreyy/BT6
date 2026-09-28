### Title
APR0 withdraw bucket permanently reverts `stopEpoch` once the vault APR is raised, freezing all vault funds - ([File: contracts/strategies/idle/IdleCreditVault.sol](contracts/strategies/idle/IdleCreditVault.sol))

### Summary
CVE-2017-9076 is a "mishandled inheritance" bug: a child socket fails to inherit state, causing a denial of service. The analog in IdleCreditVault is the APR0 withdrawal bucket (`apr0TotalPrincipal` / `apr0Users`): a receipt created while `unscaledApr == 0` is never migrated or cleared when the vault later moves to a nonzero APR. `prepareStopEpochWithApr0` reverts whenever a stale APR0 principal exists while `unscaledApr != 0`, and this call sits on the only path `stopEpoch`/`stopEpochWithDuration` can take — so any later epoch stop permanently reverts and all borrower/vault funds are frozen.

### Finding Description
When a user calls `requestWithdraw` while `unscaledApr == 0` (and the pool is not closed), the vault routes the request into the APR0 bucket instead of `withdrawsRequests`:

```solidity
// contracts/strategies/idle/IdleCreditVault.sol L285-286
if (unscaledApr == 0 && !isClosed) {
  _requestWithdrawApr0(_amount, _user);
}
```

`_requestWithdrawApr0` increments `apr0TotalPrincipal` and records `apr0Users[_user].principalEpoch = epochNumber` (L567-577). The bucket "inherits" its validity assumption — that APR stays 0 for the request's whole lifecycle — but nothing enforces or migrates that assumption.

At epoch stop, `IdleCDOEpochVariant.stopEpoch` calls `prepareStopEpochWithApr0` (IdleCDOEpochVariant.sol L362), which contains:

```solidity
// contracts/strategies/idle/IdleCreditVault.sol L505-508
if (unscaledApr != 0) {
  revert NotAllowed();
}
```

`apr0TotalPrincipal` is only ever reset to 0 at the end of this same function (L540). There is no other path that clears it (no sweep on `setAprs`, no claim-time decrement of the global counter — `_claimFundedWithdrawRequest` deletes `apr0Users[_user]` but never decrements `apr0TotalPrincipal`). So once the manager raises the APR for the next epoch while a pending APR0 receipt exists, every subsequent `stopEpoch` (and `stopEpochWithDuration`, which routes through the same preview/settlement) reverts at this check.

Attack sequence (all attacker steps are unprivileged; privileged calls are honest):
1. Buffer phase, `unscaledApr == 0`: attacker (any KYC-passing lender) deposits dust, calls `requestWithdraw(1 wei, tranche)` → `apr0TotalPrincipal = 1`, receipt minted.
2. Manager honestly calls `setAprs`/`_setScaledApr` to a nonzero APR and starts the epoch (`startEpoch` does not touch `apr0TotalPrincipal`).
3. At `epochEndDate`, manager calls `stopEpoch` → `prepareStopEpochWithApr0` sees `_principal = 1` and `unscaledApr != 0` → reverts `NotAllowed`.
4. Every retry reverts identically; the only escape is the manager restoring APR to 0 — i.e., a single dust receipt bricks the entire vault's epoch lifecycle until governance intervention.

### Impact Explanation
Temporary-to-permanent freezing of all vault funds. With `stopEpoch` bricked, the pool cannot pull borrower repayment, cannot fund pending withdrawals, cannot accrue/settle interest, and cannot complete a default/`stopEpochWithDuration` flow — all revert through the same `NotAllowed` check. Funds at risk equal the entire vault TVL plus the borrower's outstanding obligation, triggered by a dust-sized request. Recovery requires the manager to detect the cause and set APR back to 0; if the APR0 receipt's epoch is never settled, the freeze persists indefinitely.

### Likelihood Explanation
Requires two preconditions: an epoch operating with `unscaledApr == 0` (an explicitly supported mode — `apr0Users`, `apr0RateByEpoch`, and `prepareStopEpochWithApr0` exist precisely for it) and a subsequent honest manager APR change while an APR0 receipt is pending. Both are routine operations. The attacker only needs to leave one dust receipt in the bucket before the mode change; they can also spam fresh requests each buffer period to keep the bucket populated. No privileged-role abuse, oracle manipulation, or default is needed.

### Recommendation
Handle APR0 bucket inheritance across mode changes instead of reverting:
- Decrement `apr0TotalPrincipal` whenever an APR0 position is claimed/settled (`_settleApr0`, `delete apr0Users[_user]`, `_claimPostDefaultWithdrawRequest`), so the global counter reflects only truly-open buckets; or
- In `prepareStopEpochWithApr0`, settle the stale bucket at a zero rate (or at `apr0RateByEpoch` of its request epoch) rather than reverting when `unscaledApr != 0`; or
- Have `setAprs` sweep/settle outstanding `apr0TotalPrincipal` so a bucket cannot survive a mode transition.

### Proof of Concept
Foundry fork PoC outline (extend `test/foundry/IdleCreditVault.t.sol`):

```solidity
function testApr0DustReceiptBricksStopEpoch() external {
    IdleCreditVault creditVault = IdleCreditVault(address(strategy));

    // 1. APR = 0 buffer phase; attacker deposits dust and requests withdraw
    vm.prank(manager);
    creditVault.setAprs(0, 0); // unscaledApr == 0
    address attacker = makeAddr("attacker");
    _depositWithUser(attacker, 10, true);
    vm.prank(attacker);
    cdoEpoch.requestWithdraw(1, address(AAtranche)); // apr0TotalPrincipal = 1

    // 2. Honest manager raises APR and runs a normal epoch
    vm.prank(manager);
    creditVault.setAprs(initialApr, initialApr);
    _startEpochAndCheckPrices(0);

    deal(defaultUnderlying, borrower, _expectedFundsEndEpoch());
    vm.warp(cdoEpoch.epochEndDate() + 1);

    // 3. stopEpoch permanently reverts
    vm.prank(manager);
    vm.expectRevert(NotAllowed.selector);
    cdoEpoch.stopEpoch(initialProvidedApr, 0);
}
```

Key invariants to assert: `creditVault.apr0TotalPrincipal() > 0` after step 1, `stopEpoch` reverts in step 3, and the revert persists across repeated calls until `unscaledApr` is set back to 0 — demonstrating that a dust receipt inherited across an APR mode transition freezes the vault.