### Title
Unauthenticated APR injection during `idleCDO == address(0)` setup window lets any EOA corrupt interest accounting - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
The external bug class is an unsanitized argument (`--shell`) passed to a privileged, root-running command. The analog is `IdleCreditVault.setAprs`/`setApr`: the authorization check in `setApr` is skipped entirely while `idleCDO` is unset, so between `initialize` and `setWhitelistedCDO` any EOA can inject a privileged accounting parameter — an arbitrarily large `unscaledApr` (never bounds-checked) and a `lastApr` of up to `DEFAULT_MAX_APR` (20e18 = 2000% APR). All downstream epoch interest, withdraw-receipt, and APR0/instant-withdraw branching consume these attacker-chosen values.

### Finding Description
`initialize` (contracts/strategies/idle/IdleCreditVault.sol:124-158) sets `maxApr = DEFAULT_MAX_APR` and `lastApr`, but leaves `idleCDO == address(0)` until the owner later calls `setWhitelistedCDO` (line 954). In `setApr` (lines 225-235) the caller check is conditional:

```solidity
address _cdo = idleCDO;
if (_cdo != address(0)) {
  if (msg.sender != _cdo && msg.sender != manager) revert NotAllowed();
}
```

So while `idleCDO == 0`, `setApr`, `setAprs`, and `setAprsWithBuffer` (lines 206-220) are callable by anyone. `setAprs`/`setAprsWithBuffer` write `unscaledApr` with no cap at all — only the scaled `_apr` is checked against `maxApr`.

Consequences of injected values, reachable by a normal unprivileged lender afterward:

- `getApr()`/`lastApr` (up to 20e18) feeds `_getStrategyApr` → `_calcInterest`/`_calcInterestWithApr` (IdleCDOEpochVariant.sol:800-809), inflating `expectedEpochInterest`, `trancheInterest` in `depositDuringEpoch`, and the interest component baked into withdraw receipts via `_calcInterestWithdrawRequest` in `requestWithdraw` (line 773). Inflated receipts become `pendingWithdraws` in `IdleCreditVault.requestWithdraw` (line 279) that `stopEpoch` must source from the borrower.
- `unscaledApr` is not capped: setting it to `0` silently switches every `requestWithdraw` into the APR0 bucket path (`unscaledApr == 0` check at IdleCreditVault.sol:285), desynchronizing `pendingWithdraws`/`withdrawsRequestsByEpoch` vs `apr0TotalPrincipal` accounting relative to what the borrower agreed to fund; setting it huge disables the instant-withdraw trigger (`lastEpochApr > currentApr + instantWithdrawAprDelta`, IdleCDOEpochVariant.sol:763).
- If receipts are inflated beyond what the honest borrower funded, `stopEpoch`'s pull fails or the pool enters default; on `finalizeDefault`, the attacker's inflated `withdrawsRequestsByEpoch` basis is haircutted by `defaultRecoveryPrice` pro-rata with all other claimants, letting the attacker extract a share of the `defaultRecoveryReserve` disproportionate to real deposits while honest LPs absorb the shortfall.

### Impact Explanation
An unprivileged EOA permanently corrupts the pool's core interest parameter. Either (a) inflated `lastApr` inflates withdraw-request claim bases, diverting recovery reserve / funded withdrawals to the attacker at other LPs' expense (theft proportional to recovery funding), or (b) a mismatched `unscaledApr` (0 or huge) forces all requests into a different accounting bucket than the funded one, freezing or mis-distributing claims. Loss ≈ the attacker's share of an inflated claim basis vs. the funded recovery/funding amount.

### Likelihood Explanation
Requires the deployment window between `initialize` and `setWhitelistedCDO` — a plausible window on any non-atomic deployment (proxy + separate owner tx), and front-runnable on public mempools. After `idleCDO` is set the bug is unreachable, so likelihood is deployment-dependent but the preconditions need no privileged action from the attacker.

### Recommendation
- Remove the `idleCDO == address(0)` bypass: require `msg.sender == manager` (or `onlyOwner`) when `idleCDO` is unset in `setApr`.
- Bound `unscaledApr` in `setAprs`/`setAprsWithBuffer` (e.g., against `maxApr` before buffer scaling).
- Wire `idleCDO` inside `initialize` (or a factory) so there is no unauthenticated window.

### Proof of Concept
```solidity
// Foundry fork PoC (setup window between initialize and setWhitelistedCDO)
function test_AprInjectionDuringSetupWindow() external {
    // 1. Owner deploys proxy + initialize (idleCDO still address(0))
    IdleCreditVault vault = IdleCreditVault(address(new ERC1967Proxy(
        address(new IdleCreditVault()),
        abi.encodeWithSelector(
            IdleCreditVault.initialize.selector,
            address(usdc), owner, manager, borrower, "Borrower", 0
        )
    )));

    // 2. Attacker (any EOA) injects privileged params before owner calls setWhitelistedCDO
    address attacker = makeAddr("attacker");
    vm.prank(attacker);
    vault.setAprs(0, 20e18); // unscaledApr = 0 (uncapped write), lastApr = 2000% APR

    assertEq(vault.unscaledApr(), 0);
    assertEq(vault.getApr(), 20e18);

    // 3. Owner completes setup; injected values persist
    vm.prank(owner);
    vault.setWhitelistedCDO(address(cdoEpoch));

    // 4. All subsequent requestWithdraw calls take the APR0 bucket path
    //    (`unscaledApr == 0`, IdleCreditVault.sol:285), and _calcInterest uses 2000% APR,
    //    inflating receipts/pendingWithdraws the borrower must fund at stopEpoch.
}
```