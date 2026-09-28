### Title
Permissionless `IdleCreditVaultFactory` lets anyone deploy "official" vaults with malicious borrower, implementation or Keyring parameters — enabling theft of depositor funds - ([File: contracts/IdleCreditVaultFactory.sol](contracts/IdleCreditVaultFactory.sol))

### Summary
`deployCreditVault()` and `deployRevolvingCreditVault()` are `external` with no access control. An unprivileged attacker supplies all security-critical parameters — the CDO/strategy/`ProgrammableBorrower`/`IdleCDOEpochQueue`/`WriteOffEscrow` `implementation` addresses, `underlying` token, `keyring` credential contract, `borrower`, `manager`, and `feeReceiver`. The factory then emits `CreditVaultDeployed`, making the malicious vault indistinguishable from a legitimate deployment. Once victims deposit, the attacker (as borrower or via a malicious implementation) drains the funds. This is the direct analog of the Tracer `deployPool()` finding: permissionless deployment with deployer-controlled trusted parameters (oracle/quoteToken ↔ borrower/keyring/implementation/underlying). Unlike Tracer, there is no allowlist or DAO safety check implemented in code here — the factory wires whatever it is given and hands ownership to `treasury`, lending it the protocol's imprimatur. [1](#0-0) 

### Finding Description
`deployCreditVault` (L92–124) and `deployRevolvingCreditVault` (L126–166) have no `onlyOwner`/role check. Every dangerous input comes from `msg.sender`'s calldata:

