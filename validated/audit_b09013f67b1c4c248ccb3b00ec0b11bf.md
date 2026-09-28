### Title
Missing self-recipient validation in `DefaultDistributor.claim` can trap a claimant’s recovery payout - (File: `contracts/DefaultDistributor.sol`)

### Summary

`DefaultDistributor.claim` lets a tranche holder specify an arbitrary payout recipient. If the holder passes the distributor’s own address, the claim consumes all of the holder’s tranche tokens while sending the underlying payout back to the distributor, leaving no user-facing way to recover either asset. [1](#0-0) 

### Finding Description

`claim(address _to)` checks that claims are active, transfers the caller’s entire `trancheToken` balance to the distributor, and then transfers `trancheBal * rate / ONE_TRANCHE` underlying tokens to `_to`. [1](#0-0) 

There is no validation that `_to != address(this)`. When `_to` is the distributor, the underlying payout remains in the distributor while the caller’s claim has already been consumed through the tranche-token transfer. [2](#0-1) 

The contract exposes no user-accessible accounting or recovery path for that consumed claim. Only the owner can move the stranded assets through the discretionary `transferToken` emergency function. [3](#0-2) 

### Impact Explanation

A tranche holder who supplies the distributor as `_to` permanently loses control of the claimed tranche tokens and does not receive the underlying payout. For a holder with `X` tranche tokens, the quantified loss is up to `X` tranche tokens plus the corresponding `X * rate / 1e18` underlying payout, subject to owner-assisted recovery. [1](#0-0) 

The payout is not allocated to a recoverable user balance. It remains as generic contract balance, and the claimant cannot retrieve it without the honest owner invoking `transferToken`. [3](#0-2) 

### Likelihood Explanation

Likelihood is low because exploitation requires the claimant to explicitly pass the distributor address as the payout recipient. No privileged role is required, but the issue primarily creates self-inflicted loss through an unvalidated recipient rather than allowing a third party to steal claims. [1](#0-0) 

### Recommendation

Add an explicit recipient check before transferring the claimant’s tranche tokens:

```solidity
require(_to != address(this), '!SELF');
```

This preserves normal recipient flexibility while preventing a successful claim from consuming tranche tokens and returning the payout to the same contract. [1](#0-0) 

### Proof of Concept

The following Foundry test demonstrates that a holder’s full tranche balance is consumed while the underlying payout remains locked in the distributor.

```solidity
// SPDX-License-Identifier: AGPL-3.0
pragma solidity 0.8.10;

import "forge-std/Test.sol";
import "../../contracts/DefaultDistributor.sol";
import "../../contracts/mocks/MockERC20.sol";

contract DefaultDistributorSelfClaimTest is Test {
    uint256 internal constant CLAIM_TRANCHES = 100e18;
    uint256 internal constant RECOVERY_FUNDS = 100e18;

    MockERC20 internal underlying;
    MockERC20 internal tranche;
    DefaultDistributor internal distributor;

    address internal owner = makeAddr("owner");
    address internal holder = makeAddr("holder");

    function setUp() public {
        // Fork context used by the repository's Foundry tests.
        vm.createSelectFork(vm.envString("MAINNET_RPC_URL"), 18_678_289);

        underlying = new MockERC20("Underlying", "UNDERLYING");
        tranche = new MockERC20("Tranche", "TRANCHE");

        distributor = new DefaultDistributor(
            address(underlying),
            address(tranche),
            owner
        );

        tranche.mint(holder, CLAIM_TRANCHES);
        underlying.mint(address(distributor), RECOVERY_FUNDS);

        vm.prank(owner);
        distributor.setIsActive(true);
    }

    function testClaimToDistributorConsumesClaimWithoutPayingHolder() public {
        vm.startPrank(holder);
        tranche.approve(address(distributor), CLAIM_TRANCHES);

        distributor.claim(address(distributor));
        vm.stopPrank();

        // The holder's entire tranche balance and claim were consumed.
        assertEq(tranche.balanceOf(holder), 0);
        assertEq(tranche.balanceOf(address(distributor)), CLAIM_TRANCHES);

        // The calculated payout went to the distributor itself.
        assertEq(underlying.balanceOf(holder), 0);
        assertEq(underlying.balanceOf(address(distributor)), RECOVERY_FUNDS);

        // Repeating the claim cannot recover anything because the receipt is gone.
        vm.prank(holder);
        distributor.claim(holder);
        assertEq(underlying.balanceOf(holder), 0);
    }
}
```

### Citations

**File:** contracts/DefaultDistributor.sol (L35-40)
```text
  function claim(address _to) external {
    require(isActive, '!ACTIVE');
    IERC20 tranche = IERC20(trancheToken);
    uint256 trancheBal = tranche.balanceOf(msg.sender);
    tranche.safeTransferFrom(msg.sender, address(this), trancheBal);
    IERC20(token).safeTransfer(_to, trancheBal * rate / ONE_TRANCHE);
```

**File:** contracts/DefaultDistributor.sol (L57-60)
```text
  function transferToken(address _token, address _to, uint256 _value) external {
    require(owner() == msg.sender, '!AUTH');
    IERC20(_token).safeTransfer(_to, _value);
  }
```
