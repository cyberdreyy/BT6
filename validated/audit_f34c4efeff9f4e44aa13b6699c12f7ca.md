### Title
ETH sent to `withdrawAA`/`withdrawBB` in `IdleCDOEthenaVariant` is permanently locked in the per-request `EthenaCooldownRequest` clone - (File: contracts/IdleCDOEthenaVariant.sol)

### Summary
The withdrawal path of the Ethena CDO variant accepts `msg.value` and forwards the entire ETH amount into a freshly deployed `EthenaCooldownRequest` clone via `ClonesWithImmutableArgs.clone`. The clone has no ETH rescue/withdrawal path (its `rescue()` only handles ERC20 tokens and is restricted to the TL multisig anyway), so any ETH sent with a withdraw call is irrecoverably locked. This is the same bug class as the reported issue: a payable function accepts ETH without validating that the full `msg.value` is needed, and no mechanism returns the excess.

### Finding Description
`IdleCDOEthenaVariant._withdraw` handles the unstaking cooldown flow by cloning `cooldownImpl` with `ClonesWithImmutableArgs.clone`:

```solidity
// contracts/IdleCDOEthenaVariant.sol:82-85
EthenaCooldownRequest clone = EthenaCooldownRequest(cooldownImpl.clone(
    abi.encodePacked(address(this), msg.sender),
    msg.value
));
```

The `clone` overload taking a third `uint256` argument deploys the clone and forwards that ETH value to it (see `clones-with-immutable-args` library semantics). The clone contract exposes only three functions:

```solidity
// contracts/strategies/ethena/EthenaCooldownRequest.sol:15-30
function startCooldown() external { ... cooldownShares(...) }
function unstake() external { ... }                    // sUSDe -> user
function rescue(address _token) external {             // ERC20 only, TL_MULTISIG only
    require(msg.sender == TL_MULTISIG, '6');
    IERC20Detailed(_token).transfer(msg.sender, IERC20Detailed(_token).balanceOf(address(this)));
}
```

Broken invariant:

1. The clone has no `receive`/`fallback` refund logic and no native-ETH rescue function. `rescue()` calls `IERC20Detailed(_token).transfer`, which cannot move native ETH (calling it on `address(0)` or WETH does not touch the ETH balance). Once ETH lands in the clone, there is no code path that can ever move it out.
2. Nothing in `_withdraw` requires ETH for the withdrawal to succeed — the cooldown is driven entirely by sUSDe (`IERC20Detailed(strategyToken).safeTransfer(address(clone), SUSDeRedeemed)` and `clone.startCooldown()`). The forwarded `msg.value` is not consumed, split, validated, or returned.
3. There is no `require(msg.value == 0)` or refund of excess ETH anywhere in `_withdraw` or in the external `withdrawAA`/`withdrawBB` entry points that reach it.

Compare with the analogous payable surface `LidoCDOTrancheGateway`: there `msg.value` is fully consumed by `IStETH.submit{value: _ethAmount}` and its `receive()` rejects non-WETH senders, so no ETH can linger. In the Ethena variant, by contrast, the ETH is deliberately pushed into a contract designed to hold it forever.

### Impact Explanation
Any ETH sent along with a withdraw call on the Ethena CDO is permanently locked in a single-use cooldown clone:

- **Self-inflicted permanent loss:** wallets/front-ends bundling ETH (e.g., a user who intends to pay for gas via `msg.value`, or a multicall/router that attaches leftover ETH) lose 100% of it. The clone is created per withdrawal, so each such transaction locks ETH in a fresh unrecoverable address.
- **No refund for partial needs:** even if the design intent were "user pre-funds clone gas," there is no check `amount == msg.value` and no return of `msg.value - consumed`, so any excess is frozen — exactly the reported bug class.
- **Value accrues nowhere:** unlike dust/donation bugs that merely distort share pricing, locked ETH is a dead loss; `unstake()` only moves sUSDe to the user and `rescue()` only moves ERC20 to the multisig.

The invariant broken is "no contract should retain user value it cannot return": the gateway contract's own comment ("This contract should not have any funds at the end of each tx") shows the intended invariant, which the Ethena clone violates by construction.

### Likelihood Explanation
Likelihood is moderate:

