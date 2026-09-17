## Finding [1](#0-0) [2](#0-1) 

### Title
Fault-proof program trusts unauthenticated preimage-oracle-supplied rollup/L1 config for any non-built-in chain ID, enabling a wrong provable output root - (File: crates/proof/proof/src/boot.rs)

### Summary
`BootInfo::load` in `crates/proof/proof/src/boot.rs` is the prologue step of the fault-proof client program that boots derivation parameters from the preimage oracle before deriving L2 blocks and computing the claimed output root. For chain IDs that are not present in the compiled-in `base_common_chains::ChainConfig` registry (or whose L1 chain ID is not in `base_common_chains::L1_CONFIGS`), the code falls back to trusting the `rollup_config`/`l1_config` values read directly from the preimage oracle, with only a `warn!` log noting this is "insecure in production without additional validation."

### Finding Description
`BootInfo::load` reads `chain_id` and an oracle-supplied `RollupConfig` from local preimage keys `L2_CHAIN_ID_KEY` / `L2_ROLLUP_CONFIG_KEY` [3](#0-2) . It only cross-checks that `chain_id == oracle_rollup_config.l2_chain_id.id()` [4](#0-3) . For a *recognized, non-devnet* built-in chain, it discards most of the oracle-supplied config in favor of the compiled `trusted_rollup_config`, only merging in contract-backed upgrade-activation timestamps [5](#0-4) . But when `ChainConfig::by_chain_id(chain_id)` returns `None` (i.e., an unrecognized chain ID), the code falls back to using the entire oracle-provided `oracle_rollup_config` as-is [6](#0-5) . The same untrusted-fallback pattern repeats for `l1_config`: if the resulting `rollup_config.l1_chain_id` is not in the built-in `L1_CONFIGS` map, an entire `ChainConfig` (deposit contract addresses, chain topology, etc.) is deserialized straight from the oracle without any cryptographic binding [2](#0-1) .

This mirrors the xUnit Plugin CWE-693 class: a less-trusted party (here, the entity supplying preimage-oracle data for the fault-proof program, analogous to the "agent") controls a message/value (the rollup/L1 config) that the more-trusted controller (the fault-proof client, which computes the authoritative output root used in dispute resolution) accepts and uses as ground truth, guarded only by a log warning rather than actual policy enforcement.

### Impact Explanation
The fault-proof program's derived output root is the basis on which the dispute-game/fault-proof system determines whether a proposed L2 output root is valid. If an unrecognized `chain_id` (or an L1 chain ID absent from `L1_CONFIGS`) reaches this code path, the derivation pipeline runs entirely on attacker/oracle-controlled parameters (deposit contract address, system config address, gas config, upgrade activation times, genesis parameters, etc.), which can be crafted to produce a *wrong provable output root* — one of the explicitly in-scope impact categories (wrong provable output root / node halt / chain split class). This directly undermines the integrity guarantee that the fault-proof program is supposed to provide.

### Likelihood Explanation
Exploitability is conditioned on how `chain_id` reaches `BootInfo::load` in production. For the currently supported built-in Base networks the fixed/trusted path is taken, and this is explicitly called out in code comments and tests as an insecure fallback intended for local devnet / not-yet-onboarded chains. The likelihood is Medium: it requires either misconfiguration/onboarding of a new chain before it is added to the built-in registry, or an execution context where the host/oracle-populated local key values are not otherwise pinned to a specific, already-known chain ID by the surrounding dispute-game/host wiring.

### Recommendation
Never accept a full `RollupConfig`/`ChainConfig` from the preimage oracle as authoritative in production paths. Either (a) hard-fail boot loading when `chain_id` (or its L1 chain ID) is not present in the compiled-in registry, rather than silently falling back with a warning, or (b) require the oracle-supplied config fields to be independently authenticated (e.g., committed on-chain and verified against a hash) before being merged into `BootInfo`.

### Proof of Concept
1. Configure a preimage oracle (as used by `Prologue::load`/`BootInfo::load`) to serve a `chain_id` that is not present in `base_common_chains::ChainConfig` and provide an arbitrary `RollupConfig` at `L2_ROLLUP_CONFIG_KEY` (e.g. with a different `deposit_contract_address`, `l1_system_config_address`, or gas/fee parameters).
2. Run `BootInfo::load` against this oracle; observe it proceeds past the `chain_id == rollup_config_chain_id` check and returns a `BootInfo` whose `rollup_config` is fully attacker-controlled (see the existing test `uses_live_rollup_config_for_local_devnet` at [7](#0-6)  which demonstrates the same "live" untrusted-config code path being exercised, only gated by the `chain_id` matching a DEVNET-style entry).
3. Feed this `BootInfo` into `FaultProofDriver::execute`; the derivation pipeline runs entirely using the attacker-influenced rollup/L1 configuration, producing an output root that does not reflect the real, agreed-upon protocol parameters.

### Citations

**File:** crates/proof/proof/src/boot.rs (L279-297)
```rust
        let chain_id = u64::from_be_bytes(
            oracle
                .get(PreimageKey::new_local(L2_CHAIN_ID_KEY.to()))
                .await
                .map_err(OracleProviderError::Preimage)?
                .as_slice()
                .try_into()
                .map_err(OracleProviderError::SliceConversion)?,
        );

        let activation_admin_address =
            base_common_chains::ChainConfig::beryl_activation_admin_address_by_chain_id(chain_id);

        let ser_cfg = oracle
            .get(PreimageKey::new_local(L2_ROLLUP_CONFIG_KEY.to()))
            .await
            .map_err(OracleProviderError::Preimage)?;
        let oracle_rollup_config: RollupConfig =
            serde_json::from_slice(&ser_cfg).map_err(OracleProviderError::Serde)?;
```

**File:** crates/proof/proof/src/boot.rs (L299-306)
```rust
        // Bind the node-served schedule to the committed boot chain ID before using any of it.
        let rollup_config_chain_id = oracle_rollup_config.l2_chain_id.id();
        if chain_id != rollup_config_chain_id {
            return Err(OracleProviderError::RollupConfigChainIdMismatch {
                boot_chain_id: chain_id,
                rollup_config_chain_id,
            });
        }
```

**File:** crates/proof/proof/src/boot.rs (L308-331)
```rust
        // Fixed built-in chains must execute with their compiled static derivation parameters. Only
        // contract-backed activation timestamps may come from the node, because ScheduleId commits
        // them separately. The local devnet is mutable, so its live node-served config is required.
        let trusted_chain_config =
            base_common_chains::ChainConfig::by_chain_id(chain_id).filter(|chain_config| {
                chain_config.chain_id != base_common_chains::ChainConfig::DEVNET.chain_id
            });
        let mut rollup_config = if let Some(chain_config) = trusted_chain_config {
            let mut trusted_rollup_config = chain_config.rollup_config();
            for upgrade in BaseUpgrade::CONTRACT_VARIANTS {
                trusted_rollup_config.apply_upgrade_activation(
                    upgrade,
                    oracle_rollup_config.upgrades.activation(upgrade),
                );
            }
            trusted_rollup_config
        } else {
            warn!(
                target: "boot_loader",
                chain_id,
                "no fixed trusted rollup config available, falling back to preimage oracle; insecure in production without additional validation"
            );
            oracle_rollup_config
        };
```

**File:** crates/proof/proof/src/boot.rs (L333-351)
```rust
        // Attempt to load the L1 config from the rollup config's L1 chain ID. If there is no config
        // for the chain, fall back to loading the config from the preimage oracle.
        let l1_config = if let Some(config) =
            base_common_chains::L1_CONFIGS.get(&rollup_config.l1_chain_id)
        {
            config.clone()
        } else {
            warn!(
                target: "boot_loader",
                chain_id = rollup_config.l1_chain_id,
                "no l1 config found in built-in mapping, falling back to preimage oracle; insecure in production without additional validation"
            );
            let ser_cfg = oracle
                .get(PreimageKey::new_local(L1_CONFIG_KEY.to()))
                .await
                .map_err(OracleProviderError::Preimage)?;

            serde_json::from_slice(&ser_cfg).map_err(OracleProviderError::Serde)?
        };
```

**File:** crates/proof/proof/src/boot.rs (L639-668)
```rust
    #[tokio::test]
    async fn uses_live_rollup_config_for_local_devnet() {
        const CLAIM_BLOCK: u64 = 100;

        let chain_config = BaseChainConfig::DEVNET;
        let mut rollup_config = chain_config.rollup_config();
        rollup_config.genesis.l2_time = 1_000;
        rollup_config.seq_window_size += 1;

        let mut oracle = MockOracle::new();
        oracle.insert(L1_HEAD_KEY, B256::repeat_byte(0x11).to_vec());
        oracle.insert(L2_OUTPUT_ROOT_KEY, B256::repeat_byte(0x22).to_vec());
        oracle.insert(L2_CLAIM_KEY, B256::repeat_byte(0x33).to_vec());
        oracle.insert(L2_CLAIM_BLOCK_NUMBER_KEY, CLAIM_BLOCK.to_be_bytes().to_vec());
        oracle.insert_rollup_config(chain_config.chain_id, &rollup_config);
        oracle.insert(
            L1_CONFIG_KEY,
            serde_json::to_vec(
                base_common_chains::L1_CONFIGS
                    .get(&1)
                    .expect("mainnet L1 config should be available"),
            )
            .expect("L1 config should serialize"),
        );

        let boot_info = BootInfo::load(&oracle).await.expect("boot info should load");

        assert_eq!(boot_info.rollup_config.genesis.l2_time, 1_000);
        assert_eq!(boot_info.rollup_config.seq_window_size, rollup_config.seq_window_size);
    }
```
