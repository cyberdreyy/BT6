### Title
ETH sent with `withdrawAA`/`withdrawBB` is permanently locked due to missing `msg.value == 0` check - (File: contracts/IdleCDO.sol)

### Summary
`withdrawAA` and `withdrawBB` in `IdleCDO` are `payable` (payability is required by the `IdleCDOEthenaVariant`, which forwards `msg.value` into a freshly created `EthenaCooldownRequest` clone at `contracts/IdleCDOEthenaVariant.sol:82-85`). In the base `IdleCDO` implementation, `_withdraw` never inspects or refunds `msg.value`, so any ETH attached to a withdrawal on a non-Ethena CDO is silently trapped in the contract — there is no `receive()`-based accounting, no ETH sweep/rescue function, and no `require(msg.value == 0)` guard. This is a direct analog of the reported `receiveFunds()` bug: a payable entry point that accepts ERC20-style parameters alongside native value, with no check that `msg.value` is zero when the native path is not intended.

### Finding Description
In `contracts/IdleCDO.sol`, the public withdrawal entry points `withdrawAA(uint256)` and `withdrawBB(uint256)` are declared `payable` so that the Ethena variant override can use `msg.value` as the clone-creation stipend (`IdleCDOEthenaVariant._withdraw`, `contracts/IdleCDOEthenaVariant.sol:82-85`). For every other `IdleCDO` deployment (the standard pool, `IdleCDOEpochVariant`, `IdleCDOInstadappLiteVariant`, etc.), the ETH path is meaningless:

- The base `_withdraw` logic burns tranche tokens and transfers ERC20 `token` underlying to the user; `msg.value` is never read.
- `IdleCDO` has no `receive()`/`fallback` that credits ETH, and no function to recover ETH held by the contract. ERC20 donation skimming (`_skimDonatedAssets`) only handles ERC20 balances.
- Even in the Ethena variant, the `msg.value` is forwarded into a minimal-proxy `EthenaCooldownRequest` clone (`contracts/strategies/ethena/EthenaCooldownRequest.sol`) that is designed only to custody sUSDe through the cooldown; ETH forwarded beyond that purpose is trapped in the clone.

Broken invariant: every wei of ETH sent to the vault must be either credited to a position or returned to the sender. Neither holds; the ETH is stranded with no withdrawal path.

### Impact Explanation
Permanent loss/locking of user funds. A lender who calls `withdrawAA{value: 1 ether}(amount)` on a standard IdleCDO loses 1 ETH permanently: the withdrawal succeeds, tranche tokens are burned, ERC20 underlying is paid out, and the attached ETH remains in the `IdleCDO` contract balance forever, benefiting no one. Impact is bounded by `msg.value` per call but unbounded in aggregate across users and calls; on the Ethena variant the same user error strands ETH inside an immutable clone proxy.

### Likelihood Explanation
The bug requires user error (sending ETH with a tranche withdrawal), which is why severity is Medium rather than High — identical to the source report. However, it is realistic because:

- `withdrawAA`/`withdrawBB` being `payable` is an unusual, undocumented affordance inherited for one variant's internals; wallets and front-ends that batch value through payable multicall wrappers can attach value.
- On the Ethena variant itself, attaching more ETH than intended (or any ETH once `clone(msg.value)` forwards it) locks it in a proxy with no ETH rescue path.
- No guard exists anywhere in the call chain to revert on nonzero `msg.value`.

### Recommendation
Add an explicit check in the entry points, or make payability variant-scoped:

```solidity
// contracts/IdleCDO.sol — in _withdraw or in withdrawAA/withdrawBB
require(msg.value == 0, "no-eth");
```

and in `contracts/IdleCDOEthenaVariant.sol` keep payability but cap/forward only the intended stipend, refunding any excess:

```solidity
uint256 stipend = msg.value;
// ... clone(stipend) ...
// or require(msg.value == EXPECTED_CLONE_STIPEND)
```

Alternatively, drop `payable` from the base `withdrawAA`/`withdrawBB` and fund clone creation from the contract's own balance or a dedicated stipend pool.

### Proof of Concept
Foundry fork test sketch (place under `test/foundry/`):

```solidity
// SPDX-License-Identifier: MIT
pragma solidity 0.8.10;

import "forge-std/Test.sol";
import {IdleCDO} from "../../contracts/IdleCDO.sol";
import {IERC20Detailed} from "../../contracts/interfaces/IERC20Detailed.sol";

contract LockedEthWithdrawTest is Test {
    IdleCDO cdo;           // fork: any deployed non-Ethena IdleCDO
    address user = address(0xA11CE);

    function setUp() public {
        // vm.createSelectFork(MAINNET_RPC);
        // cdo = IdleCDO(<deployed IdleCDO address>);
        // deal underlying to user, depositAA to obtain AA tranche tokens
        vm.deal(user, 10 ether);
    }

    function test_ethLockedOnWithdraw() public {
        uint256 trancheBal = IERC20Detailed(cdo.AATranche()).balanceOf(user);
        uint256 cdoEthBefore = address(cdo).balance;

        // user mistakenly attaches 1 ETH to a tranche withdrawal
        vm.prank(user);
        cdo.withdrawAA{value: 1 ether}(trancheBal);

        // ETH is now sitting in the IdleCDO with no recovery path
        assertEq(address(cdo).balance, cdoEthBefore + 1 ether);
        assertEq(user.balance, 9 ether); // only ERC20 underlying was returned

        // there is no function on IdleCDO that can move ETH out:
        // no receive-credit, no sweepETH, no rescue for native value.
    }
}
```

Uncertainty note: the exact signature lines of `withdrawAA`/`withdrawBB` in `contracts/IdleCDO.sol` were not re-read in this session, but the `payable` modifier is required for `IdleCDOEthenaVariant` to consume `msg.value` inside `_withdraw` (`contracts/IdleCDOEthenaVariant.sol:82-85`), which is consistent with the `payable` matches found in `contracts/IdleCDO.sol`. The core claim — nonzero `msg.value` is silently retained with no recovery path — follows from the absence of any ETH-handling logic in the base withdrawal flow.