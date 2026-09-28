### Title
Zero-priced write-off requests allow permissionless seizure of escrowed tranche tokens - ([File: contracts/IdleCreditVaultWriteOffEscrow.sol](contracts/IdleCreditVaultWriteOffEscrow.sol))

### Summary
`createWriteOffRequest` requires a nonzero tranche deposit but does not require a nonzero `underlyingsRequested`, so a lender can create a request that offers valuable tranche tokens for zero underlying assets. [1](#0-0)  Any wallet can then fulfill that request by passing zero `_underlyings`, receive all escrowed tranche tokens, and pay nothing because the fulfillment check only rejects `_underlyings < currentRequest.underlyings`. [2](#0-1) 

### Finding Description
The escrow is intended to exchange lender tranche tokens for underlying assets supplied by a fulfiller. [3](#0-2)  During creation, the contract checks only that `amount != 0`, then escrows `amount` tranche tokens and records `underlyingsRequested` without validating that the requested quote is economically meaningful. [4](#0-3)  Fulfillment requires the exact stored tranche amount and merely requires `_underlyings >= currentRequest.underlyings`; when the stored quote is zero, `_underlyings = 0` passes validation. [5](#0-4)  The function then deletes the request and transfers the escrowed tranche tokens to the fulfiller. [6](#0-5) 

This is analogous to accepting an all-space password: the request technically satisfies the only explicit validation, but it fails the unstated economic requirement that a sale must demand a nonzero payment. The tranche tokens represent a claim on vault NAV whose value is exposed through `tranchePrice` and `virtualPrice`. [7](#0-6) [8](#0-7) 

### Impact Explanation
An unprivileged attacker can monitor `createWriteOffRequest` transactions and immediately call `fullfillWriteOffRequest` after the transaction succeeds, acquiring the victim's escrowed tranche tokens for zero underlying. [2](#0-1)  The loss equals the market/redemption value of the deposited tranche tokens, less whatever value the attacker can subsequently extract through the vault's normal tranche mechanisms. For example, a request depositing `10_000e18` AA tranche tokens can be fulfilled with zero USDC, transferring the entire `10_000e18` position to the attacker while the victim receives nothing. [4](#0-3) 

The victim can delete the request before fulfillment, but no privilege is needed to front-run or immediately fulfill it, and no owner, borrower, manager, or keyring approval is involved in `fullfillWriteOffRequest`. [9](#0-8) 

### Likelihood Explanation
The attack requires a lender to submit a request with `underlyingsRequested == 0`, such as through malformed calldata, an integration bug, or a mistaken zero-valued parameter. [4](#0-3)  Creation is only possible while an epoch is running, and fulfillment is explicitly callable by any wallet, so the exploitation path itself is simple and permissionless. [10](#0-9) [11](#0-10) 

### Recommendation
Reject zero-priced requests at creation time by requiring `underlyingsRequested > 0`, ideally alongside a defensible minimum payment relative to the deposited tranche amount or current `virtualPrice`. `createWriteOffRequest` should enforce this before transferring tranche tokens, and `fullfillWriteOffRequest` should additionally reject `currentRequest.underlyings == 0` to protect requests created by older implementations. [4](#0-3) [12](#0-11) 

### Proof of Concept
The following Foundry test can be added to `test/foundry/IdleCreditVaultWriteOffEscrow.t.sol`, using the existing mainnet-fork setup in which `LP` holds AA tranche tokens and the escrow is initialized against `cdoEpoch`. [13](#0-12) 

```solidity
function testZeroPriceRequestLetsAnyWalletTakeTranches() external {
  uint256 trancheAmount = 10_000 * ONE_TRANCHE;
  address attacker = makeAddr("zeroPriceBuyer");

  uint256 sellerTranchesBefore = tranche.balanceOf(LP);
  uint256 attackerTranchesBefore = tranche.balanceOf(attacker);

  // Victim deposits valuable tranche tokens but requests zero underlying.
  vm.prank(LP);
  escrow.createWriteOffRequest(trancheAmount, 0);

  (uint256 storedTranches, uint256 storedUnderlyings) = escrow.userRequests(LP);
  assertEq(storedTranches, trancheAmount);
  assertEq(storedUnderlyings, 0);

  // Any wallet can fulfill the zero-priced order without approving or paying USDC.
  vm.prank(attacker);
  escrow.fullfillWriteOffRequest(LP, trancheAmount, 0);

  assertEq(tranche.balanceOf(attacker) - attackerTranchesBefore, trancheAmount);
  assertEq(sellerTranchesBefore - tranche.balanceOf(LP), trancheAmount);
  assertEq(underlying.balanceOf(LP), 0);
  assertEq(underlying.balanceOf(attacker), 0);

  (storedTranches, storedUnderlyings) = escrow.userRequests(LP);
  assertEq(storedTranches, 0);
  assertEq(storedUnderlyings, 0);
}
```

### Citations

**File:** contracts/IdleCreditVaultWriteOffEscrow.sol (L17-21)
```text
/// @title IdleCreditVaultWriteOffEscrow
/// @dev Contract that collects write off requests of a credit vault deposit from a lender and underlyings from a fulfiller
/// and allow them to trustlessly exchange debt between lender and buyer. If the borrower fulfills the request, the borrower
/// will then be able to write off the debt by burning tranche tokens (via IdleCDOEpochVariant writeOffDeposit method)
contract IdleCreditVaultWriteOffEscrow is Initializable, OwnableUpgradeable, ReentrancyGuardUpgradeable {
```

**File:** contracts/IdleCreditVaultWriteOffEscrow.sol (L84-101)
```text
  /// @notice create a write-off request by depositing tranche tokens and setting the amount of underlyings requested
  /// @param amount of tranche tokens to deposit
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
```

**File:** contracts/IdleCreditVaultWriteOffEscrow.sol (L104-151)
```text
  /// @notice delete the write-off request and transfer tranche tokens back to the user
  function deleteWriteOffRequest() external nonReentrant {
    // get current write-off request
    WriteOffRequest memory currentRequest = userRequests[msg.sender];
    // check if the user has a write-off request
    if (currentRequest.tranches == 0) revert Is0();

    // Existing upgraded escrows can have legacy requests that were never added to pendingUnderlyings.
    pendingUnderlyings -= pendingUnderlyings >= currentRequest.underlyings ? currentRequest.underlyings : pendingUnderlyings;
    delete userRequests[msg.sender];
    // transfer tranche tokens back to the user
    IERC20Detailed(tranche).safeTransfer(msg.sender, currentRequest.tranches);
  }

  /// @notice fulfill the write-off request by buying the escrowed tranche tokens
  /// @param _user address of the user that made the write-off request
  /// @param _tranches amount of tranche tokens to transfer
  /// @param _underlyings amount of underlyings to transfer
  /// @dev this function can be called by any wallet
  function fullfillWriteOffRequest(address _user, uint256 _tranches, uint256 _underlyings) external nonReentrant {
    // get current write-off request
    WriteOffRequest memory currentRequest = userRequests[_user];
    // check if the user has a write-off request
    if (currentRequest.tranches == 0) revert Is0();
    // check if the request matches at least the expected values (borrower can choose to overpay if needed, but not underpay)
    if (currentRequest.tranches != _tranches || _underlyings < currentRequest.underlyings) {
      revert WrongRequest();
    }

    // Existing upgraded escrows can have legacy requests that were never added to pendingUnderlyings.
    pendingUnderlyings -= pendingUnderlyings >= currentRequest.underlyings ? currentRequest.underlyings : pendingUnderlyings;
    delete userRequests[_user];

    IERC20Detailed underlyingToken = IERC20Detailed(underlying);
    // transfer underlyings requested from the fulfiller to this contract
    underlyingToken.safeTransferFrom(msg.sender, address(this), _underlyings);
    // check if the exit fee is set and if so, apply it
    uint256 _exitFee = exitFee;
    uint256 _totFee;
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

**File:** contracts/IdleCDO.sol (L168-183)
```text
  /// @param _tranche tranche address
  /// @return tranche price, in underlyings, at the last interaction (not considering interest earned 
  /// since last interaction)
  function tranchePrice(address _tranche) external view returns (uint256) {
    return _tranchePrice(_tranche);
  }

  /// @notice calculates the current net TVL (in `token` terms)
  /// @dev unclaimed rewards (gov tokens) and `unclaimedFees` are not counted. 
  /// Harvested rewards are counted only if enough blocks have passed (`_lockedRewards`)
  function getContractValue() public override view returns (uint256) {
    address _strategyToken = strategyToken;
    // TVL is the sum of unlent balance in the contract + the balance in lending - harvested but locked rewards - unclaimedFees
    // Balance in lending is the value of the interest bearing assets (strategyTokens) in this contract
    // TVL = (strategyTokens * strategy token price) + unlent balance - lockedRewards - unclaimedFees
    return (_contractTokenBalance(_strategyToken) * _strategyPrice() / (10**(IERC20Detailed(_strategyToken).decimals()))) +
```

**File:** contracts/IdleCDO.sol (L204-219)
```text
  /// @notice calculates the current tranches price considering the interest/loss that is yet to be splitted
  /// ie the interest/loss generated since the last update of priceAA and priceBB (done on depositXX/withdrawXX/harvest)
  /// @param _tranche address of the requested tranche
  /// @return _virtualPrice tranche price considering all interest/losses
  function virtualPrice(address _tranche) public virtual view returns (uint256 _virtualPrice) {
    // get both NAVs, because we need the total NAV anyway
    uint256 _lastNAVAA = lastNAVAA;
    uint256 _lastNAVBB = lastNAVBB;

    (_virtualPrice, ) = _virtualPriceAux(
      _tranche,
      getContractValue(), // nav
      _lastNAVAA + _lastNAVBB, // lastNAV
      _lastSavedNAV(_tranche), // lastTrancheNAV
      trancheAPRSplitRatio
    );
```

**File:** test/foundry/IdleCreditVaultWriteOffEscrow.t.sol (L20-62)
```text
  IdleCDOEpochVariant public constant cdoEpoch = IdleCDOEpochVariant(0xf6223C567F21E33e859ED7A045773526E9E3c2D5);
  IdleCreditVaultWriteOffEscrow public escrow;
  IERC20Detailed public underlying;
  IERC20Detailed public tranche;
  IdleCreditVault public strategy;
  address public manager;
  address public borrower;
  // LP address
  address public constant LP = 0xA7780086ab732C110E9E71950B9Fb3cb2ea50D89;
  address public constant FASA = 0x7545CdbccD780DabAd6AdA8279D82E5ccfd4bF88;
  address public constant TL_MULTISIG = 0xFb3bD022D5DAcF95eE28a6B07825D4Ff9C5b3814;

  function setUp() public {
    vm.createSelectFork('mainnet', 23032567);

    // we deploy a new IdleCDOEpochVariant and IdleCreditVault contract used only to get the bytecode 
    // and etch at the same address of the original one so to enable console.log in the IdleCDOEpochVariant 
    // and new features not yet deployed on mainnet
    IdleCDOEpochVariant dummy = new IdleCDOEpochVariant();
    IdleCreditVault dummyStrategy = new IdleCreditVault();
    vm.etch(address(cdoEpoch), address(dummy).code);
    vm.etch(cdoEpoch.strategy(), address(dummyStrategy).code);

    escrow = new IdleCreditVaultWriteOffEscrow();
    // allow initialization of the escrow contract
    vm.store(address(escrow), bytes32(uint256(0)), bytes32(uint256(0)));
    escrow.initialize(address(cdoEpoch), TL_MULTISIG, true);

    underlying = IERC20Detailed(cdoEpoch.token());
    strategy = IdleCreditVault(cdoEpoch.strategy());
    manager = strategy.manager();
    borrower = strategy.borrower();
    tranche = IERC20Detailed(cdoEpoch.AATranche());

    // approve escrow contract to spend tranches tokens of address(this)
    tranche.approve(address(escrow), type(uint256).max);

    // allow everyone to deposit
    vm.prank(cdoEpoch.owner());
    cdoEpoch.setKeyringParams(address(0), 1);

    vm.prank(LP);
    tranche.approve(address(escrow), type(uint256).max);
```
