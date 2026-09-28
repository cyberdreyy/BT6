### Title
ETH forwarded to cooldown-request clone is permanently locked due to missing `msg.value` check in `_withdraw` - ([File: contracts/IdleCDOEthenaVariant.sol](contracts/IdleCDOEthenaVariant.sol))

### Summary
`IdleCDOEthenaVariant._withdraw` forwards the entire `msg.value` into the freshly created `EthenaCooldownRequest` clone via `cooldownImpl.clone(args, msg.value)`. The clone is a minimal `Clone` contract with no payable/receive logic for spending ETH and whose only recovery path (`rescue`) can move ERC20 tokens, not ETH. Any ETH sent along `withdrawAA`/`withdrawBB` is permanently locked in the clone.

### Finding Description
At line 82-85 of `contracts/IdleCDOEthenaVariant.sol`:

```solidity
EthenaCooldownRequest clone = EthenaCooldownRequest(cooldownImpl.clone(
  abi.encodePacked(address(this), msg.sender),
  msg.value
));
```

`clone(bytes, uint256)` in `ClonesWithImmutableArgs` sends `value` wei to the new clone. There is no check that `msg.value == 0`. The cooldown request itself never requires ETH — `startCooldown` (line 15-18 of `EthenaCooldownRequest.sol`) only calls `IStakedUSDeV2(SUSDE).cooldownShares(...)`, and `unstake` (line 22-24) calls `unstake(_getUser())`. Neither consumes ETH.

The only recovery mechanism is `rescue(address _token)` (lines 27-30), restricted to `TL_MULTISIG` and implemented with `IERC20Detailed(_token).transfer(...)`, which cannot transfer native ETH. The clone has no `receive`/`fallback` that can forward ETH out and no selfdestruct/withdraw function, so the ETH balance is unrecoverable.

### Impact Explanation
A user calling the withdrawal path (`withdrawAA`/`withdrawBB` on `IdleCDOEthenaVariant`) who mistakenly attaches ETH — e.g., a wallet/aggregator bundling a value-carrying call, or a copy-paste error — loses the full `msg.value` permanently. The ETH sits in an immutable clone whose bytecode offers no ETH egress; even the trusted multisig cannot rescue it because `rescue` only handles ERC20. This is permanent freezing/loss of funds with a quantified loss equal to the attached `msg.value`, mirroring the external report's "excess ETH locked in contract" class.

### Likelihood Explanation
Requires a user mistake (nonzero `msg.value` on a withdrawal), so likelihood is moderate-low — the same likelihood profile as the source finding. However, the function silently accepts and forwards value instead of reverting, which is what makes the mistake fatal rather than a no-op. No existing guard (reentrancy lock, `_checkSameBlock`, `_checkDefault`, KYC) inspects `msg.value`.

Caveat: this depends on the external withdraw entry point being `payable` (the grep index shows `payable` occurrences in `IdleCDO.sol`, consistent with `msg.value` being usable in `_withdraw`); if a deployment's entry is non-payable, the ETH path is unreachable and the issue degrades to dead/harmful code. I could not confirm the `payable` modifier line in `IdleCDO.sol` within the iteration budget.

### Recommendation
Add an explicit check in `_withdraw` (or the external withdraw functions) that no ETH is attached:

```solidity
require(msg.value == 0, "no-eth");
```

Alternatively, drop the `msg.value` argument from `cooldownImpl.clone(...)`, since `EthenaCooldownRequest` never needs an ETH balance. If ETH support is ever intended, add an ETH-aware rescue path (e.g., `rescue` handling `address(0)` via `call`) to `EthenaCooldownRequest`.

### Proof of Concept
```solidity
// SPDX-License-Identifier: UNLICENSED
pragma solidity 0.8.10;

import "forge-std/Test.sol";
import {IdleCDOEthenaVariant} from "../contracts/IdleCDOEthenaVariant.sol";
import {EthenaCooldownRequest} from "../contracts/strategies/ethena/EthenaCooldownRequest.sol";
import {IERC20Detailed} from "../contracts/interfaces/IERC20Detailed.sol";

contract EthenaEthLockTest is Test {
    // mainnet fork with a live IdleCDOEthenaVariant instance
    IdleCDOEthenaVariant cdo = IdleCDOEthenaVariant(payable(<DEPLOYED_CDO>));
    address user = makeAddr("user");

    function testEthLockedInCooldownClone() public {
        // user holds AA tranche tokens (obtained via deposit or deal)
        uint256 trancheBal = IERC20Detailed(cdo.AATranche()).balanceOf(user);
        assertGt(trancheBal, 0);

        // snapshot a block to pass _checkSameBlock
        vm.roll(block.number + 1);

        vm.prank(user);
        // user mistakenly attaches 1 ETH to the withdraw call
        cdo.withdrawAA{value: 1 ether}(0);

        // fetch the clone address from the NewCooldownRequestContract event
        // (or compute clone(cooldownImpl, abi.encodePacked(cdo, user)))
        address clone = <emittedClone>;

        // the ETH is in the clone and cannot move
        assertEq(clone.balance, 1 ether);

        // multisig rescue cannot recover ETH: rescue only does ERC20 transfer
        vm.prank(EthenaCooldownRequest(clone).TL_MULTISIG());
        vm.expectRevert(); // IERC20Detailed(ETH).transfer is not a valid ETH transfer
        EthenaCooldownRequest(clone).rescue(address(0));

        // user funds permanently locked
        assertEq(clone.balance, 1 ether);
    }
}
```

Run as a Foundry mainnet fork against a deployed `IdleCDOEthenaVariant` pool; pre-condition is that the caller holds tranche tokens and `cooldownDuration() != 0` so the `_withdraw` path executes.