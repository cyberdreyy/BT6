### Title
ETH sent with Ethena-variant withdrawals is permanently trapped in the ephemeral cooldown clone - ([File: contracts/IdleCDOEthenaVariant.sol])

### Summary
`IdleCDOEthenaVariant._withdraw` accepts `msg.value` and forwards the entire amount as native ETH to a freshly cloned `EthenaCooldownRequest` contract. That clone has no function that can move native ETH out — `rescue()` only performs an ERC20 `transfer`, and `unstake()` only delivers sUSDe to the user. Any ETH attached to a withdrawal call is therefore locked forever in a single-use contract, with no refund to the caller and no rescue path.

### Finding Description
In `contracts/IdleCDOEthenaVariant.sol`, `_withdraw` creates a per-request clone and passes `msg.value` as the creation value:

```solidity
EthenaCooldownRequest clone = EthenaCooldownRequest(cooldownImpl.clone(
  abi.encodePacked(address(this), msg.sender),
  msg.value
));
``` [1](#0-0) 

There is no check on `msg.value`, no required amount, and no refund of the forwarded ETH — the clone simply needs sUSDe to run `cooldownShares`. Examining `contracts/strategies/ethena/EthenaCooldownRequest.sol`, the clone exposes only:

- `startCooldown()` — callable only by the CDO, moves sUSDe into cooldown [2](#0-1) 
- `unstake()` — sends sUSDe proceeds to the user [3](#0-2) 
- `rescue(address _token)` — restricted to `TL_MULTISIG` and only calls `IERC20Detailed(_token).transfer(...)`, i.e. it can only sweep ERC20 tokens, not native ETH [4](#0-3) 

The clone has no `receive`/ETH-withdrawal logic, no selfdestruct, and `rescue` cannot target ETH (there is no ERC20 at the ETH address). So the full `msg.value` is permanently bricked in an address whose only purpose ends after the single cooldown.

This is the direct analog of the external report: a user-facing payable function accepts ETH it does not need and never returns the excess — except here it is strictly worse, because *any* attached ETH is lost in full, not just the surplus above a price.

### Impact Explanation
A tranche holder calling `withdrawAA`/`withdrawBB` on the Ethena variant with a nonzero `msg.value` (e.g. via a multicall/batcher that attaches ETH, a wallet UI default, or simple user error) loses 100% of the attached ETH permanently. The funds are neither credited to the user's withdrawal, nor recoverable by the honest owner/`TL_MULTISIG` via `rescue`. Broken invariant: fair burn/redemption — the user burns tranche tokens and additionally donates unrecoverable ETH to a dead address. Loss is quantified as the full `msg.value` per affected withdrawal.

### Likelihood Explanation
The function silently accepts and forwards ETH, so the failure mode requires only a single user transaction with `msg.value > 0` — the same accidental-overpayment scenario as the source report. Routers, multisig batchers, and account-abstraction frontends frequently attach ETH to calls, making nonzero `msg.value` a realistic occurrence. No existing guard (default check, epoch gating, nonReentrant) inspects or returns the ETH, and `rescue`'s ERC20-only design confirms there is no contingency for ETH in the clone. Likelihood is moderate: it needs user error, but the protocol provides no safeguard against it.

Caveat: this assumes the `withdrawAA`/`withdrawBB` entrypoints on this variant are `payable` (which the explicit `msg.value` forwarding strongly implies). If they were non-payable, `msg.value` would always be 0 and the issue would be unreachable.

### Recommendation
Either make the withdrawal non-payable / revert on `msg.value > 0`, or refund the ETH to `msg.sender` instead of forwarding it to the clone. If ETH is intentionally forwarded for future clone functionality, add an ETH sweep to `EthenaCooldownRequest` (e.g. allow `_getUser()` to `call` out the ETH balance, or extend `rescue` to handle `address(0)` as native ETH) and return any unused ETH to the user in `_withdraw`.

### Proof of Concept
```solidity
// SPDX-License-Identifier: UNLICENSED
pragma solidity 0.8.10;

import "forge-std/Test.sol";
import {IdleCDOEthenaVariant} from "../contracts/IdleCDOEthenaVariant.sol";
import {EthenaCooldownRequest} from "../contracts/strategies/ethena/EthenaCooldownRequest.sol";
import {IERC20Detailed} from "../contracts/interfaces/IERC20Detailed.sol";

// Fork mainnet at a block where the Ethena variant CDO is live
// and IStakedUSDeV2(SUSDE).cooldownDuration() != 0.
contract ExcessEthTrappedPoC is Test {
    IdleCDOEthenaVariant cdo = IdleCDOEthenaVariant(payable(ETHEREUM_MAINNET_EThena_CDO));
    IERC20Detailed AATranche = IERC20Detailed(cdo.AATranche());
    address user = makeAddr("user");

    function test_WithdrawEthTrapped() public {
        // give user some AA tranche tokens (deal or deposit path)
        uint256 trancheBal = 100e18;
        deal(address(AATranche), user, trancheBal);

        uint256 ethSent = 1 ether;
        vm.deal(user, ethSent);

        // record clone address via the emitted event
        vm.recordLogs();
        vm.prank(user);
        cdo.withdrawAA{value: ethSent}(trancheBal); // reverts only if entrypoint is non-payable

        // extract NewCooldownRequestContract(address clone,...)
        Vm.Log[] memory logs = vm.getRecordedLogs();
        address clone;
        for (uint256 i; i < logs.length; i++) {
            // topic0 == NewCooldownRequestContract signature; topic1 == clone address
            if (logs[i].topics[0] == keccak256("NewCooldownRequestContract(address,address,uint256)")) {
                clone = address(uint160(uint256(logs[i].topics[1])));
            }
        }
        assertTrue(clone != address(0));

        // the full msg.value is sitting in the clone
        assertEq(clone.balance, ethSent, "ETH forwarded to clone");
        assertEq(user.balance, 0, "user got no ETH back");

        // no path to recover it:
        // - rescue() is ERC20-only and TL_MULTISIG-only
        vm.prank(EthenaCooldownRequest(payable(clone)).TL_MULTISIG());
        vm.expectRevert(); // IERC20Detailed(address(0)).transfer -> call to EOA/0x0 fails or is meaningless
        EthenaCooldownRequest(payable(clone)).rescue(address(0));

        // ETH remains permanently locked after cooldown completes as well,
        // since unstake() only moves sUSDe proceeds to the user.
        assertEq(clone.balance, ethSent, "ETH permanently trapped");
    }
}
```

Steps: fork mainnet, give an unprivileged user AA tranche tokens, call `withdrawAA{value: 1 ether}(amount)`, observe the newly deployed `EthenaCooldownRequest` clone holds the full 1 ETH, the user is refunded nothing, and neither the user nor `TL_MULTISIG` (via the ERC20-only `rescue`) can ever move that ETH.

### Citations

**File:** contracts/IdleCDOEthenaVariant.sol (L82-85)
```text
    EthenaCooldownRequest clone = EthenaCooldownRequest(cooldownImpl.clone(
      abi.encodePacked(address(this), msg.sender), 
      msg.value
    ));
```

**File:** contracts/strategies/ethena/EthenaCooldownRequest.sol (L15-18)
```text
  function startCooldown() external {
    require(msg.sender == _getCDO(), '6');
    IStakedUSDeV2(SUSDE).cooldownShares(IERC20Detailed(SUSDE).balanceOf(address(this)));
  }
```

**File:** contracts/strategies/ethena/EthenaCooldownRequest.sol (L22-24)
```text
  function unstake() external {
    IStakedUSDeV2(SUSDE).unstake(_getUser());
  }
```

**File:** contracts/strategies/ethena/EthenaCooldownRequest.sol (L27-30)
```text
  function rescue(address _token) external {
    require(msg.sender == TL_MULTISIG, '6');
    IERC20Detailed(_token).transfer(msg.sender, IERC20Detailed(_token).balanceOf(address(this)));
  }
```
