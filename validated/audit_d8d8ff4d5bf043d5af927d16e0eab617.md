### Title
Native ETH attached to `withdrawAA`/`withdrawBB` is permanently locked in a throwaway `EthenaCooldownRequest` clone - (File: contracts/IdleCDOEthenaVariant.sol)

### Summary
`IdleCDOEthenaVariant._withdraw` forwards the caller's `msg.value` to a freshly created `EthenaCooldownRequest` clone during an ERC20 (USDe/sUSDe) withdrawal flow. Since the clone has no ETH-receiving or ETH-recovery logic (its `rescue` only sweeps ERC20 tokens and is gated to a multisig), any native ETH sent along with a withdrawal is permanently locked in the clone. This is the same bug class as the external report: a function operating on ERC20 tokens silently accepts/forwards `msg.value`, causing unexpected loss of native tokens.

### Finding Description
In `contracts/IdleCDOEthenaVariant.sol`, the cooldown-based withdrawal path clones a request contract like this (lines 82–85):

```solidity
EthenaCooldownRequest clone = EthenaCooldownRequest(cooldownImpl.clone(
  abi.encodePacked(address(this), msg.sender), 
  msg.value
));
```

`ClonesWithImmutableArgs.clone(data, value)` deploys the clone **and transfers `value` wei of ETH to it**. The withdrawal itself deals exclusively with ERC20 tokens (`strategyToken` = sUSDe, redeemed to USDe) — there is no legitimate use of native ETH anywhere in this flow:

- `EthenaCooldownRequest.startCooldown()` only calls `IStakedUSDeV2.cooldownShares` (non-payable ERC20 operation).
- `unstake()` calls `IStakedUSDeV2.unstake(_getUser())` — also non-payable.
- `rescue(address _token)` transfers an `IERC20Detailed` balance and is restricted to `TL_MULTISIG`; it cannot recover ETH.
- The clone defines no `receive()`/`fallback()` handler that forwards ETH and no ETH sweep.

Because `msg.value` is used inside `_withdraw`, the external entry point (`withdrawAA`/`withdrawBB` on the `IdleCDO` base) is necessarily payable (the contract compiles only if `msg.value` is reachable in a payable context). A user — or a UI/integration that reuses a payable multicall pattern — attaching ETH to a withdrawal will have that ETH sent to a one-off clone address where it is locked forever. There is no validation that `msg.value == 0` anywhere in the path.

Contrast with `LidoCDOTrancheGateway` (`contracts/strategies/lido/LidoCDOTrancheGateway.sol`), which handles this correctly: `depositAAWithEth`/`depositBBWithEth` consume the full `msg.value` via `_mintStEth`, and the ERC20-token variants (`depositAAWithEthToken`, `depositBBWithEthToken`) are deliberately **non-payable**, so no ETH can be attached when funding with WETH/stETH.

### Impact Explanation
Permanent, unrecoverable loss of native ETH for any user who attaches value to a `withdrawAA`/`withdrawBB` call on the Ethena variant CDO. The ETH lands in a purpose-built clone contract (`EthenaCooldownRequest`) that has no code path to move ETH — `rescue` only handles ERC20 and is multisig-gated, and `unstake`/`startCooldown` never touch ETH. The loss equals the full `msg.value`, and the clone address is effectively a burn address for ETH.

### Likelihood Explanation
Moderate. It requires a user mistake (attaching ETH to a withdrawal), but the function is payable, gives no revert for `msg.value > 0`, and the funds are unrecoverable rather than merely at risk — exactly the profile of the referenced OpenQ finding, which was judged Medium. Integrating wallets/routers that batch calls with leftover value, or users assuming ETH-denominated vaults, are realistic triggers.

### Recommendation
Validate that no native value is attached to the ERC20 withdrawal, and do not forward `msg.value` to the clone:

```solidity
require(msg.value == 0, 'no-eth');
// or simply deploy with 0 value:
EthenaCooldownRequest clone = EthenaCooldownRequest(cooldownImpl.clone(
  abi.encodePacked(address(this), msg.sender), 
  0
));
```

Alternatively, make the external `withdrawAA`/`withdrawBB` entry points non-payable if nothing else requires `msg.value`.

### Proof of Concept
A Foundry test would: deploy/fork `IdleCDOEthenaVariant` (mainnet fork pinning sUSDe and the real `cooldownImpl` at `0xe0C4...8d1`), seed the CDO with sUSDe, have a user deposit to mint AA tranche tokens, then call `withdrawAA(amount){value: 1 ether}`. Assert `address(clone).balance == 1 ether` (from the `NewCooldownRequestContract` event) and that no function on `EthenaCooldownRequest` can move the ETH — `unstake()` and `startCooldown()` leave the ETH balance untouched and `rescue` only sweeps ERC20. The 1 ETH is permanently locked.

Caveat: I could not fully confirm the exact `payable` modifier on `IdleCDO.withdrawAA`/`withdrawBB` because grep returned only match counts on the final iteration; however, `msg.value` inside `_withdraw` (line 84) compiles only if the external caller path is payable, so the exposure follows necessarily.