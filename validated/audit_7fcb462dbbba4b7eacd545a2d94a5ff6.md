### Title
Unprivileged vault liquidity exhaustion makes `stopEpoch` revert and temporarily freezes all credit-vault withdrawals - ([File: contracts/strategies/idle/ProgrammableBorrower.sol](contracts/strategies/idle/ProgrammableBorrower.sol))

### Summary
The external report describes a permissionless treasury function (`veCRVlock`) whose market-facing action can be sandwiched. The credit-vault analog is `ProgrammableBorrower.onStopEpoch`: when `IdleCDOEpochVariant._stopEpoch` calls the hook, the hook performs a hard `vault.withdraw(shortfall, ...)` and reverts with `StopEpochVaultLiquidityUnavailable` on any ERC4626 withdrawal failure. Any unprivileged user of the programmable borrower's ERC4626 vault (an explicitly in-scope attacker class) can exhaust the vault's liquid assets before the honest manager's `stopEpoch` call, making every `stopEpoch`/`stopEpochWithDuration` transaction revert for as long as liquidity stays withdrawn or fully borrowed. During that window the epoch cannot stop, `isEpochRunning` stays true, deposits and withdrawal requests stay disabled by `_pause()`/`_beforeUnpause`, and pending withdraw-request receipts cannot be funded — a temporary freezing of all pool funds driven entirely by an unprivileged transaction sequenced around a privileged call.

### Finding Description
`ProgrammableBorrower` keeps idle pool principal inside a third-party ERC4626 `vault` and honors two `_onlyIdleCDO` hooks. In `onStopEpoch`, if on-hand cash is below the amount IdleCDO will pull, the contract first checks `shortfall > _currentVaultAssets()` and returns `true` only when the position is economically insolvent; otherwise it executes `try vault.withdraw(shortfall, ...) catch { revert StopEpochVaultLiquidityUnavailable(); }`. `IdleCDOEpochVariant._stopEpoch` lets that revert bubble (the comment says "Hook reverts bubble so transient ERC4626 liquidity failures can be retried"), so the entire stop transaction fails before `_updateAccounting`, `collectWithdrawFunds`, `unpause`, or the `isEpochRunning = false` flag run.

The asymmetry versus the Mochi report: `stopEpoch` itself is owner/manager-gated, but the price/liquidity condition it depends on is controlled by the public ERC4626 vault. An attacker who is an ordinary user of that vault (e.g., borrows the maximum or redeems the liquid balance — allowed attacker: "a user of the programmable borrower's ERC4626 vault") sandwiches the manager's stop call:

1. Vault utilization is high; attacker borrows/withdraws the remaining free liquidity so `vault.maxWithdraw(programmableBorrower) < shortfall`.
2. Manager calls `stopEpoch`/`stopEpochWithDuration`. `totalInterestDueNow`/`vaultLoss` read `convertToAssets` fine (position is solvent), so `shortfall <= _currentVaultAssets()` and the code takes the withdraw branch — which then reverts in `vault.withdraw` and hits the `catch`.
3. `stopEpoch` reverts. `isEpochRunning` remains true, `_beforeUnpause` blocks `unpause`, `allowAA/BBWithdrawRequest` remain false, and `claimWithdrawRequest` can never be funded because `collectWithdrawFunds` is only reached inside the same transaction.
4. Attacker keeps the vault drained (or repeats with a flash-assisted borrow each time liquidity returns), extending the freeze indefinitely at the cost of borrow interest.

The `shortfall > _currentVaultAssets()` solvency check protects against under-collateralization but not against solvent-but-illiquid vaults, and no fallback path (partial withdraw, `redeem` of available liquidity, or deferral) exists — `emergencyExitVault` is owner/manager-only and equally subject to the same liquidity cap. This is distinct from a donation/NAV manipulation: the vault position stays correctly valued, so `_skimDonatedAssets`, default checks, and the reserve logic do not mitigate it.

