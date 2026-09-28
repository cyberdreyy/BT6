### Title
Write-off cancellation can be front-run by fulfilling the stale escrow order - ([File: contracts/IdleCreditVaultWriteOffEscrow.sol](contracts/IdleCreditVaultWriteOffEscrow.sol))

### Summary

`IdleCreditVaultWriteOffEscrow` lets a tranche holder create a cancellable sell order for escrowed tranche tokens, but lets any unprivileged fulfiller execute that order until the cancellation transaction is mined. [1](#0-0)  Because `fullfillWriteOffRequest` deletes the request before atomically paying the seller and transferring the tranche tokens, a fulfiller can observe a pending `deleteWriteOffRequest` transaction and win the race. [2](#0-1) 

### Finding Description

During a running epoch, a lender calls `createWriteOffRequest`, deposits `amount` tranche tokens, and records `underlyingsRequested`. [3](#0-2)  The order remains fulfillable even after the epoch ends because `fullfillWriteOffRequest` has no `isEpochRunning`, expiry, cancellation-pending, or other phase check. [4](#0-3) 

If tranche value later exceeds the stale ask, the lender calls `deleteWriteOffRequest`. [5](#0-4)  An attacker sees that transaction in the mempool and submits `fullfillWriteOffRequest(lender, tranches, underlyings)` first. The attacker only needs to supply at least the stored `underlyings`, after which the request is deleted, the attacker receives all escrowed tranches, and the lender receives the stale amount minus `exitFee`. [6](#0-5) 

The subsequent cancellation reverts with `Is0` because the attacker already deleted `userRequests[lender]`. [7](#0-6) 

### Impact Explanation

The lender permanently loses tranche tokens at a stale price despite broadcasting cancellation first in real time. The quantifiable loss is:

```text
sellerLoss = tranches * currentTranchePrice / 1e18
           - (underlyingsRequested * (FULL_VALUE - exitFee) / FULL_VALUE)
```

The attacker’s profit is the same NAV spread before liquidation costs. For example, if `10_000e18` tranches were listed for `10_000` underlying and their protocol value later rises to `11_000`, the attacker pays `10_000`, receives tranche tokens worth approximately `11_000`, and the seller receives only `9_990` at the default `0.1%` exit fee. [8](#0-7) 

### Likelihood Explanation

Likelihood depends on public-mempool visibility and a stale order becoming profitable. No privileged role is required: fulfillment is explicitly available to any wallet, and a write-off fulfiller is an in-scope attacker. [9](#0-8)  Successful epoch interest, NAV changes, or a lender repricing attempt can all make an outstanding ask economically unfavorable.

### Recommendation

Make requests expire automatically or implement a two-step cancellation:

```solidity
cancelWriteOffRequest();          // sets cancellation timestamp, blocks fulfillment
finalizeCancelWriteOffRequest();  // withdraws tranches after a short delay
```

`fullfillWriteOffRequest` should revert when `cancelRequestedAt[user] != 0` or when `block.timestamp > expiresAt[user]`. A nonce or price-deadline field would also let the lender invalidate stale terms without relying on transaction ordering.

### Proof of Concept

Add this test to `test/foundry/IdleCreditVaultWriteOffEscrow.t.sol`, which already forks the production CDO and deploys the escrow. [10](#0-9) 

```solidity
function testDeleteWriteOffRequestCanBeFrontRunByFulfill() external {
    address attacker = makeAddr("attacker");
    uint256 tranches = 10_000e18;
    uint256 asked = 10_000e6;

    // Lender creates the cancellable order while the epoch is running.
    vm.prank(LP);
    escrow.createWriteOffRequest(tranches, asked);

    // Let the epoch settle successfully so the AA price rises above 1.
    // Existing test helper funds and repays the borrower, warps past
    // epochEndDate, and calls manager.stopEpoch.
    _stopCurrentEpoch();

    uint256 currentPrice = cdoEpoch.virtualPrice(address(tranche));
    uint256 trancheValue = tranches * currentPrice / 1e18;
    assertGt(trancheValue, asked, "order must be stale and profitable");

    // Attacker sees LP's pending deleteWriteOffRequest and fulfills first.
    deal(address(underlying), attacker, asked);
    vm.startPrank(attacker);
    underlying.approve(address(escrow), asked);
    escrow.fullfillWriteOffRequest(LP, tranches, asked);
    vm.stopPrank();

    // LP's cancellation now reverts because the request was consumed.
    vm.expectRevert(abi.encodeWithSelector(Is0.selector));
    vm.prank(LP);
    escrow.deleteWriteOffRequest();

    uint256 fee = asked * escrow.exitFee() / escrow.FULL_VALUE();
    assertEq(tranche.balanceOf(attacker), tranches);
    assertGt(
        trancheValue - (asked - fee),
        0,
        "seller permanently lost the NAV spread"
    );
}
```

The test demonstrates the broken invariant directly: one cancellation attempt does not guarantee reclamation because an unprivileged fulfiller can consume the escrowed position first.

### Citations

**File:** contracts/IdleCreditVaultWriteOffEscrow.sol (L86-116)
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
```

**File:** contracts/IdleCreditVaultWriteOffEscrow.sol (L118-151)
```text
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

**File:** test/foundry/IdleCreditVaultWriteOffEscrow.t.sol (L32-63)
```text
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
  }
```