- The trigger requires a `payable` entry point reaching `_withdraw` while carrying nonzero `msg.value`. Because `_withdraw` forwards `msg.value`, the external `withdrawAA`/`withdrawBB` on this variant must accept ETH (otherwise the `msg.value` forwarding is dead code and the forwarding itself is a vestigial risk).
- Any EOA or contract withdrawing while attaching ETH — common in multicall wallets, batching routers, or users replicating payable-deposit patterns — hits the loss. No privileged role, timing, or epoch state is needed; it works in the normal running phase.
- The attacker-EOA framing maps directly to "unprivileged user interaction causes permanent freezing of funds," which the acceptance criteria allow ("permanent freezing" with a quantified loss = full `msg.value`).

### Recommendation
Remove the ETH forwarding from the clone creation and reject ETH on the withdraw entry points:

```solidity
// contracts/IdleCDOEthenaVariant.sol
require(msg.value == 0, "no-eth");
EthenaCooldownRequest clone = EthenaCooldownRequest(cooldownImpl.clone(
    abi.encodePacked(address(this), msg.sender)
));
```

If funding the clone is intentional, compute the required amount, forward exactly that, and refund `msg.value - required` to `msg.sender`. Additionally, either give `EthenaCooldownRequest` an ETH sweep in `rescue()` (e.g., `payable(msg.sender).transfer(address(this).balance)` when `_token == address(0)`) or a `receive()`-less design so ETH cannot accrue. Note this does not recover ETH already locked in existing clones — those are permanently frozen unless the multisig deploys a rescue path.

### Proof of Concept
Foundry fork test (mainnet, against `IdleCDOEthenaVariant` holding sUSDe — the test assumes `withdrawAA`/`withdrawBB` on this variant are payable, consistent with the `msg.value` forwarding at line 84):

```solidity
// SPDX-License-Identifier: UNLICENSED
pragma solidity 0.8.10;

import "forge-std/Test.sol";

interface IIdleCDOEthenaVariant {
    function withdrawAA(uint256 amount) external payable returns (uint256);
    function AATranche() external view returns (address);
}
interface IERC20 { function balanceOf(address) external view returns (uint256); }

contract ExcessEthLockedTest is Test {
    IIdleCDOEthenaVariant cdo = IIdleCDOEthenaVariant(CDO_ADDRESS);
    IERC20 aaTranche = IERC20(cdo.AATranche());
    address user = trancheHolder; // any EOA holding AA tranche tokens

    function test_ExcessEthLockedInClone() public {
        uint256 amt = aaTranche.balanceOf(user);
        vm.deal(user, 1 ether);

        vm.recordLogs();
        vm.prank(user);
        // withdraw while attaching ETH; user expects it back or consumed
        cdo.withdrawAA{value: 1 ether}(amt);

        // parse NewCooldownRequestContract event to get clone address
        Vm.Log[] memory logs = vm.getRecordedLogs();
        address clone;
        for (uint i; i < logs.length; i++) {
            if (logs[i].topics[0] == keccak256("NewCooldownRequestContract(address,address,uint256)")) {
                clone = address(uint160(uint256(logs[i].topics[1])));
            }
        }

        // ETH was forwarded to the per-request clone
        assertEq(clone.balance, 1 ether);
        assertEq(user.balance, 0);

        // No recovery path: rescue() only handles ERC20 and is multisig-gated
        vm.prank(TL_MULTISIG);
        vm.expectRevert(); // _token = address(0) -> IERC20 call reverts
        EthenaCooldownRequest(payable(clone)).rescue(address(0));

        // ETH remains locked forever
        assertEq(clone.balance, 1 ether);
    }
}
```

Steps: an unprivileged tranche holder calls `withdrawAA{value: 1 ether}`. The sUSDe cooldown proceeds normally (the ETH is not needed), but the full 1 ETH is deposited into the clone. Attempts to recover fail: `rescue` is ERC20-only and multisig-gated, and `unstake` only moves sUSDe. Loss = entire `msg.value` per affected withdrawal, permanently frozen.

Caveat: if the deployed external withdraw functions are non-payable, `msg.value` can never be nonzero and the impact reduces to a latent/dead-code risk; the payable entry is implied by the explicit `msg.value` forwarding but could not be confirmed via `IdleCDO.sol` signature inspection within the index.