### Title
Unprivileged ERC4626 vault liquidity drain freezes epoch settlement and all LP funds via `onStopEpoch` revert - (File: contracts/strategies/idle/ProgrammableBorrower.sol)

### Summary
XXE is a bug class where processing reaches an attacker-controllable *external entity* and that entity's behavior subverts the host. The strongest analog in this codebase is `ProgrammableBorrower`, which delegates its stop-epoch settlement to an external, permissionless ERC4626 vault (`vault.withdraw`, `convertToAssets`). Any unprivileged participant in the vault's underlying markets (e.g., a Morpho borrower draining lendable liquidity) can force `vault.withdraw` to revert, which makes `ProgrammableBorrower.onStopEpoch` revert with `StopEpochVaultLiquidityUnavailable` and therefore makes `IdleCDOEpochVariant.stopEpoch` permanently revert for as long as the attacker keeps the vault illiquid. All lender principal, accrued interest, and matured withdraw requests stay locked in the facility.

### Finding Description
In `IdleCDOEpochVariant.stopEpoch`, the programmable-borrower path calls `IProgrammableBorrower.onStopEpoch` and bubbles reverts up intentionally ("Hook reverts bubble so transient ERC4626 liquidity failures can be retried", `contracts/IdleCDOEpochVariant.sol:396-404`).

In `ProgrammableBorrower.onStopEpoch` (`contracts/strategies/idle/ProgrammableBorrower.sol:231-268`):

```solidity
if (_amountRequired > onHand) {
  uint256 shortfall = _amountRequired - onHand;
  if (shortfall > _currentVaultAssets()) return true;
  try vault.withdraw(shortfall, address(this), address(this)) returns (uint256 shares) {
    ...
  } catch {
    revert StopEpochVaultLiquidityUnavailable();
  }
}
```

The revert fires whenever the vault holds enough shares on paper (`shortfall <= _currentVaultAssets()`) but cannot pay out cash — exactly the state a third party can create on a MetaMorpho/ERC4626 vault by borrowing out the underlying market liquidity (an explicitly allowed attacker: "a user of the programmable borrower's ERC4626 vault").

There is no recovery path that bypasses the vault while the position exists:
- `emergencyExitVault` also calls `vault.redeem` and reverts on the same illiquidity (`ProgrammableBorrower.sol:361-372`).
- `setVault` reverts while `vault.balanceOf(address(this)) != 0` or `epochAccountingActive` (`ProgrammableBorrower.sol:165`), so the manager cannot swap to a healthy vault mid-epoch.
- The default path `_handleBorrowerDefault` is only reachable when `onStopEpoch` *returns* false, not when it reverts, so honest-manager sequencing cannot route around it.

Because `epochAccountingActive` stays true and `stopEpoch` never completes, the epoch state machine is stuck in "running": `requestWithdraw`/`claimWithdrawRequest`/`claimInstantWithdrawRequest` settle only across epoch boundaries, and tranche `withdrawAA/withdrawBB` are gated off during a running epoch, so LP funds are frozen until vault liquidity returns — which the attacker can keep suppressed at the cost of borrow interest only.

### Impact Explanation
Temporary freezing of all funds in the facility: total loss of availability for the full CDO TVL (AA + BB tranche NAV plus pending withdraw requests) for as long as the attacker sustains the liquidity drain. The attacker can renew the drain market-by-market (MetaMorpho vaults allocate across multiple Morpho markets; draining the cheapest borrow markets repeatedly is far cheaper than the frozen TVL). No privileged role and no malicious borrower is required — the borrower can be fully repaid and the attack still works, because `onStopEpoch` must pull *interest and pending withdraws* from the vault sleeve.

### Likelihood Explanation
Requires a vault whose underlying markets expose borrowable liquidity of the same asset — precisely the MetaMorpho deployment this contract is built for (see `test/foundry/ProgrammableBorrowerCreditVault.t.sol`, which forks a live MetaMorpho vault). An attacker needs capital or a flash-loan-then-borrow position sufficient to take utilization to ~100% across the vault's markets whenever `stopEpoch` is called at epoch end. The window is predictable (epoch end date is public), and the attacker can keep utilization pinned indefinitely, so the freeze duration is attacker-controlled. Profit is indirect (griefing/extortion or forcing an emergency unwind on worse terms), but the accepted impact class is temporary freezing with quantified funds at risk — here the entire pool TVL.

### Recommendation
Bound external-vault influence on the settlement path, mirroring the "don't resolve untrusted external entities" fix for XXE:

- Make the catch path degrade to a default/partial settlement instead of reverting: e.g., treat a failed withdraw as a failed pull so `IdleCDOEpochVariant` can enter `_handleBorrowerDefault` or a `stopEpochWithDuration` loss path, rather than leaving the epoch running.
- Alternatively, allow `setVault`/`emergencyExitVault` while `epochAccountingActive` with the unrecovered shortfall booked as a borrower loss, and/or let the manager trigger `onDefault`-style shutdown when `onStopEpoch` reverts.
- At minimum, cap the amount `onStopEpoch` must source from the vault (withdraw only up to `vault.maxWithdraw`), and treat residual shortfall as a payable-later deficit rather than a hard revert.

### Proof of Concept
Foundry fork PoC against the live MetaMorpho vault used in `test/foundry/ProgrammableBorrowerCreditVault.t.sol`:

```solidity
// Setup: depositAA, startEpoch, borrower draws funds, warp to epochEndDate.
// Attacker (unprivileged) drains lendable liquidity in every Morpho market
// the MetaMorpho vault allocates to:
for each market in morphoVault.supplyQueue():
    morpho.borrow(marketParams, market.totalBorrowableAssets(), 0, attacker, attacker);

// Manager tries to settle the epoch:
vm.prank(manager);
vm.expectRevert(); // StopEpochVaultLiquidityUnavailable bubbles out of onStopEpoch
cdoEpoch.stopEpoch(0, 0);

// Same for the emergency escape:
vm.prank(owner);
vm.expectRevert();
programmableBorrower.emergencyExitVault(0); // vault.redeem reverts on illiquidity

// Funds remain frozen while attacker maintains ~100% utilization:
// claimWithdrawRequest / withdrawAA all remain blocked because epoch never stops.
```

The revert propagates from `vault.withdraw` → `catch { revert StopEpochVaultLiquidityUnavailable(); }` (`ProgrammableBorrower.sol:246-253`) → `onStopEpoch` → `stopEpoch` (`IdleCDOEpochVariant.sol:398`). Repeating the borrow whenever utilization drops keeps the freeze alive; attacker cost is limited to borrow interest on the drained liquidity.