- `cvParams.implementation` / `strategyData.implementation` / `programmableBorrowerParams.implementation` — passed straight to `TransparentUpgradeableProxy`, so the attacker can point the proxies at arbitrary malicious contracts, while the factory still calls `initialize` and emits the standard deployment event. [2](#0-1) 
- `strategyData.borrower` — set on the `IdleCreditVault` strategy via `initialize`; the borrower is the party entitled to draw the vault's underlying through the strategy's borrow path. An attacker-set borrower can call borrow and steal all deposited underlying. [3](#0-2) 
- `ancillaryParams.keyring` — a fake Keyring credential contract makes `KeyringIdleWhitelist.isWalletAllowed` return true for the attacker (and false for real users, or true for attacker-controlled sybils), so KYC gating provides no protection inside the malicious vault. [4](#0-3) 
- `cvParams.underlying`, `feeReceiver`, `manager` (which becomes `guardian` via `_configureCreditVault` at L303) — all attacker-chosen.

The only checks are `_checkMinimumFees` (a floor on fees, not on who receives them — `feeReceiver` is attacker-controlled) and `manager != 0`. There is no allowlist on `implementation`, `underlying`, `keyring`, or `borrower`. After deployment, `strategy.transferOwnership(treasury)` and `cv.transferOwnership(treasury)` run (L113–114, L155–156) — but with a malicious implementation `transferOwnership` is a no-op controlled by the attacker, and even with the honest implementation the borrower/whitelist wiring is already fixed in the attacker's favor.

### Impact Explanation
Direct theft. Two concrete variants:

1. **Malicious borrower + fake Keyring (honest implementations).** Attacker deploys a vault with real `IdleCDOEpochVariant`/`IdleCreditVault` implementations, `underlying` = USDC, `keyring` = attacker contract returning `true`, `borrower` = attacker EOA, `manager` = attacker. Victims see a factory-emitted `CreditVaultDeployed` event and deposit USDC; the attacker-borrower draws the entire balance through the strategy's borrow function. Loss = 100% of deposits.

2. **Malicious `implementation`.** Attacker passes their own contract as `cvParams.implementation`; it accepts the `initialize` call, mimics a vault, and sweeps any `underlying` transferred to it. Same outcome with zero protocol constraints.

The broken invariant is deployment integrity: the factory certifies (via event and treasury ownership handoff) vaults whose trust parameters were never vetted, exactly the "malicious oracleWrapper/quoteToken" class from the Tracer report.

### Likelihood Explanation
Deployment is a single permissionless call costing only gas. Exploitation requires attracting depositors, but the deployment carries the factory's identity and standard event — identical to legitimate launches — and the protocol has no on-chain registry distinguishing vetted from unvetted vaults. Frontend curation does not mitigate contracts/intermediate users interacting directly. All roles that could stop it (treasury as owner) receive control only after the malicious wiring is complete.

### Recommendation
- Restrict `deployCreditVault`/`deployRevolvingCreditVault` to `treasury` (or a dedicated deployer role), matching the privileged-deployment model of `IdleCDOFactory`.
- If permissionless deployment is intended, add allowlists for `implementation` addresses (kept by the factory and checked in `_deployProxy`), `underlying` tokens, and `keyring` credential contracts, and emit a clear "unverified" flag in `CreditVaultDeployed` for non-allowlisted parameter sets.

### Proof of Concept
```solidity
// test/foundry/PermissionlessFactoryExploit.t.sol — fork mainnet
function test_PermissionlessDeployDrainsDeposits() public {
    // attacker = unprivileged EOA
    MockFactoryERC20 usdc = new MockFactoryERC20("USDC","USDC",6);

    IdleCreditVaultFactory.CreditVaultParams memory cv = IdleCreditVaultFactory.CreditVaultParams({
        implementation: address(new IdleCDOEpochVariant()),
        limit: type(uint256).max,
        underlying: address(usdc),
        apr: 10e18, epochDuration: 7 days, bufferPeriod: 1 days,
        instantWithdrawDelay: 0, instantWithdrawAprDelta: 0,
        disableInstantWithdraw: false,
        keyringPolicy: 1,                 // require KYC — but keyring is attacker's
        feeReceiver: attacker,
        fees: 5_000, managementFee: 500,  // passes _checkMinimumFees
        isInterestMinted: false, isDepositDuringEpochDisabled: false
    });
    IdleCreditVaultFactory.StrategyData memory sd = IdleCreditVaultFactory.StrategyData({
        implementation: address(new IdleCreditVault()),
        manager: attacker, borrower: attacker, borrowerName: "evil"
    });
    IdleCreditVaultFactory.AncillaryParams memory ap = IdleCreditVaultFactory.AncillaryParams({
        keyring: address(new FakeKeyring()), // isWalletAllowed -> attacker only
        queueImplementation: address(0), prefundedDepositWindow: 0,
        writeOffImplementation: address(0)
    });

    vm.prank(attacker);
    factory.deployCreditVault(cv, sd, ap);   // succeeds, emits CreditVaultDeployed

    // victim deposits via the freshly deployed CV (whitelisted by FakeKeyring or
    // policy checks that pass for attacker-deployed whitelist)
    // -> attacker, as `borrower`, calls strategy.borrow(...) and receives all USDC
    assertEq(usdc.balanceOf(attacker), victimDeposit);
}
```

### Citations

**File:** contracts/IdleCreditVaultFactory.sol (L92-124)
```text
  function deployCreditVault(
    CreditVaultParams memory cvParams,
    StrategyData memory strategyData,
    AncillaryParams memory ancillaryParams
  ) external {
    _checkMinimumFees(cvParams);
    address manager = strategyData.manager;
    if (manager == address(0)) revert Is0();

    (IdleCDOEpochVariant cv, IdleCreditVault strategy) =
      _deployBaseCreditVault(strategyData, cvParams);

    address keyringWhitelist = _deployKeyring(ancillaryParams);
    _configureCreditVault(cv, strategy, cvParams, keyringWhitelist, manager);

    IdleCDOEpochQueue queue = _deployQueue(ancillaryParams, cv, keyringWhitelist);
    IdleCreditVaultWriteOffEscrow writeOffEscrow =
      _deployWriteOffEscrow(ancillaryParams.writeOffImplementation, cv, treasury);

    _finalizeKeyringAdmin(keyringWhitelist, manager);
    // Transfer ownership of strategy and credit vault to treasury
    strategy.transferOwnership(treasury);
    cv.transferOwnership(treasury);

    emit CreditVaultDeployed(
      address(cv),
      address(strategy),
      address(queue),
      address(0),
      keyringWhitelist,
      address(writeOffEscrow)
    );
  }
```

**File:** contracts/IdleCreditVaultFactory.sol (L168-173)
```text
  function _deployKeyring(AncillaryParams memory ancillaryParams) internal returns (address) {
    if (ancillaryParams.keyring == address(0)) return address(0);

    KeyringIdleWhitelist keyringWhitelist = new KeyringIdleWhitelist(ancillaryParams.keyring, address(this));
    return address(keyringWhitelist);
  }
```

**File:** contracts/IdleCreditVaultFactory.sol (L182-193)
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
```

**File:** contracts/IdleCreditVaultFactory.sol (L280-286)
```text
  function _deployProxy(address implementation, bytes memory data) internal returns (address) {
    return address(new TransparentUpgradeableProxy(
      implementation,
      proxyAdmin,
      data
    ));
  }
```
