### Title
Accidental underlying deposits are captured in the default recovery rate - (File: contracts/DefaultDistributor.sol)

### Summary

`DefaultDistributor` derives the fixed redemption rate from its entire underlying-token balance when claims are activated. An underlying amount accidentally transferred to the distributor before activation is therefore treated as default-recovery funds and distributed to every tranche holder who calls `claim`. [1](#0-0) 

### Finding Description

`setIsActive(true)` calculates `rate` as `token.balanceOf(address(this)) * ONE_TRANCHE / trancheToken.totalSupply()`. [1](#0-0) 

There is no distinction between the intended recovery deposit and unsolicited underlying tokens already held by the contract. [2](#0-1) 

After activation, `claim` transfers all of the caller's tranche tokens to the distributor and pays `trancheBal * rate / ONE_TRANCHE` underlying tokens. [3](#0-2) 

Accordingly, if intended recovery funds `R` are present and a user accidentally transfers `D`, activation sets the claim basis to `R + D`; a tranche holder controlling fraction `f` of the tranche supply receives `f * D` more than intended.

### Impact Explanation

The accidental sender permanently loses `D`, while tranche holders collectively receive `D` as excess claim proceeds.

A holder controlling the full tranche supply can recover the complete donation; multiple holders split the donation pro rata according to their claimed tranche balances. [3](#0-2) 

The broken invariant is donation isolation: unsolicited underlying transfers change claimant payouts even though they were never deposited as recovery assets.

### Likelihood Explanation

The sequence requires an underlying transfer to arrive after the distributor is created or funded but before the honest owner activates claims.

That ordering is externally reachable because activation reads the live balance rather than an immutable recovery amount recorded when the distributor was funded. [1](#0-0) 

The existing owner-only `transferToken` rescue does not prevent the loss because the donation can be crystallized into `rate` as soon as `setIsActive(true)` executes. [4](#0-3) 

### Recommendation

Do not calculate `rate` from the raw contract balance at activation.

Store the intended recovery amount when funding is performed, or require the owner to pass an `expectedUnderlyingAmount` to `setIsActive` and transfer any excess balance to a rescue recipient before setting the rate.

Alternatively, atomically fund and activate the distributor, or track a `recoveryBasis` separately so later accidental transfers remain claimable only through an explicit rescue function.

### Proof of Concept

```solidity
// test/foundry/DefaultDistributorDonation.t.sol
// SPDX-License-Identifier: UNLICENSED
pragma solidity 0.8.10;

import "forge-foundry/test.sol";
import {DefaultDistributor} from "contracts/DefaultDistributor.sol";
import {ERC20} from "@openzeppelin/contracts/token/ERC20/ERC20.sol";

contract TestToken is ERC20 {
    constructor() ERC20("Test", "TEST") {}

    function mint(address to, uint256 amount) external {
        _mint(to, amount);
    }
}

contract DefaultDistributorDonationTest is Test {
    TestToken internal underlying;
    TestToken internal tranche;
    DefaultDistributor internal distributor;

    address internal owner = address(0x100);
    address internal accidentalSender = address(0x200);
    address internal attacker = address(0x300);

    function testDonationIncludedInRecoveryRate() public {
        vm.createSelectFork(vm.envString("ETH_RPC_URL"));

        underlying = new TestToken();
        tranche = new TestToken();
        distributor = new DefaultDistributor(
            address(underlying),
            address(tranche),
            owner
        );

        uint256 intendedRecovery = 100 ether;
        uint256 donation = 10 ether;

        // Legitimate recovery funding and an accidental pre-activation transfer.
        underlying.mint(address(distributor), intendedRecovery);
        underlying.mint(accidentalSender, donation);
        vm.prank(accidentalSender);
        underlying.transfer(address(distributor), donation);

        // The attacker controls all outstanding tranche supply for the maximal case.
        tranche.mint(attacker, 1 ether);

        // Honest owner activates claims after the accidental transfer.
        vm.prank(owner);
        distributor.setIsActive(true);

        assertEq(distributor.rate(), 110 ether / 1 ether);

        vm.startPrank(attacker);
        tranche.approve(address(distributor), 1 ether);
        distributor.claim(attacker);
        vm.stopPrank();

        // The attacker receives both the intended recovery and the mistaken deposit.
        assertEq(underlying.balanceOf(attacker), intendedRecovery + donation);
        assertEq(underlying.balanceOf(address(distributor)), 0);
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

**File:** contracts/DefaultDistributor.sol (L45-50)
```text
  function setIsActive(bool _active) external {
    require(owner() == msg.sender, '!AUTH');
    isActive = _active;
    if (_active) {
      rate = IERC20(token).balanceOf(address(this)) * ONE_TRANCHE / IERC20(trancheToken).totalSupply();
    }
```

**File:** contracts/DefaultDistributor.sol (L53-60)
```text
  /// @notice Emergency method, tokens gets transferred out 
  /// @param _token address
  /// @param _to recipient
  /// @param _value amount to transfer
  function transferToken(address _token, address _to, uint256 _value) external {
    require(owner() == msg.sender, '!AUTH');
    IERC20(_token).safeTransfer(_to, _value);
  }
```
