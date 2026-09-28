### Title
ETH forwarded by `IdleCDOEthenaVariant._withdraw` is permanently locked in `EthenaCooldownRequest` clones — ([File: contracts/IdleCDOEthenaVariant.sol](contracts/IdleCDOEthenaVariant.sol))

### Summary
`IdleCDOEthenaVariant._withdraw` forwards the caller's entire `msg.value` into each newly created `EthenaCooldownRequest` clone. The clone contract has no `receive`/`fallback` that can move ETH and its `rescue` function only handles ERC20 transfers, so any native token delivered to the clone — via the forwarded `msg.value` or via `selfdestruct` — is permanently unrecoverable. This mirrors the reported bug class: value is sent to a contract that has no endpoint to return it, so the funds are frozen.

### Finding Description
In `contracts/IdleCDOEthenaVariant.sol:82-85`, a withdrawal deploys a per-request cooldown contract and passes `msg.value` through to `ClonesWithImmutableArgs.clone`:

```solidity
EthenaCooldownRequest clone = EthenaCooldownRequest(cooldownImpl.clone(
  abi.encodePacked(address(this), msg.sender),
  msg.value
));
```

`ClonesWithImmutableArgs.clone` forwards the value to the clone's address at creation time, so the clone's ETH balance becomes `msg.value`. Looking at `contracts/strategies/ethena/EthenaCooldownRequest.sol:8-41`, the clone exposes only three state-changing functions:

- `startCooldown()` — callable only by the CDO, calls `cooldownShares` on sUSDe.
- `unstake()` — calls `IStakedUSDeV2(SUSDE).unstake(_getUser())`, which sends USDe (an ERC20) to the user, never ETH.
- `rescue(address _token)` — callable only by `TL_MULTISIG`, performs `IERC20Detailed(_token).transfer(...)`, i.e. ERC20 only. There is no ETH path (no `.call{value:...}` and no `selfdestruct`).

The clone also defines no `receive()`/`fallback`, so the deployed bytecode itself cannot move ETH once it holds a balance. Nothing in `IdleCDOEthenaVariant` reads or refunds the user's `msg.value`; the ETH simply sits in the clone forever.

Note: this issue is only reachable if the entry points that reach `_withdraw` (`withdrawAA`/`withdrawBB`) are `payable` on the deployed variant; the explicit `msg.value` forwarding in line 84 indicates the function was designed to accept value, which is why the clone also accepts it. I was not able to confirm the `payable` modifier on the outer functions within the indexed portion of `IdleCDO.sol`. Independently of `msg.value`, ETH can also be forced into a clone via `selfdestruct`, where it is equally unrecoverable — the absence of an ETH-rescue path is unconditional.

### Impact Explanation
Any native token balance that lands in an `EthenaCooldownRequest` clone is permanently frozen. When `withdrawAA`/`withdrawBB` are called with a non-zero `msg.value` (e.g., a user or an aggregator contract that funds withdrawals with ETH, or simply attaches value by mistake), that ETH is transferred to the clone and can never be recovered by the user, the CDO, or even `TL_MULTISIG` — `rescue` transfers `IERC20Detailed(_token)`, which does not work for the native asset. The loss is direct and quantifiable: 100% of the ETH balance held by the clone. This is the same failure mode as the Seaport/PausableZone report — a contract participates in a value-bearing flow but lacks the endpoint needed to move the native asset back out.

### Likelihood Explanation
- The CDO explicitly forwards `msg.value`, so any caller attaching ETH loses it immediately; no privileged action or race is required.
- Each withdrawal creates a fresh clone, so the vulnerable surface recurs on every cooldown withdrawal.
- ETH can also be forced into any clone address (which is CREATE-predictable) via `selfdestruct`, at which point it is permanently locked even though no one intentionally sent it.
- The only recovery mechanism (`rescue`) is hardcoded to ERC20 and gated to `TL_MULTISIG`, so there is no admin mitigation for ETH specifically.

### Recommendation
- Remove the `msg.value` forwarding in `IdleCDOEthenaVariant._withdraw` (pass `0` to `clone`), or explicitly refund any `msg.value` to `msg.sender` in the same transaction.
- Alternatively/additionally, give `EthenaCooldownRequest` an ETH rescue path: add `receive() external payable {}` and extend `rescue` (or add `rescueEth()`) to forward `address(this).balance` to `TL_MULTISIG` via a low-level `.call{value:...}`.

### Proof of Concept
Foundry fork test (mainnet fork, matching `test/foundry/EthenaSusdeStrategy.t.sol` setup which deploys `IdleCDOEthenaVariant` against the real sUSDe at `0x9D39A5DE30e57443BfF2A8307A4256c8797A3497`):

```solidity
// SPDX-License-Identifier: AGPL-3.0
pragma solidity 0.8.10;

import "./EthenaSusdeStrategy.t.sol";

contract TestEthenaCloneEthLocked is TestEthenaSusdeStrategy {
    function testEthForwardedToCloneIsLocked() external {
        uint256 amount = 10000 * ONE_SCALE;
        idleCDO.depositAA(amount);
        _cdoHarvest(true);
        vm.roll(block.number + 1);

        // user withdraws and attaches 1 ETH (payable entry point)
        vm.recordLogs();
        uint256 ethSent = 1 ether;
        vm.deal(address(this), ethSent);
        idleCDO.withdrawAA{value: ethSent}(0);

        Vm.Log[] memory logs = vm.getRecordedLogs();
        address clone = address(uint160(uint256(logs[logs.length - 1].topics[1])));

        // ETH arrived at the clone
        assertEq(clone.balance, ethSent, "clone received forwarded msg.value");

        // rescue() cannot recover ETH: it only does IERC20Detailed(token).transfer
        vm.prank(0xFb3bD022D5DAcF95eE28a6B07825D4Ff9C5b3814); // TL_MULTISIG
        EthenaCooldownRequest(clone).rescue(USDe); // moves USDe only, ETH stays

        // unstake() only moves USDe to the user, ETH stays
        vm.warp(block.timestamp + uint256(IStakedUSDeV2(SUSDe).cooldownDuration()) + 1);
        EthenaCooldownRequest(clone).unstake();

        // ETH is permanently frozen
        assertEq(clone.balance, ethSent, "ETH permanently locked in clone");
        assertEq(address(this).balance, 0, "user never got ETH back");
    }
}
```

Expected result: the clone's ETH balance equals the forwarded `msg.value` after the withdrawal and remains non-zero after `unstake()` and after `TL_MULTISIG` calls `rescue`, demonstrating permanent freezing of user-supplied native tokens.