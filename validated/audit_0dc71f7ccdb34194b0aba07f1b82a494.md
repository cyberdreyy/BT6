The analog maps directly: `IdleCreditVaultFactory.deployCreditVault` / `deployRevolvingCreditVault` are permissionless and accept fully caller-supplied `implementation`, `underlying`, and `vault` addresses. The proxies delegate to the supplied implementation, and the system grants unlimited ERC20 allowances to supplied contracts — `IdleCDOCreditVault.initialize` max-approves the supplied `strategy` for both underlying and strategyToken [1](#0-0) , and `ProgrammableBorrower.initialize` max-approves the supplied `_vault` and `_idleCDO` for underlying [2](#0-1) .

### Title
Permissionless factory allows deploying credit vaults with malicious implementation/strategy/vault contracts that receive unlimited allowance and full delegatecall control over depositor funds - (File: contracts/IdleCreditVaultFactory.sol)

### Summary
`deployCreditVault` and `deployRevolvingCreditVault` have no access control and trust caller-supplied `CreditVaultParams.implementation`, `StrategyData.implementation`, and `ProgrammableBorrowerParams.vault` [3](#0-2) . Proxies are deployed pointing at attacker-controlled implementation code, and unlimited allowances are granted to attacker-controlled contract addresses, letting the attacker steal all deposited and borrowed underlying.

### Finding Description
Both deploy functions are `external` with no `onlyOwner`/`onlyTreasury`/whitelist gate — the only checks are `_checkMinimumFees` and `manager != 0` [4](#0-3) . `_deployBaseCreditVault` creates transparent proxies over `strategyData.implementation` and `cvParams.implementation` [5](#0-4) , and `IdleCDOCreditVault.initialize` then grants `type(uint256).max` allowance of `token` and `strategyToken` to the supplied `strategy` [1](#0-0) . In the revolving path, `ProgrammableBorrower.initialize` grants max underlying allowance to the caller-supplied `vault` [6](#0-5) , and `setVault` repeats the same unlimited approval pattern for owner/manager-supplied vaults [7](#0-6) . Nothing validates that implementations are audited bytecode or that the vault is a sanctioned ERC4626 — the `emit CreditVaultDeployed` event makes the malicious deployment indistinguishable from official ones to an off-chain UI or indexer.

### Impact Explanation
- Path A (malicious implementation): the CDO proxy delegates to attacker bytecode; any depositor who approves the proxy for `underlying` can be drained via `transferFrom` inside `depositAA`, and all proxy-held funds/approvals are fully controlled.
- Path B (malicious vault): the attacker supplies their own `vault` in `deployRevolvingCreditVault`; `ProgrammableBorrower` max-approves it, so the vault contract can `transferFrom` all PB-held underlying (borrowed principal + repaid interest) at any time.
- Path C (malicious strategy): `strategyData.implementation` = attacker code receives max allowance of the vault's `token`/`strategyToken` and `deposit()`/`redeem()` are `onlyIdleCDO`-gated only by the `idleCDO` slot the attacker sets in their own `initialize`.
Broken invariant: solvency — the "factory-deployed ⇒ safe Pareto vault" assumption. Loss = 100% of deposited and borrowed funds routed through the malicious instance.

### Likelihood Explanation
Requires luring lenders/borrower to use the malicious vault (front-end reliance on `CreditVaultDeployed` events or vanity deployment). This is the same trust model as the Sablier report: one transaction to `deployRevolvingCreditVault` with `programmableBorrowerParams.vault = attackerContract` is enough to arm the theft; no privileged role needed since deploy is permissionless.

### Recommendation
Whitelist permitted `implementation` addresses (CDO, strategy, queue, escrow, PB) and require `implementation` params to be in the whitelist; whitelist the ERC4626 `vault` per underlying or restrict `deployRevolvingCreditVault` to treasury; optionally verify `IERC4626(vault).asset() == underlying` and code-hash of implementations.

### Proof of Concept
```solidity
// Foundry, mainnet-style unit test on factory
function testMaliciousVaultDrainsPB() public {
  MaliciousVault mv = new MaliciousVault(usdc); // implements IERC4626, exposes skim()
  factory.deployRevolvingCreditVault(
    cvParams,                     // underlying = USDC
    strategyData,                 // legit strategy impl
    ProgrammableBorrowerParams(pbImpl, address(mv), borrower, apr),
    ancillaryParams
  );
  // PB.initialize ran: usdc.approve(mv, type(uint256).max)
  // After honest borrower funds land in PB:
  deal(usdc, pb, 1_000_000e6);
  mv.skim(pb);                    // usdc.transferFrom(pb, attacker, balance)
  assertEq(usdc.balanceOf(pb), 0);
}
```
`MaliciousVault.skim` is just `underlying.transferFrom(pb, attacker, underlying.balanceOf(pb))`, enabled by the unlimited approval set in `ProgrammableBorrower.initialize` [2](#0-1) .

### Citations

**File:** contracts/IdleCDOCreditVault.sol (L77-79)
```text
    // Set allowance for strategy
    _allowUnlimitedSpend(_guardedToken, _strategy);
    _allowUnlimitedSpend(_strategyToken, _strategy);
```

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L125-135)
```text
    underlyingToken = IERC20Detailed(_underlyingToken);
    vault = IERC4626(_vault);
    idleCDO = _idleCDO;
    manager = _manager;
    borrower = _borrower;
    borrowerApr = _borrowerApr;

    // Set approvals for vault and IdleCDO
    _allowUnlimitedSpend(_underlyingToken, _vault);
    _allowUnlimitedSpend(_underlyingToken, _idleCDO);
  }
```

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L166-168)
```text
    underlyingToken.safeApprove(address(vault), 0);
    vault = IERC4626(_vault);
    _allowUnlimitedSpend(address(underlyingToken), _vault);
```

**File:** contracts/IdleCreditVaultFactory.sol (L92-96)
```text
  function deployCreditVault(
    CreditVaultParams memory cvParams,
    StrategyData memory strategyData,
    AncillaryParams memory ancillaryParams
  ) external {
```

**File:** contracts/IdleCreditVaultFactory.sol (L97-99)
```text
    _checkMinimumFees(cvParams);
    address manager = strategyData.manager;
    if (manager == address(0)) revert Is0();
```

**File:** contracts/IdleCreditVaultFactory.sol (L182-207)
```text
    strategy = IdleCreditVault(_deployProxy(
      strategyData.implementation,
      abi.encodeWithSelector(
        IdleCreditVault.initialize.selector,
        cvParams.underlying,
        address(this),
        strategyData.manager,
        strategyData.borrower,
        strategyData.borrowerName,
        cvParams.apr
      )
    ));

    cv = IdleCDOEpochVariant(_deployProxy(
      cvParams.implementation,
      abi.encodeWithSelector(
        IdleCDOCreditVault.initialize.selector,
        cvParams.limit,
        cvParams.underlying,
        treasury,
        address(this),
        address(0),
        address(strategy),
        FULL_ALLOC
      )
    ));
```
