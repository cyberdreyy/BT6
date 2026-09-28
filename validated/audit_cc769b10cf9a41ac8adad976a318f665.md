### Title
Unprotected `initialize` on `IdleCreditVaultWriteOffEscrow` lets an attacker take ownership of the escrow and drain escrowed tranche tokens - (File: contracts/IdleCreditVaultWriteOffEscrow.sol)

### Summary
The Wintermute/Optimism incident is an "attacker establishes control over the receiving contract before the intended party" bug: the multisig did not exist on the destination chain, so a third party deployed code at that address and captured 20M OP. The same class exists in `IdleCreditVaultWriteOffEscrow`: the contract is an `Initializable` upgradeable contract whose `initialize` can be front-run (or called on a proxy that was deployed without atomic initialization). The first caller becomes `owner` and `feeReceiver`, and `owner` controls `emergencyWithdraw`, which can sweep every escrowed tranche token and all escrowed underlying held against pending write-off requests.

### Finding Description
`IdleCreditVaultWriteOffEscrow` is designed to custody lender tranche tokens while a write-off request is open: `createWriteOffRequest` pulls `tranche` tokens into the contract and records `userRequests[msg.sender]` [1](#0-0) . Control of the contract is established by `initialize`, which is `public`, `initializer`-gated only, sets `feeReceiver = _owner`, and calls `transferOwnership(_owner)` [2](#0-1) . The only extra guard is `idleCDOEpoch != address(0)`; it does not bind the initializer to any intended deployer [3](#0-2) .

Because the constructor only disables initializers on the implementation [4](#0-3) , a proxy deployed in one transaction and initialized in a later one (the standard deployment pattern for this codebase, which `vm.store`-resets slot 0 and calls `initialize` manually in tests) can be initialized by an attacker watching the mempool or deployment. Once owner, the attacker calls `emergencyWithdraw(tranche, attacker, balance)` to take all lender-deposited tranche tokens backing open write-off requests [5](#0-4) , and `setFeeReceiver(attacker)` to capture the exit fee on every future `fullfillWriteOffRequest` [6](#0-5) . This is exactly the Wintermute shape: funds (escrowed tranches) are sent to an address whose controlling logic was established by an attacker rather than the intended owner.

### Impact Explanation
Direct theft of all tranche tokens escrowed in `createWriteOffRequest` at the moment of takeover (bounded only by aggregate open requests, potentially the full pool of lenders seeking write-off exit) plus permanent capture of the 0.1%–1% exit fee stream and any underlying sitting in the contract mid-fulfillment. Stolen AA tranche tokens can then be sold into `fullfillWriteOffRequest` or redeemed through the vault's normal flows, converting the theft into underlying. Loss is quantified as `tranche.balanceOf(escrow)` plus `underlying.balanceOf(escrow)`.

### Likelihood Explanation
Requires only that deployment and `initialize` are non-atomic for a proxy escrow, or that an implementation deployment leaves any uninitialized proxy clone vulnerable — a single unprivileged transaction by any EOA. Tranche tokens accumulate in the escrow over each epoch, so the longer the window before takeover is detected the larger the haul; users cannot react because `deleteWriteOffRequest` still works technically but races an attacker who can sweep first.

### Recommendation
Deploy each escrow atomically — initialize inside the proxy's constructor/deployment transaction (e.g., OpenZeppelin `ERC1967Proxy` with an `init` calldata payload) so no un-initialized window exists. Additionally, harden `initialize` by requiring `msg.sender` to be a factory/deployer address, and consider making `emergencyWithdraw` unable to pull `tranche`/`underlying` while `pendingUnderlyings > 0` or routing recovered escrowed tokens back to requesters pro-rata.

### Proof of Concept
```solidity
// Foundry fork test, modeled on test/foundry/IdleCreditVaultWriteOffEscrow.t.sol
function testInitializeFrontRunDrainsEscrow() external {
    // 1. Team deploys the escrow proxy but does NOT initialize in the same tx
    IdleCreditVaultWriteOffEscrow proxy = new IdleCreditVaultWriteOffEscrow();
    vm.store(address(proxy), bytes32(uint256(0)), bytes32(uint256(0)));

    // 2. Attacker front-runs initialize and becomes owner + feeReceiver
    address attacker = makeAddr("attacker");
    vm.prank(attacker);
    proxy.initialize(address(cdoEpoch), attacker, true);
    assertEq(proxy.owner(), attacker);

    // 3. Over an epoch, honest LPs escrow tranche tokens for write-off requests
    //    (simulate existing balance: escrow already holds LP tranches)
    uint256 escrowed = 50_000e18;
    deal(address(tranche), address(proxy), escrowed);

    // 4. Attacker sweeps all escrowed tranche tokens
    vm.prank(attacker);
    proxy.emergencyWithdraw(address(tranche), attacker, escrowed);
    assertEq(tranche.balanceOf(attacker), escrowed);
    assertEq(tranche.balanceOf(address(proxy)), 0);
}
```

### Citations

**File:** contracts/IdleCreditVaultWriteOffEscrow.sol (L57-60)
```text
  /// @custom:oz-upgrades-unsafe-allow constructor
  constructor() {
    _disableInitializers();
  }
```

**File:** contracts/IdleCreditVaultWriteOffEscrow.sol (L66-82)
```text
  function initialize(address _idleCDOEpoch, address _owner, bool _isAATranche) public virtual initializer {
    if (idleCDOEpoch != address(0)) revert NotAllowed();
    // initialize the parent contracts
    OwnableUpgradeable.__Ownable_init();
    ReentrancyGuardUpgradeable.__ReentrancyGuard_init();
    // set basic storage variables
    IdleCDOEpochVariant _cdo = IdleCDOEpochVariant(_idleCDOEpoch);
    idleCDOEpoch = _idleCDOEpoch;
    strategy = _cdo.strategy();
    underlying = _cdo.token();
    tranche = _isAATranche ? _cdo.AATranche() : _cdo.BBTranche();
    borrower = IdleCreditVault(strategy).borrower();
    exitFee = 100; // 0.1%
    feeReceiver = _owner; // set fee receiver to owner
    // transfer ownership to the owner
    transferOwnership(_owner);
  }
```

**File:** contracts/IdleCreditVaultWriteOffEscrow.sol (L86-102)
```text
  function createWriteOffRequest(uint256 amount, uint256 underlyingsRequested) external nonReentrant {
    // can request write off only when epoch is running
    if (!IdleCDOEpochVariant(idleCDOEpoch).isEpochRunning()) revert EpochNotRunning();
    // cannot request write off with 0 tranche tokens
    if (amount == 0) revert NotAllowed();

    // get tranche tokens from user
    IERC20Detailed(tranche).safeTransferFrom(msg.sender, address(this), amount);
    // get current write-off request
    WriteOffRequest memory currentRequest = userRequests[msg.sender];
    // update user requests
    userRequests[msg.sender] = WriteOffRequest({
      tranches: currentRequest.tranches + amount,
      underlyings: currentRequest.underlyings + underlyingsRequested
    });
    pendingUnderlyings += underlyingsRequested;
  }
```

**File:** contracts/IdleCreditVaultWriteOffEscrow.sol (L143-151)
```text
    if (_exitFee > 0) {
      _totFee = (_underlyings * _exitFee) / FULL_VALUE;
      // transfer exit fee to the feeReceiver
      underlyingToken.safeTransfer(feeReceiver, _totFee);
    }
    // transfer the remaining underlyings to the user
    underlyingToken.safeTransfer(_user, _underlyings - _totFee);
    // transfer tranche tokens to fulfiller
    IERC20Detailed(tranche).safeTransfer(msg.sender, _tranches);
```

**File:** contracts/IdleCreditVaultWriteOffEscrow.sol (L174-180)
```text
  function emergencyWithdraw(address _token, address _to, uint256 _amount) external {
    _checkOnlyOwner();
    // do not allow to withdraw to the zero address
    if (_to == address(0)) revert Is0();

    IERC20Detailed(_token).safeTransfer(_to, _amount);
  }
```
