## Analysis: L1 Chain Config Oracle-Fallback Trust Gap in the Fault-Proof Boot Loader

The Octavia CVE's root cause is that a service accepts an attacker-supplied resource identifier (an amphora image) as trustworthy without verifying it comes from an authorized/owned source. The closest reachable analog I found in this codebase is in the fault-proof program's boot loader, which falls back to accepting an L1 chain configuration from the (attacker-influenced) preimage oracle when the L2 chain ID isn't in the compiled built-in list — a path the code itself flags as unsafe.

### Finding Description
`BootInfo`'s loader in `crates/proof/proof/src/boot.rs` first tries to resolve the L1 config from a compiled-in map (`base_common_chains::L1_CONFIGS`), and if the L2 chain ID isn't recognized, it fetches the L1 config from the preimage oracle under `L1_CONFIG_KEY` and deserializes it directly, with only a `warn!` log noting it is "insecure in production without additional validation": [1](#0-0) 

The oracle-loaded `L1Config` is not cryptographically bound to any hash committed at proof-verification time in the code paths I was able to inspect — the local key comment itself documents that "Oracle-loaded configs require additional validation": [2](#0-1) 

The preimage oracle in a fault-proof / dispute-game context is populated by the party running the client program (the host/prover), which in an adversarial dispute is exactly the untrusted party the fault-proof system is meant to defend against. If the L1 config controls derivation-relevant parameters (e.g., L1 system-config address, deposit-contract address, hardfork activation logic used by the derivation pipeline), then an attacker-controlled config injected via this fallback could change how L1 data is derived into L2 state — analogous to Octavia's fault: an unauthenticated caller supplies "configuration" that the system treats as verified.

### Impact Explanation
If reachable on any network path where the L2 chain ID isn't statically compiled into `L1_CONFIGS` (e.g., devnets, new supported chains, or any deployment relying on this fallback), a dispute-game participant supplying preimages could bias derivation parameters and cause the program to compute a wrong provable output root, potentially winning a fraudulent dispute-game resolution.

### Likelihood Explanation
This is gated behind the chain ID not being present in the compiled `L1_CONFIGS` map — for `mainnet`/`sepolia`/other officially supported chains, the safe compiled path is used, so exploitability depends on the chain-ID/config resolution logic being reachable in production deployments (e.g. a devnet-only path mistakenly exposed to a real dispute game, or a newly onboarded chain not yet added to `L1_CONFIGS`). I could not fully verify, within the available tooling, whether downstream code cryptographically commits/re-validates the oracle-supplied `L1Config` before it influences derivation, or whether callers restrict this fallback to non-production configurations only. This uncertainty means the exploit chain is plausible but not conclusively proven end-to-end from the code I was able to examine.

### Recommendation
Bind any oracle-supplied `L1Config` to a value committed in the boot/journal hash (so a mismatched or attacker-chosen config causes proof verification to fail), or reject the preimage-oracle fallback entirely for any officially deployed dispute game, restricting it strictly to local/devnet tooling that is out of the adversarial trust boundary.

### Proof of Concept
Not constructible from the available context — verifying exploitability requires confirming (1) whether `L1Config` values are included in the final output-root/journal commitment checked on-chain, and (2) whether production dispute-game deployments can reach the oracle-fallback branch (i.e., chain IDs missing from `L1_CONFIGS`). I recommend a Devin session with full repository access to trace `L1Config` usage from `BootInfo::load` through to the on-chain `AggregateVerifier`/output-root journal to confirm whether this gap is exploitable in a live dispute.

---

**Caveat:** Given the scope constraints and the single documented "insecure...without additional validation" comment as the strongest evidence, I want to be explicit that I was not able to trace the full commitment chain (whether `L1Config` fields flow into the cryptographically-verified output-root journal) within the available tool budget. If that downstream validation exists, this finding would be a documented, correctly-mitigated risk rather than an exploitable vulnerability. I did not find any other in-scope analog (precompile role/permit checks, EIP-8130 authorization, txpool admission, B20 factory/token creation) that exhibited a genuine missing-authorization defect — all of those paths I reviewed had explicit, tested authorization guards (`B20Guards::ensure_role`, `require_admin_caller`, policy registry admin checks, etc.).

### Citations

**File:** crates/proof/proof/src/boot.rs (L57-62)
```rust
/// The local key identifier for the L1 chain configuration.
///
/// This key is used as a fallback to retrieve the chain configuration from
/// the preimage oracle when no hardcoded configuration is available for the
/// given chain ID. Oracle-loaded configs require additional validation.
pub const L1_CONFIG_KEY: U256 = uint!(7_U256);
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
