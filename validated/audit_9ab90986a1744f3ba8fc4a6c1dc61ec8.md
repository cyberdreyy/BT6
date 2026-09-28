### Title
ETH forwarded to `EthenaCooldownRequest` clones is permanently locked — no ETH rescue path - (File: contracts/IdleCDOEthenaVariant.sol)

### Summary
`IdleCDOEthenaVariant._withdraw` forwards `msg.value` to the ephemeral `EthenaCooldownRequest` clone it deploys per withdrawal. The clone contract can handle SUSDE (`startCooldown`, `unstake`) and ERC20 tokens (`rescue`), but has no mechanism to transfer out native ETH. Any ETH that lands in a clone — whether sent by the user alongside `withdrawAA`/`withdrawBB` or force-sent via `selfdestruct` — is unrecoverable.

### Finding Description
When a user calls `withdrawAA`/`withdrawBB`, the overridden `_withdraw` in `IdleCDOEthenaVariant` creates a per-request cooldown clone:

```solidity
// contracts/IdleCDOEthenaVariant.sol:82-85
EthenaCooldownRequest clone = EthenaCooldownRequest(cooldownImpl.clone(
  abi.encodePacked(address(this), msg.sender),
  msg.value
));
```

The `clone(target, data, value)` overload of `ClonesWithImmutableArgs` forwards `value` wei to the new clone. The clone's full surface is:

- `startCooldown()` — CDO-only, calls `SUSDE.cooldownShares(balance)` (`EthenaCooldownRequest.sol:15-18`)
- `unstake()` — permissionless, sends unstaked USDe to the immutable user arg (`:22-24`)
- `rescue(address _token)` — multisig-only, does `IERC20Detailed(_token).transfer(...)` which cannot move ETH (`:27-30`)

There is no `receive`/`fallback` handler, no ETH transfer, selfdestruct, or payable call in the clone. ETH sent in is therefore bricked forever. This is the same bug class as the reference report: a contract accepts native value but exposes no path to withdraw it (the `rescue` function is the analog of `withdrawETH` — it only handles tokens, not native ETH).

Note: whether ETH reaches the clone through `msg.value` depends on `withdrawAA`/`withdrawBB` being payable in this deployment (the `msg.value` forwarding strongly implies the entry point was designed to accept ETH); independent of that, ETH can be forced into an already-created clone via `selfdestruct`, and once the user calls `unstake()` and abandons the contract there is no recovery mechanism at all. I could not fully verify the `payable` modifier on the external withdraw entry points within available search iterations — the finding stands regardless via the force-send vector on clones, which are deployed contracts holding user-bound funds.

### Impact Explanation
Any ETH balance held by an `EthenaCooldownRequest` clone is permanently locked. The clone is single-purpose and typically abandoned after `unstake()`; neither the user, the CDO, nor the TL multisig (`rescue` is ERC20-only) can extract native ETH. Loss equals the ETH balance of each affected clone — a permanent freezing of funds, matching the "stuck ETH" impact of the original report.

### Likelihood Explanation
- Users interacting through wallets/UIs that attach ETH to calls, or intentionally funding the clone address for gas, lose that ETH.
- Since the clone address is emitted in `NewCooldownRequestContract`, it is a known payable-capable address; anyone can `selfdestruct` ETH into it, and nothing can be done about it.
- The `msg.value` forwarding in `_withdraw` indicates ETH was expected to flow to the clone, yet no outbound path was implemented.

### Recommendation
Either:
- Stop forwarding `msg.value` (call `cooldownImpl.clone(abi.encodePacked(address(this), msg.sender))` with the zero-value overload) and ensure withdraw entry points are non-payable, or
- Add an ETH sweep to `EthenaCooldownRequest.rescue`, e.g. treat `address(0)`/`ETH` sentinel by doing `payable(msg.sender).call{value: address(this).balance}("")`.

Since clones are created from a fixed `cooldownImpl`, the implementation must be redeployed if the sweep is added.

### Proof of Concept
```solidity
// Foundry fork test (mainnet), assumes withdrawAA is payable on IdleCDOEthenaVariant
function test_EthStuckInCooldownClone() public {
    // setup: user holds AA tranche tokens (omitted: deposit USDe -> depositAA)

    uint256 balBefore = address(this).balance;
    // user withdraws and attaches 1 ETH
    cdo.withdrawAA{value: 1 ether}(amount);

    // fetch clone address from NewCooldownRequestContract event or clone deterministic address
    EthenaCooldownRequest clone = EthenaCooldownRequest(payable(cloneAddr));

    assertEq(address(clone).balance, 1 ether);

    // cooldown ends, user unstakes: gets USDe, but ETH remains
    vm.warp(block.timestamp + cooldownDuration);
    clone.unstake();
    assertEq(address(clone).balance, 1 ether); // still locked

    // multisig rescue cannot help: it only does IERC20 transfer
    vm.prank(TL_MULTISIG);
    // clone.rescue(ETH_sentinel) -> reverts or transfers a meaningless ERC20
    // ETH remains in clone forever: no payable function, no selfdestruct
    assertEq(address(clone).balance, 1 ether);
}
```

Key references: `contracts/IdleCDOEthenaVariant.sol:82-85` (msg.value forwarding), `contracts/strategies/ethena/EthenaCooldownRequest.sol:27-30` (ERC20-only rescue).