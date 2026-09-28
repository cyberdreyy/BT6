### Title
Permissionless factory lets anyone deploy "verified-looking" credit vaults pointing at arbitrary attacker-controlled implementations - (File: contracts/IdleCreditVaultFactory.sol)

### Summary
`IdleCreditVaultFactory.deployCreditVault` and `deployRevolvingCreditVault` are callable by any EOA, and the implementation addresses for the credit vault (`cvParams.implementation`), the strategy (`strategyData.implementation`), the queue (`ancillaryParams.queueImplementation`), the write-off escrow (`ancillaryParams.writeOffImplementation`) and the programmable borrower (`programmableBorrowerParams.implementation`) are all supplied by the caller with no allowlist or validation. An attacker can therefore pass a malicious contract as the CDO/strategy implementation, have the factory deploy a `TransparentUpgradeableProxy` to it under the factory's own `proxyAdmin`, emit a `CreditVaultDeployed` event that is indistinguishable from a legitimate deployment, and steal 100% of any underlying tokens users deposit into that proxy. [1](#0-0) [2](#0-1) [3](#0-2) 

### Finding Description
The only gates in `deployCreditVault` are `_checkMinimumFees` (which only enforces `fees >= 5_000` or `managementFee >= 500`) and `manager != address(0)`. There is no check on `msg.sender` and no check that `cvParams.implementation` or `strategyData.implementation` are audited Idle contracts. `_deployBaseCreditVault` passes these addresses directly into `_deployProxy`, which constructs `new TransparentUpgradeableProxy(implementation, proxyAdmin, data)`. The initializer `data` is an `abi.encodeWithSelector(IdleCDOCreditVault.initialize.selector, ...)` call, but a malicious implementation only needs to expose functions matching the selectors the factory later calls (`initialize`, `setEpochParams`, `setInstantWithdrawParams`, `setKeyringParams`, `setFeeParams`, `setGuardian`, `transferOwnership`, `setAprs`, `setWhitelistedCDO` on the strategy) — all of which can be no-ops or trivially implemented in attacker bytecode. The post-deploy hardening (`strategy.transferOwnership(treasury)`, `cv.transferOwnership(treasury)`) is meaningless because the implementation itself is attacker code: ownership of the proxy state does not constrain what the implementation's `deposit`/`depositAA` logic does with user funds. Setting `manager`, `borrower`, and `feeReceiver` to honest-looking or even treasury addresses does not mitigate this — the theft happens inside the malicious implementation, not through a privileged role. The `CreditVaultDeployed` event emitted by the official factory then serves as the de-facto "registry" signal that indexers/UIs use to present the vault as legitimate. [4](#0-3) [5](#0-4) [6](#0-5) 

### Impact Explanation
Direct theft of user funds: any user who deposits `underlying` into the factory-deployed proxy (trusting the `CreditVaultDeployed` provenance) has their tokens routed by attacker-controlled logic — e.g., the malicious `depositAA`/`_deposit` simply transfers tokens to the attacker. Loss is 100% of deposits into the malicious vault. This violates the donation-isolation/solvency invariant trivially because no real vault accounting exists. No honest privileged role is required to misbehave: `treasury`, `manager`, `guardian`, and `borrower` can all be set to legitimate or even protocol-owned addresses and the attack still succeeds.

### Likelihood Explanation
Medium-low. Deployment is permissionless and costs only gas plus satisfying the minimum-fee check, so standing up a malicious vault is trivial. The exploit depends on attracting victims, but the attacker's vault carries the same on-chain provenance signal (`CreditVaultDeployed` from the canonical factory, correct `proxyAdmin`, treasury as `owner()`) as real vaults, so any UI, subgraph, or user heuristic that treats "deployed by the factory" as a trust signal is spoofable. This is the exact trust ambiguity the referenced bug class describes, realized in this codebase via unvalidated implementation pointers rather than a malicious owner.

