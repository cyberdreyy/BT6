### Title
Permissionless `deployCreditVault` lets anyone deploy a fully functional impersonated credit vault with an attacker-controlled borrower and manager - ([File: contracts/IdleCreditVaultFactory.sol](contracts/IdleCreditVaultFactory.sol))

### Summary
`IdleCreditVaultFactory.deployCreditVault`, `deployRevolvingCreditVault`, and `IdleCDOFactory.deployCDO` have no access control. Any EOA can deploy a complete, authentic credit vault stack (IdleCDOEpochVariant + IdleCreditVault strategy + queue + Keyring whitelist) that emits the same `CreditVaultDeployed` event the backend/UI relies on, while setting themselves as `strategyData.borrower` and `strategyData.manager`. Deposit funds routed into this doppelgänger vault are lent to the attacker's borrower address and can be permanently drained.

### Finding Description
The deploy functions are `external` with no caller check:

- `deployCreditVault` at `contracts/IdleCreditVaultFactory.sol:92-124` only validates `_checkMinimumFees` and `manager != 0`.
- `deployRevolvingCreditVault` at `contracts/IdleCreditVaultFactory.sol:126-166` is likewise unrestricted.
- `IdleCDOFactory.deployCDO` at `contracts/IdleCDOFactory.sol:9-12` is fully permissionless.

Inside `_deployBaseCreditVault` (`:175-208`), the attacker-supplied `strategyData.borrower` and `strategyData.manager` are passed directly into `IdleCreditVault.initialize`, which stores them as `borrower` and `manager` (`IdleCreditVault.sol:141-142`). `_configureCreditVault` (`:288-307`) then calls `cv.setGuardian(manager)` and `strategy.setWhitelistedCDO(cv)`, wiring the attacker's manager as the vault's guardian. `_deployKeyring` + `_finalizeKeyringAdmin` (`:168-173`, `:323-327`) hand the attacker admin of the freshly deployed `KeyringIdleWhitelist`, so they can whitelist accomplice wallets at will. Ownership of the strategy and CDO is transferred to `treasury` (`:113-114`), but this does not protect deposited principal: the pool's underlying is lent out to `borrower`, and the borrower/manager roles that control fund custody during epochs are entirely attacker-chosen.

Because the deployment is genuine (real implementation proxies, real events, identical underlying token and `borrowerName`), a UI or indexer keying on `CreditVaultDeployed` sees a vault indistinguishable from a legitimate one — the exact doppelgänger condition of the reference report, except here the stakes are deposited principal rather than just votes.

### Impact Explanation
A KYC-passing or unwhitelisted attacker (no privileged role needed) deploys a vault copying a live pool's `underlying`, `borrowerName`, APR and epoch parameters, but with `borrower`/`manager`/`feeReceiver` pointing at themselves. Victims who deposit through the confused UI mint real tranche tokens; their underlying flows to the `IdleCreditVault` strategy and is disbursed to the attacker-borrower during the epoch lifecycle. The attacker simply never repays, so at epoch end the pool is insolvent and tranche holders absorb a 100% loss — direct theft of user deposits, quantified as the entire deposited NAV of the impersonated vault. The minimum-fee check (`_checkMinimumFees`, `MIN_PERFORMANCE_FEE = 5_000` / `MIN_MANAGEMENT_FEE = 500`) only constrains fee parameters and provides no protection; treasury-ownership of the proxies does not restrict who receives the lent principal.

### Likelihood Explanation
High, conditional on UI/indexer trust. The attack requires no privilege, no timing, and no protocol interaction beyond one `deployCreditVault` call plus luring deposits — the same precondition (backend reliance on deployment events) that the original Gitcoin report identifies. The code gives no registry, no `ownedBy` check, and no caller restriction, so the impersonation path is always open. Residual uncertainty: the degree of theft depends on whether the off-chain frontend lists factory events indiscriminately, and on the exact borrower drawdown mechanics in `IdleCreditVault`'s epoch flow (the borrower is the designated recipient of pooled liquidity in this design; treasury ownership does not claw back lent funds).

### Recommendation
Restrict `deployCreditVault`, `deployRevolvingCreditVault`, and `IdleCDOFactory.deployCDO` to `treasury` (or an explicit deployer role), or maintain an on-chain registry/allowlist of sanctioned vaults that the UI consumes instead of raw `CreditVaultDeployed` events. At minimum, validate that `strategyData.borrower`/`manager` come from a governance-approved set.

### Proof of Concept
Foundry fork sketch against a mainnet `IdleCreditVaultFactory`:

```solidity
function test_ImpersonatedVaultDrainsDeposits() public {
    IdleCreditVaultFactory factory = IdleCreditVaultFactory(FACTORY);
    IERC20 usdc = IERC20(UNDERLYING);

    // attacker params: self as manager, borrower, feeReceiver; same underlying/borrowerName as real pool
    IdleCreditVaultFactory.CreditVaultParams memory p = _paramsLikeRealVault();
    p.feeReceiver = attacker;
    IdleCreditVaultFactory.StrategyData memory s = IdleCreditVaultFactory.StrategyData({
        implementation: realStrategyImpl,      // reused legit implementation
        manager: attacker,
        borrower: attacker,
        borrowerName: "RealBorrower"           // identical display name
    });

    vm.prank(attacker);
    factory.deployCreditVault(p, s, _ancillary(realKeyring));

    // victim deposits into the doppelgänger vault surfaced by the UI
    deal(address(usdc), victim, 1_000_000e6);
    vm.startPrank(victim);
    usdc.approve(fakeCV, type(uint256).max);
    IdleCDOEpochVariant(fakeCV).depositAA(1_000_000e6);
    vm.stopPrank();

    // attacker-guardian runs the epoch; pool funds are lent to attacker-borrower
    // borrower never repays -> stopEpochWithDuration loss -> victim NAV -> 0
}
```