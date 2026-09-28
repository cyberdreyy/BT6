### Title
ETH sent with `withdrawAA`/`withdrawBB` is permanently locked in the per-request cooldown clone - ([File: contracts/IdleCDOEthenaVariant.sol](contracts/IdleCDOEthenaVariant.sol))

### Summary
The Ethena variant of IdleCDO forwards the entire `msg.value` of a withdraw call into the immutable-args clone it deploys for each cooldown request (`contracts/IdleCDOEthenaVariant.sol:82-85`). The clone (`EthenaCooldownRequest`) has no function that can move native ETH: `rescue` only performs `IERC20Detailed(_token).transfer`, which cannot withdraw ETH. Any ETH attached to a withdrawal is therefore locked forever, with no check that it is needed and no refund of the excess — the same bug class as the reported "excess `msg.value` is never returned" issue.

### Finding Description
`IdleCDOEthenaVariant._withdraw` creates a fresh clone per cooldown request and endows it with the full call value:

```solidity
EthenaCooldownRequest clone = EthenaCooldownRequest(cooldownImpl.clone(
    abi.encodePacked(address(this), msg.sender),
    msg.value
));
```

The clone (`contracts/strategies/ethena/EthenaCooldownRequest.sol`) exposes only three state-changing functions:

- `startCooldown()` — callable only by the CDO.
- `unstake()` — calls `sUSDe.unstake(user)`; does not use or forward ETH.
- `rescue(token)` — restricted to `TL_MULTISIG`, but rescues only ERC20 balances via `IERC20Detailed(_token).transfer(...)`; there is no ETH sweep path.

There is no equality check, cap, or refund on `msg.value`. A user who attaches ETH to `withdrawAA`/`withdrawBB` — whether by fee/miscalculation, a stale UI suggesting a gas endowment, or a copy-paste error — has the full amount `CREATE`-endowed into a bytecode-minimal clone whose code can never spend it. Even an EOA cannot be the beneficiary: `rescue` is gated to the multisig and is ERC20-only, and `unstake` sends sUSDe proceeds to the hardcoded user arg, never ETH.

The broken invariant is "value in ≥ value out minus intended fees": the contract silently absorbs arbitrary native value without crediting it anywhere in `lastNAVAA`/`lastNAVBB`, `getContractValue`, or `unclaimedFees`, so it is neither accounted for nor recoverable — it is not even a donation other users could claim, it is simply burned-equivalent.

### Impact Explanation
Direct, permanent loss of user funds: 100% of any `msg.value` sent alongside a withdrawal is irretrievably locked in a single-use clone. Loss is unbounded (the entire ETH amount attached). No privileged role is involved and no guard (`_checkSameBlock`, `_checkDefault`, `nonReentrant`, `whenNotPaused`) inspects or limits `msg.value`.

### Likelihood Explanation
Low — requires a user mistake (attaching nonzero ETH to a payable withdrawal call), mirroring the reported finding. There is no attacker profit motive; the loss is self-inflicted. The deliberate `msg.value` forwarding suggests ETH was intended as a gas endowment for the clone, but since the clone cannot spend ETH, even the "intended" amount is stranded.

### Recommendation
Add an exact-amount requirement or refund the excess. For example, require `msg.value == expectedEndowment` (or `msg.value == 0` if no endowment is intended), or compute the needed endowment and return `msg.value - needed` to `msg.sender` before cloning. Additionally, give `EthenaCooldownRequest` an ETH recovery path (e.g., let `rescue(address(0))` or a dedicated `rescueETH()` send `address(this).balance` to the multisig/user) so stranded value is not permanently locked.

### Proof of Concept
Foundry fork test sketch (Ethereum mainnet fork, sUSDe cooldown duration != 0):

```solidity
// SPDX-License-Identifier: UNLICENSED
pragma solidity 0.8.10;

import "forge-std/Test.sol";
import {IdleCDOEthenaVariant} from "../contracts/IdleCDOEthenaVariant.sol";
import {IERC20Detailed} from "../contracts/interfaces/IERC20Detailed.sol";

contract ExcessMsgValueTest is Test {
    IdleCDOEthenaVariant cdo = IdleCDOEthenaVariant(payable(EThena_CDO_ADDR));
    IERC20Detailed usde = IERC20Detailed(USDE);
    IERC20Detailed susde = IERC20Detailed(0x9D39A5DE30e57443BfF2A8307A4256c8797A3497);
    address user = address(0xA11CE);

    function test_ethLockedInClone() public {
        // user holds AA tranche tokens (setup: deal USDe, depositAA)
        deal(USDE, user, 1000e18);
        vm.startPrank(user);
        usde.approve(address(cdo), 1000e18);
        uint256 minted = cdo.depositAA(1000e18);
        vm.warp(block.timestamp + 1); // avoid same-block check

        // user mistakenly attaches 1 ETH to the withdraw call
        vm.deal(user, 1 ether);
        vm.recordLogs();
        cdo.withdrawAA{value: 1 ether}(minted);

        // recover clone address from NewCooldownRequestContract event
        Vm.Log[] memory logs = vm.getRecordedLogs();
        address clone;
        for (uint256 i; i < logs.length; i++) {
            // topic1 = contractAddress (indexed)
            clone = address(uint160(uint256(logs[i].topics[1])));
        }

        // the full 1 ETH sits in the clone; no function can return it
        assertEq(clone.balance, 1 ether);
        assertEq(user.balance, 0);
        // rescue only moves ERC20 -> ETH is unrecoverable
        vm.stopPrank();
    }
}
```

Note: this analysis assumes `withdrawAA`/`withdrawBB` are declared `payable` on `IdleCDO` (required for `msg.value` to be nonzero and consistent with the deliberate forwarding at line 84); if they are not payable, `msg.value` is always zero and no value can be locked, in which case this finding reduces to dead code rather than a fund-loss vector.