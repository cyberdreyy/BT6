### Title
Hardcoded `cooldownImpl` clone implementation address breaks all withdrawals on non-mainnet deployments - (File: contracts/IdleCDOEthenaVariant.sol)

### Summary
`IdleCDOEthenaVariant` hardcodes `cooldownImpl = 0xe0C4a2B14F0ACd936226A598BE6BfeD190E098d1`, a `ClonesWithImmutableArgs` implementation contract that exists only at that address on the chain where it was originally deployed. Every withdrawal in this CDO variant clones this implementation to create an `EthenaCooldownRequest`. If the CDO is deployed on a chain where the implementation was not deployed at that exact address (or cannot be, because CREATE2 deployment is deterministic but may not have been executed), `clone()` resolves to an empty/incorrect contract and withdrawals permanently revert or send sUSDe to a dead clone, freezing lender funds. This is the same bug class as the LooksRare `UNISWAP_V3_FACTORY` hardcoding: a fixed external address assumed valid on all chains.

### Finding Description
`contracts/IdleCDOEthenaVariant.sol:18` declares:

```solidity
address public constant cooldownImpl = 0xe0C4a2B14F0ACd936226A598BE6BfeD190E098d1;
```

`_withdraw` (the only redemption path — the parent `super._withdraw` path is dead code, gated by `require(...cooldownDuration() != 0)` on line 47) executes:

```solidity
EthenaCooldownRequest clone = EthenaCooldownRequest(cooldownImpl.clone(
    abi.encodePacked(address(this), msg.sender), msg.value));
IERC20Detailed(strategyToken).safeTransfer(address(clone), SUSDeRedeemed);
clone.startCooldown();
```

`ClonesWithImmutableArgs.clone()` copies runtime bytecode from `cooldownImpl`. On any chain where `0xe0C4...98d1` holds no code (or different code), `clone()` either reverts, returns `address(0)`, or produces a clone of a foreign contract. In the revert/empty cases every `_withdraw` call reverts — deposits, accounting, and NAV all update correctly but users can never exit. In the wrong-code case, `SUSDeRedeemed` strategy tokens are transferred to a contract that cannot run the correct `unstake()`/`rescue()` logic, stranding the redeemed sUSDe.

The same hardcoding pattern exists in the clone's own code (`contracts/strategies/ethena/EthenaCooldownRequest.sol:9-10` hardcodes `SUSDE` and `TL_MULTISIG`), and `TL_MULTISIG` there is the mainnet treasury multisig — on other chains (the repo deploys to Arbitrum, Base, Optimism, Polygon zkEVM per `utils/addresses.js`) the multisig differs, so `rescue()` would also be unusable.

### Impact Explanation
Permanent freezing of user funds (withdrawals). sUSDe with a non-zero cooldown duration can only be redeemed through this clone-and-cooldown flow; there is no fallback path in `_withdraw`. If `cooldownImpl` is absent or incorrect on the target chain, 100% of the CDO's NAV becomes unreachable to tranche holders — equivalent to the "breaks core contract functionality" Medium impact in the source report, with the stronger consequence here that the breakage hits the only exit function and locks TVL.

### Likelihood Explanation
Medium. The condition materializes as soon as this variant is deployed on any chain where the implementation was not deployed at that deterministic address, or where the bytecode at that address belongs to an unrelated deployer. It is not attacker-triggered; it is a deployment-environment mismatch, matching the source bug class. The repo's deployment infra (`utils/addresses.js`, `.openzeppelin/*.json`) shows multi-chain deployments are an intended target, so the mismatch is plausible rather than hypothetical.

### Recommendation
Elevate `cooldownImpl` to a storage/immutable variable set at initialization (e.g., a new init arg validated as non-zero with `extcodesize > 0`), or deploy the `EthenaCooldownRequest` implementation in the same transaction/routine that upgrades the CDO and store the resulting address. Similarly, make `SUSDE` and `TL_MULTISIG` in `EthenaCooldownRequest` constructor/immutable-args rather than `constant`, since token and multisig addresses differ per chain.

### Proof of Concept
A Foundry PoC would:

1. Fork a chain (e.g., Base) where `0xe0C4a2B14F0ACd936226A598BE6BfeD190E098d1` contains no deployed `EthenaCooldownRequest` bytecode, or locally deploy `IdleCDOEthenaVariant` without etching code at `cooldownImpl`.
2. Initialize the CDO with the sUSDe strategy and complete a deposit (AA tranche).
3. Call `withdrawAA(amount)` — `_withdraw` reaches `cooldownImpl.clone(...)` at line 82.
4. Observe the call reverts (clone of empty address fails / `startCooldown()` on a zero-code clone reverts), or the sUSDe transfer lands on a clone whose `startCooldown`/`unstake` cannot run — demonstrating permanent freezing of the withdrawal.

Note: I could not fully verify whether the repo's deploy scripts pre-deploy the cooldown implementation per-chain (scripts/tasks were not exhaustively searched), but the `constant` at `contracts/IdleCDOEthenaVariant.sol:18` unconditionally couples the CDO's only exit path to one fixed address regardless of chain.