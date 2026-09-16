### Title
Activation Registry precompile accepts and permanently strands attached ETH pre-Cobalt (currently live on Base mainnet) - (`crates/common/precompiles/src/activation/dispatch.rs`)

### Summary
The `ActivationRegistryStorage::dispatch_with_observer` function only rejects non-zero `msg.value` when the active hardfork is `BaseUpgrade::Cobalt` or later. On Base mainnet, `cobalt` is currently unscheduled (`cobalt: None` in `UpgradeConfig::BASE_MAINNET`), so the chain is permanently on `Beryl` from the precompile's point of view. Any transaction that calls the Activation Registry precompile (e.g. `isActivated`, `checkActivated`, `setAdmin`) with attached ETH will have that value silently accepted into the precompile address's balance and never used or made withdrawable, permanently locking the funds — exactly the "payable function with no use of native currency" bug class from the referenced report.

### Finding Description
`ActivationRegistryStorage::dispatch_with_observer` gates the non-payable check behind an explicit hardfork condition: [1](#0-0) 

The comment states this is deliberate: "Activation-registry selectors are all nonpayable; reject attached ETH from Cobalt onward. Pre-Cobalt (Beryl) preserves the historical accept-and-strand behavior..." [2](#0-1) 

Every other precompile dispatcher in the same crate (`b20_asset`, `b20_stablecoin`, `b20_factory`, `policy`, `nonce`, `tx_context`) rejects non-zero call value unconditionally regardless of fork, confirming this is the intended, safe pattern for all other precompiles: [3](#0-2) [4](#0-3) 

The `ActivationRegistryStorage` is the only dispatcher with the fork-gated exception, and this exception is not merely historical — because `cobalt` has no scheduled activation timestamp on Base mainnet (`cobalt: None`), the "pre-Cobalt" branch is the one that is permanently active today: [5](#0-4) 

The mainnet fork table also confirms `Cobalt` resolves to `ForkCondition::Never` on the current mainnet chain spec: [6](#0-5) 

### Impact Explanation
Since this precompile's non-payable check is currently unreachable on Base mainnet, any unprivileged sender or contract can attach ETH to a call into the Activation Registry precompile and that ETH will be accepted by the EVM's value-transfer mechanics into the precompile's account balance, while the precompile logic itself performs no accounting or refund for it. Because precompile addresses have no code path to move out an accumulated native balance, the ETH is permanently and irrecoverably locked — a concrete, permanent freezing-of-funds impact, matching the severity class of the referenced report.

### Likelihood Explanation
This requires no special privilege — any transaction sender or any contract making an internal call to the Activation Registry precompile address with non-zero `value` on any selector (`isActivated`, `checkActivated`, `setAdmin`, etc.) triggers it. Because the gating condition (`upgrade >= BaseUpgrade::Cobalt`) is never true on the currently live Base mainnet configuration, this is reachable on every call today, not merely as a historical replay quirk.

### Recommendation
Remove the fork-gated exception in `dispatch_with_observer` and reject non-zero `ctx.call_value()` unconditionally for the Activation Registry precompile, mirroring the unconditional check already used by every other B-20 precompile dispatcher (`b20_asset`, `b20_stablecoin`, `b20_factory`, `policy`, `nonce`, `tx_context`). If byte-for-byte replay of specific historical Beryl blocks that already sent value to this precompile must be preserved, that behavior should be scoped strictly to replaying those exact historical blocks (e.g., via a chain-specific/height-specific carve-out) rather than being tied to a "Cobalt" activation that may never occur on mainnet.

### Proof of Concept
1. On Base mainnet (current fork = Beryl, since `cobalt_timestamp` is `None`), craft a transaction (or an internal `CALL`) to the Activation Registry precompile address with calldata for any valid selector, e.g. `isActivated(feature)`, and set `value = 1 wei` (or any amount).
2. `ActivationRegistryStorage::dispatch_with_observer` evaluates `upgrade >= BaseUpgrade::Cobalt` as `false` (mainnet Beryl < Cobalt-never), so the `NonPayable` revert branch is skipped.
3. Execution proceeds normally, calldata gas is deducted, and the call succeeds while the EVM's outer value-transfer semantics move the attached ETH into the precompile address's balance.
4. The ETH is now held at the precompile address with no code path in `ActivationRegistryStorage` (or anywhere in the precompile crate) to withdraw or refund it, permanently locking the funds.

### Citations

**File:** crates/common/precompiles/src/activation/dispatch.rs (L46-58)
```rust
        let mut recorder = PrecompileCallRecorder::start(
            observer,
            PrecompileMetricLabels::activation_call(calldata),
        );
        // Activation-registry selectors are all nonpayable; reject attached ETH from Cobalt
        // onward. Pre-Cobalt (Beryl) preserves the historical accept-and-strand behavior so
        // replay of live-installed activation calls remains byte-identical.
        if upgrade >= BaseUpgrade::Cobalt && !ctx.call_value().is_zero() {
            return recorder.record_base_error_result(
                ctx,
                BasePrecompileError::revert(IActivationRegistry::NonPayable {}),
            );
        }
```

**File:** crates/common/precompiles/src/b20_asset/dispatch.rs (L53-56)
```rust
        if !ctx.call_value().is_zero() {
            return recorder
                .record_base_error_result(ctx, BasePrecompileError::revert(IB20::NonPayable {}));
        }
```

**File:** crates/common/precompiles/src/nonce/dispatch.rs (L29-33)
```rust
        // `getNonce` is nonpayable; reject attached ETH before charging calldata gas.
        if !ctx.call_value().is_zero() {
            return BasePrecompileError::revert(INonceManager::NonPayable {})
                .into_precompile_result(ctx.gas_used(), ctx.state_gas_used());
        }
```

**File:** crates/common/genesis/src/chain/upgrade.rs (L656-674)
```rust
    pub const BASE_MAINNET: Self = Self {
        regolith_time: Some(1_686_789_347),
        canyon_time: Some(1_704_992_401),
        delta_time: Some(1_708_560_000),
        ecotone_time: Some(1_710_374_401),
        fjord_time: Some(1_720_627_201),
        granite_time: Some(1_726_070_401),
        holocene_time: Some(1_736_445_601),
        pectra_blob_schedule_time: None,
        isthmus_time: Some(1_746_806_401),
        jovian_time: Some(1_764_691_201),
        base: BaseUpgradeConfig {
            azul: Some(1_779_991_200),
            beryl: Some(1_782_410_400),
            cobalt: None,
            denim: None,
            zenith: None,
        },
    };
```

**File:** crates/common/chains/src/chain.rs (L178-179)
```rust
        assert_eq!(base_mainnet_forks[Cobalt], ForkCondition::Never);
        assert_eq!(base_mainnet_forks[Denim], ForkCondition::Never);
```
