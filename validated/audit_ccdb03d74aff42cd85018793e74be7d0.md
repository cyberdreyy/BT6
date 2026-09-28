### Title
Unprivileged APR overwrite during the `IdleCreditVault` setup gap before `setWhitelistedCDO` - ([File: contracts/strategies/idle/IdleCreditVault.sol](contracts/strategies/idle/IdleCreditVault.sol))

### Summary
The external bug is a deploy-time divergence: two paired contracts are initialized with different values (`inflationMultiplier` from L1 state vs `1e18` on L2), and an unprivileged user exploits the gap before the values are reconciled. The analog in idle-tranches is the APR pair (`unscaledApr`, `lastApr`) of `IdleCreditVault`: `initialize` sets them from `_apr`, but `setApr`/`setAprs` skip the caller check entirely while `idleCDO == address(0)`, so any EOA can overwrite the vault's fixed APR in the window between strategy initialization and `setWhitelistedCDO`, before the CDO becomes the authoritative APR setter.

### Finding Description
`IdleCreditVault.initialize` stores the agreed borrower APR in `lastApr`/`unscaledApr` but leaves `idleCDO` unset; wiring happens in a later `setWhitelistedCDO` transaction (see test flow `IdleCreditVault.t.sol` lines 62 and 95, and the factory's `_configureCreditVault` which relies on ordering, `IdleCreditVaultFactory.sol` lines 304-306). In `setApr`, the authorization check is skipped when `idleCDO == address(0)` ("this can happen only during the setup"), so during that gap *any* caller — not just owner/manager — can set `unscaledApr` and `lastApr` to arbitrary values up to `maxApr` (default `20e18`). `lastApr` is the scaled fixed APR that drives `expectedEpochInterest` and therefore the interest the borrower must pay and the gains credited to tranche holders at `stopEpoch`; `unscaledApr == 0` also silently switches `requestWithdraw` into the `apr0Users` accounting path.

Attack sequence (unprivileged attacker, buffer/pre-first-epoch phase, fixed-APR mode):
1. Deployer (honest) initializes `IdleCreditVault` with the negotiated APR and deploys the `IdleCDOEpochVariant`, but has not yet called `setWhitelistedCDO`.
2. Attacker calls `setAprs(attackerApr, 20e18)` — no role check applies because `idleCDO == 0`.
3. Owner whitelists the CDO; from then on `setApr` is locked to CDO/manager, but the poisoned `lastApr` persists.
4. Attacker deposits into the AA tranche; the epoch runs with the inflated APR, so `expectedEpochInterest` and AA gains are computed at up to ~20x a 10% intended APR, paid out of borrower repayments at `stopEpoch`.

### Impact Explanation
Direct theft of yield: every lender deposit accrues interest at the attacker-chosen APR, so the borrower (or, on shortfall, the vault's loss waterfall with BB first) overpays interest up to the `maxApr` cap. Loss ≈ `(20e18 − intendedApr) × principal × (epochDuration + buffer)/YEAR` per epoch; e.g. on a 1M USDC vault with intended 10% APR and ~41.5 day scaled epoch, excess interest is ≈ 11.4k USDC per epoch paid to the attacker's tranche position. Alternatively setting `unscaledApr = 0` corrupts withdraw-request routing into the `apr0Users` path for the whole epoch.

### Likelihood Explanation
Requires only that `initialize` and `setWhitelistedCDO` are not atomic — which the codebase itself demonstrates: tests and `ProgrammableBorrowerCreditVault.t.sol` perform them in separate transactions, and even the factory executes `strategy.setAprs(...)` and `strategy.setWhitelistedCDO(...)` as distinct calls where the pre-whitelist `setAprs` would itself go through the unchecked branch. The attacker only needs one transaction in that window; no privileged role, no oracle, no default precondition.

### Recommendation
Bind the permissionless-APR window to the owner: in `setApr`, when `idleCDO == address(0)` require `msg.sender == owner()` (or `manager`) instead of skipping the check. Alternatively set `idleCDO` atomically inside `initialize`/factory deployment so the unchecked branch is unreachable post-deployment, mirroring the report's suggestion to reconcile both sides' values in a single transaction.

### Proof of Concept
```solidity
// Foundry fork test (mainnet, USDC). Deployer steps marked honest; attacker is a fresh EOA.
function testAprHijackDuringSetupGap() public {
    // 1) Honest deployer initializes strategy (intended 10% APR) — CDO not yet wired
    IdleCreditVault cv = new IdleCreditVault();
    cv.initialize(USDC, owner, manager, borrower, "Borrower", 10e18);

    IdleCDOEpochVariant cdo = new IdleCDOEpochVariant();
    cdo.initialize(0, USDC, gov, owner, rebalancer, address(cv), 100000);

    // 2) ATTACK: any EOA overwrites APR while idleCDO == address(0)
    vm.prank(attacker);
    cv.setAprs(10e18, 20e18);           // unscaledApr stays sane, lastApr = max
    assertEq(cv.getApr(), 20e18);

    // 3) Deployer finishes wiring (still honest)
    vm.prank(owner);
    cv.setWhitelistedCDO(address(cdo));
    // setApr is now locked to CDO/manager; poisoned 20e18 persists.

    // 4) Attacker deposits AA; epoch interest is computed at 20e18 scaled APR
    deal(USDC, attacker, 1_000_000e6);
    vm.startPrank(attacker);
    IERC20(USDC).approve(address(cdo), type(uint256).max);
    cdo.depositAA(1_000_000e6);
    vm.stopPrank();

    // expectedEpochInterest / stopEpoch accounting now charges ~2x intended interest,
    // paid from borrower repayment into the attacker's tranche position.
}
```