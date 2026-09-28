### Title
Unprivileged APR hijack during `idleCDO == address(0)` setup window lets any EOA corrupt `unscaledApr`/`lastApr` accounting — ([File: contracts/strategies/idle/IdleCreditVault.sol])

### Summary
`IdleCreditVault.setApr`, `setAprs`, and `setAprsWithBuffer` skip the caller check entirely while `idleCDO` is unset (`if (_cdo != address(0))` guard only). Since the strategy must be `initialize`d before the CDO that references it exists, and `setWhitelistedCDO` is a separate later call, there is a window where any unprivileged EOA can write `unscaledApr` (completely uncapped) and `lastApr` (capped only by `maxApr`). This mirrors the CVE bug class: a local/unprivileged actor exercising a privileged control path due to a missing authorization check on a state-transitioning function.

### Finding Description
In `contracts/strategies/idle/IdleCreditVault.sol`:

```solidity
function setAprs(uint256 _unscaledApr, uint256 _apr) external {
    unscaledApr = _unscaledApr;   // written unconditionally, no cap, no auth here
    setApr(_apr);
}

function setApr(uint256 _apr) public {
    address _cdo = idleCDO;
    if (_cdo != address(0)) {                          // check skipped when unset
        if (msg.sender != _cdo && msg.sender != manager) revert NotAllowed();
    }
    uint256 _maxApr = maxApr;
    if (_maxApr != 0 && _apr > _maxApr) revert NotAllowed();  // caps _apr only
    lastApr = _apr;
}
```
`contracts/strategies/idle/IdleCreditVault.sol:206-235`

`unscaledApr` is a security-critical flag, not just metadata:

- `requestWithdraw` branches on `unscaledApr == 0` to route receipts into the APR0 bucket (`_requestWithdrawApr0`) vs the normal `withdrawsRequestsByEpoch` path (`IdleCreditVault.sol:285-294`).
- `prepareStopEpochWithApr0` hard-reverts with `NotAllowed()` whenever `unscaledApr != 0` and `apr0TotalPrincipal != 0` (`IdleCreditVault.sol:506-508`). Since `prepareStopEpochWithApr0` is called from the CDO's `stopEpoch` flow, a nonzero `unscaledApr` on a vault that accumulated APR0 principal makes every `stopEpoch` revert — the epoch can never close until the manager manually repairs the value.

Attack paths for any EOA during the pre-`setWhitelistedCDO` window:

1. **APR0 vault freeze**: vault intended to run `unscaledApr == 0`. Attacker calls `setAprs(1, 0)`. Once users request withdrawals, `apr0TotalPrincipal > 0`, and `stopEpoch` → `prepareStopEpochWithApr0` always reverts → temporary freezing of all LP principal and pending receipts, requiring manager intervention.
2. **Yield theft on fixed-APR vault**: attacker calls `setApr(0)` (or `setAprsWithBuffer(0, d, b)`) immediately before `startEpoch`. `lastApr = 0` → the CDO computes zero `expectedEpochInterest`, the honest borrower repays only principal, and an entire epoch of LP yield is never owed — direct theft of unclaimed yield quantified as `principal × intendedApr × epochDuration / YEAR`.
3. **Cross-mode mispricing**: `setAprs(0, maxApr)` leaves `lastApr = 20e18` while `unscaledApr = 0`, desynchronizing the two accounting modes the rest of the contract treats as consistent.

The broken invariant is access control: a privileged configuration write is reachable by an unprivileged caller. No existing guard (`_onlyIdleCDO`, KYC, epoch gating, skim) covers these setters.

### Impact Explanation
Depending on the vault mode, the attacker causes either (a) theft of an entire epoch's interest (e.g., a 10M USDC vault at 10% APR over a 90-day epoch loses ≈ 246k USDC of owed interest), or (b) a revert-lock on `stopEpoch` freezing all deposits and pending withdraw receipts until the manager notices and resets `unscaledApr`. Both satisfy the accepted impact classes (theft of unclaimed yield / temporary freezing with quantified loss).

### Likelihood Explanation
Exploitation requires the tx to land between `initialize` (which sets `maxApr`/`lastApr` but leaves `idleCDO == address(0)`) and the owner's `setWhitelistedCDO`. Because the CDO address must exist before it can be whitelisted, this window is structurally present for every deployment; its length depends on the deployer's operational flow, which I could not verify from the indexed code (factory/orchestrator wiring of `setWhitelistedCDO` was not fully traced). If deployment wires both atomically, likelihood drops to zero; that is the main open question.

### Recommendation
Apply the caller check unconditionally once `owner`/`manager` are set: revert in `setApr`/`setAprs`/`setAprsWithBuffer` unless `msg.sender == idleCDO || msg.sender == manager`, and additionally bound `unscaledApr` (e.g., `<= maxApr` or a dedicated cap) so the unscaled value can never desynchronize from `lastApr`. Alternatively, set `idleCDO` atomically during `initialize`.

### Proof of Concept
```solidity
// Fork test against the deployed IdleCreditVault before setWhitelistedCDO is called
function test_AprHijackPreCDO() public {
    // strategy initialized by factory, idleCDO still address(0)
    assertEq(strategy.idleCDO(), address(0));

    address attacker = makeAddr("attacker");

    // Path A: freeze an APR0 vault
    vm.prank(attacker);
    strategy.setAprs(1, 0);            // unscaledApr=1, lastApr=0 — no auth, no revert
    assertEq(strategy.unscaledApr(), 1);

    // ... users request withdrawals (apr0TotalPrincipal > 0) ...
    // stopEpoch -> prepareStopEpochWithApr0 -> revert NotAllowed() every time
    vm.expectRevert(NotAllowed.selector);
    // cdo.stopEpoch(0);

    // Path B: zero out yield on a fixed-APR vault
    vm.prank(attacker);
    strategy.setApr(0);                // lastApr = 0, epoch then runs at 0% APR
    assertEq(strategy.getApr(), 0);
}
```
Note: validity of Path A's freeze depends on `apr0TotalPrincipal > 0` at stop time; Path B's theft depends on the call landing after `initialize` and before the owner's corrective `setAprs`/`startEpoch` sequence.