### Recommendation
Maintain an on-chain allowlist (set by `treasury`) of approved implementation addresses per role (CDO variant, `IdleCreditVault` strategy, queue, write-off escrow, programmable borrower) and revert in `_deployProxy`/`_deployBaseCreditVault`/`_deployQueue`/`_deployWriteOffEscrow`/`_deployProgrammableBorrower` when a supplied implementation is not allowlisted. Alternatively gate `deployCreditVault`/`deployRevolvingCreditVault` behind an `onlyTreasury`/deployer role so the `CreditVaultDeployed` event remains a trustworthy registry signal. Until then, do not treat factory deployment events as proof of legitimacy in any UI or indexer.

### Proof of Concept
Foundry PoC sketch (fork or local):

```solidity
contract EvilCDO {
    address public owner_;
    IERC20 public token;
    address public attacker;
    // all selectors the factory calls:
    function initialize(uint256, address u, address, address, address, address, uint256) external {
        token = IERC20(u); attacker = msg.sender; // factory; attacker EOA stored off-chain
    }
    function setEpochParams(uint256,uint256) external {}
    function setInstantWithdrawParams(uint256,uint256,bool) external {}
    function setKeyringParams(address,uint256) external {}
    function setFeeParams(address,uint256,uint256,uint256) external {}
    function setIsDepositDuringEpochDisabled(bool) external {}
    function setGuardian(address) external {}
    function transferOwnership(address) external {}
    // victim entrypoint mimicking IdleCDO deposit
    function depositAA(uint256 amount) external {
        token.transferFrom(msg.sender, ATTACKER_EOA, amount); // theft
    }
}
contract EvilStrategy {
    function initialize(address,address,address,address,string memory,uint256) external {}
    function setAprs(uint256,uint256) external {}
    function setWhitelistedCDO(address) external {}
    function transferOwnership(address) external {}
}

function testFactoryDeploysMaliciousVault() public {
    // attacker EOA calls; no role needed
    vm.prank(attacker);
    factory.deployCreditVault(
        cvParams /* implementation = address(new EvilCDO()), underlying = USDC, fees >= 5000 */,
        strategyData /* implementation = address(new EvilStrategy()), manager = honestAddr, borrower = honestAddr */,
        ancillaryParams /* all zero */
    );
    // CreditVaultDeployed emitted; victim deposits USDC into cv proxy -> EvilCDO.depositAA steals it
}
```

Note: a purely local variant works without a fork; a mainnet-fork variant can reuse the deployed factory at `creditVaultFactoryV3` and a real USDC holder as victim for a fully reproducible PoC.

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

**File:** contracts/IdleCreditVaultFactory.sol (L175-208)
```text
  function _deployBaseCreditVault(
    StrategyData memory strategyData,
    CreditVaultParams memory cvParams
  ) internal returns (
    IdleCDOEpochVariant cv,
    IdleCreditVault strategy
  ) {
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
  }
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

**File:** contracts/IdleCreditVaultFactory.sol (L288-307)
```text
  function _configureCreditVault(
    IdleCDOEpochVariant cv,
    IdleCreditVault strategy,
    CreditVaultParams memory par,
    address keyringWhitelist,
    address manager
  ) internal {
    cv.setEpochParams(par.epochDuration, par.bufferPeriod);
    cv.setInstantWithdrawParams(par.instantWithdrawDelay, par.instantWithdrawAprDelta, par.disableInstantWithdraw);
    cv.setKeyringParams(keyringWhitelist, par.keyringPolicy);
    if (par.isInterestMinted) {
      cv.setIsInterestMinted(par.isInterestMinted);
    }
    cv.setIsDepositDuringEpochDisabled(par.isDepositDuringEpochDisabled);
    cv.setFeeParams(par.feeReceiver, par.fees, feeSplit, par.managementFee);
    cv.setGuardian(manager);
    // setAprs should be done before setWhitelistedCDO
    strategy.setAprs(par.apr, par.apr * (par.epochDuration + par.bufferPeriod) / par.epochDuration);
    strategy.setWhitelistedCDO(address(cv));
  }
```

**File:** contracts/IdleCreditVaultFactory.sol (L309-311)
```text
  function _checkMinimumFees(CreditVaultParams memory cvParams) internal pure {
    if (cvParams.fees < MIN_PERFORMANCE_FEE && cvParams.managementFee < MIN_MANAGEMENT_FEE) revert FeesTooLow();
  }
```