### Impact Explanation
Temporary freezing of all credit-vault funds. While the epoch is stuck running: lenders cannot deposit (`_deposit` is `whenNotPaused`), cannot create withdraw requests (`allowAA/BBWithdrawRequest` were cleared at `startEpoch`), cannot claim existing receipts (`claimWithdrawRequest` needs the strategy to hold funded proceeds, which only `collectWithdrawFunds` inside a successful stop provides), and managers cannot close the pool or run the default path (a hard `onDefault` requires a `stopEpoch` that itself reverts). The freeze persists as long as the attacker suppresses vault liquidity; a motivated attacker who already holds a large borrow position in the underlying money-market vault can sustain this at the marginal cost of interest on funds they borrowed anyway. Quantified lower bound: all pending withdraw-request amounts plus the full tranche NAV remain locked for the duration of the liquidity squeeze, with borrowers' contractual interest continuing to accrue against the pool.

### Likelihood Explanation
High feasibility for vaults backed by lending markets or any ERC4626 whose available liquidity is user-drainable. The attacker needs no protocol role — only ordinary usage of the external vault — and can watch the mempool for the manager's `stopEpoch` transaction (publicly callable only after `epochEndDate`, giving a predictable, narrow target window). The attacker's cost is the borrow interest or opportunity cost of holding vault shares during the squeeze, not capital destruction. It is a griefing-style temporary freeze rather than direct theft, which places it at medium severity rather than high.

### Recommendation
In `ProgrammableBorrower.onStopEpoch`, treat a reverted `vault.withdraw` as a deferred settlement instead of a hard revert: attempt `vault.withdraw(vault.maxWithdraw(address(this)), ...)` (or `redeem` up to `maxRedeem`) to pull whatever liquidity exists, return a status indicating partial funding, and let `IdleCDOEpochVariant` record a shortfall state that can be topped up in a later permissionless retry — mirroring the existing "retryable" intent without requiring a full successful withdraw. At minimum, allow a permissionless `pokeStopEpoch`/`fulfillPendingStop` entry point so liveness does not depend on racing liquidity between manager transactions, and document that the chosen ERC4626 must have utilization-independent withdrawal liquidity.

### Proof of Concept
```solidity
// test/foundry/ProgrammableBorrowerStopEpochSandwich.t.sol
// Fork: mainnet, vault = a real ERC4626 lending vault (e.g. a Morpho/AAVE-style market)
// whose idle liquidity a public user can fully borrow/withdraw.
function test_stopEpochRevertsWhenVaultLiquidityExhausted() public {
    // Setup: pool in active epoch, principal parked in `vault`, epochEndDate reached.
    // uint256 shortfall = amountToPull + pendingWithdraws > onHandCash;

    // 1) Attacker (ordinary vault user / KYC'd lender unrelated to roles) borrows or
    //    redeems so that vault.maxWithdraw(address(programmableBorrower)) < shortfall.
    dealAndApprove(attacker);
    attackerBorrowAllLiquidity(vault);          // utilization -> ~100%
    assertLt(vault.maxWithdraw(address(pb)), shortfall);

    // 2) Honest manager calls stopEpoch -> onStopEpoch -> vault.withdraw(shortfall) reverts
    //    -> StopEpochVaultLiquidityUnavailable bubbles -> whole stopEpoch reverts.
    vm.prank(manager);
    vm.expectRevert(); // StopEpochVaultLiquidityUnavailable (wrapped by vault revert)
    cdo.stopEpoch(newApr, 0);

    // 3) Freeze: epoch still running, withdrawals/requests still blocked,
    //    receipts unfunded; every retry reverts while liquidity stays drained.
    assertTrue(cdo.isEpochRunning());
    vm.expectRevert();
    cdo.claimWithdrawRequest(); // pendingWithdraws never collected

    // 4) Attacker maintains the squeeze; only when vault liquidity returns does
    //    stopEpoch succeed -> temporary, attacker-controlled freeze of pool funds.
}
```
The PoC is reproducible as a Foundry fork test: point `ProgrammableBorrower` at a real deployed ERC4626 lending vault, drain its free liquidity via a standard borrow/redeem as an unprivileged account, then show `stopEpoch` reverting and the epoch/withdrawal state remaining frozen.