## Finding

Confirmed: `AlloyChainProvider` in `crates/consensus/providers/src/chain_provider.rs` verifies header hashes via `verify_header_hash` when `trust_rpc = false`, but `receipts_by_hash` never verifies the returned receipts against the header's `receipts_root` via a trie proof — regardless of the `trust_rpc` setting. This is asymmetric with the oracle/fault-proof path (`crates/proof/proof/src/l1/chain_provider.rs`), which does perform `receipts_root` verification. The un-authenticated receipts flow directly into `derive_deposits` and `SystemConfig::update_with_receipts` in `crates/consensus/derive/src/attributes/stateful.rs`, which build L2 deposit transactions and system-config updates straight from L1 receipt logs with no cryptographic binding to the L1 header.

### Title
Unverified L1 receipts allow forged deposit/system-config events to be derived into L2 blocks - (File: crates/consensus/providers/src/chain_provider.rs)

### Summary
`AlloyChainProvider::receipts_by_hash` fetches receipts from the configured L1 RPC endpoint and returns them without proving inclusion against the corresponding block header's `receipts_root`, unlike `header_by_hash`/`block_info_and_transactions_by_hash`, which call `verify_header_hash`. The `trust_rpc` flag, intended to let operators run in an untrusted-RPC mode, has no effect on receipts at all.

### Finding Description
`verify_header_hash` (`crates/consensus/providers/src/chain_provider.rs:86-109`) is only invoked from `header_by_hash` and `block_info_and_transactions_by_hash`. `receipts_by_hash` (lines 207-235) fetches receipts via `self.inner.get_block_receipts(hash.into())` and returns them directly after a bare shape conversion — there is no `verify_proof`/MPT check tying the receipts to the header's `receipts_root`, and no check is gated by `trust_rpc` here at all. [1](#0-0) 

These receipts are consumed directly by the attributes builder: `derive_deposits` (`crates/consensus/derive/src/attributes/stateful.rs:286-311`) scans receipt logs for `TransactionDeposited` events emitted by the configured deposit contract and turns them into deposit transactions placed into the derived L2 block, and `sys_config.update_with_receipts` on the same receipts (`stateful.rs:133-137`) applies system-config updates. [2](#0-1) 

Because the receipts are never checked against the actual L1 header's `receipts_root`, any component sitting between the node and the "real" L1 chain — a malicious/compromised L1 RPC endpoint, a MITM proxy, or an operator-misconfigured RPC — can serve a header that matches by hash (or is accepted because `trust_rpc = true`, the default) together with an arbitrary receipts list containing forged `TransactionDeposited` logs or forged system-config event logs. The derivation pipeline has no independent means to detect the forgery for the receipts specifically, since inclusion is never proven. This mirrors the Rainbow Bridge class of bug: fake block/receipt data accepted by a light-client-style derivation path without independently proving inclusion against a signed/committed root. [3](#0-2) 

By contrast, the ZK/fault-proof-oracle L1 provider path does perform this verification (evidenced by the `receipts_root` match in `crates/proof/proof/src/l1/chain_provider.rs`), showing the live derivation path is inconsistent with the security model used for the canonical dispute path.

### Impact Explanation
If an attacker controls or compromises the L1 RPC endpoint a sequencer/verifier node is configured against (a realistic operational scenario, e.g. shared/public RPC providers, DNS hijack, or a malicious relay), they can inject arbitrary forged `TransactionDeposited` events, causing the node to mint L2 ETH/tokens with no backing L1 deposit ever having occurred — unbacked supply / theft. They can equally forge `SystemConfigUpdate` events to change gas limits, unsafe block signer, batcher address, or fee scalars on the L2 side. This is a concrete unauthorized-mint / unbacked-supply vector reachable purely by controlling data returned to the `ChainProvider`, which the derivation pipeline treats as authoritative L1 truth.

### Likelihood Explanation
Medium. It requires the attacker to control or MITM the L1 RPC data path feeding the derivation pipeline rather than corrupting the real L1 chain itself, and the ultimate divergence would be caught by the fault-proof/dispute game path (which does verify receipts against `receipts_root`), similar to how the Rainbow Bridge attack was caught in 31 seconds. But any window before divergence detection, and any node/service that acts on the forged deposit (e.g., a sequencer building on it, or downstream consumers trusting derived state) is exposed, and `trust_rpc=false` — the mode explicitly meant to defend against this class of RPC compromise — does not actually protect the receipts path at all.

### Recommendation
Add MPT inclusion-proof verification of receipts against the header's `receipts_root` in `AlloyChainProvider::receipts_by_hash`, at minimum when `trust_rpc = false`, mirroring the verification already performed for headers and already present in the oracle-backed proof L1 chain provider. Consider making receipts verification mandatory regardless of `trust_rpc`, since receipts feed directly into deposit/system-config derivation which controls minting and privileged config changes.

### Proof of Concept
1. Point a Base consensus node's L1 RPC provider at an attacker-controlled/MITM'd endpoint (or compromise a shared RPC provider), with `trust_rpc` left at default (`true`) or even `false`.
2. Have the malicious endpoint respond to `eth_getBlockByHash` with a legitimate header (so `verify_header_hash` passes) but respond to `eth_getBlockReceipts` for that same hash with a receipts list containing a forged `TransactionDeposited` log from the configured `deposit_contract` address encoding an attacker-chosen `mint`/`value`.
3. The node's `StatefulAttributesBuilder::prepare_payload_attributes` calls `receipts_fetcher.receipts_by_hash`, then `derive_deposits` decodes the forged log via `Deposits::decode` (`crates/consensus/protocol/src/deposits.rs:38`) into a valid deposit transaction, which is placed into the derived L2 block and executed by the deposit-transaction path in the EVM handler (`crates/common/evm/src/handler.rs:81-94`), which explicitly skips extra validation for deposit transactions ("pre-verified on L1").
4. The forged mint is credited on L2 with no real L1 ETH ever deposited.

### Citations

**File:** crates/consensus/providers/src/chain_provider.rs (L17-31)
```rust
/// The [`AlloyChainProvider`] is a concrete implementation of the [`ChainProvider`] trait, providing
/// data over Ethereum JSON-RPC using an alloy provider as the backend.
#[derive(Debug, Clone)]
pub struct AlloyChainProvider {
    /// The inner Ethereum JSON-RPC provider.
    pub inner: RootProvider,
    /// Whether to trust the RPC without verification.
    pub trust_rpc: bool,
    /// `header_by_hash` LRU cache.
    header_by_hash_cache: LruCache<B256, Header>,
    /// `receipts_by_hash_cache` LRU cache.
    receipts_by_hash_cache: LruCache<B256, Vec<Receipt>>,
    /// `block_info_and_transactions_by_hash` LRU cache.
    block_info_and_transactions_by_hash_cache: LruCache<B256, (BlockInfo, Vec<TxEnvelope>)>,
}
```

**File:** crates/consensus/providers/src/chain_provider.rs (L207-235)
```rust
    async fn receipts_by_hash(&mut self, hash: B256) -> Result<Vec<Receipt>, Self::Error> {
        if let Some(receipts) = self.receipts_by_hash_cache.get(&hash) {
            Metrics::chain_cache_hits("receipts_by_hash").increment(1);
            return Ok(receipts.clone());
        }

        Metrics::chain_cache_misses("receipts_by_hash").increment(1);

        Metrics::chain_rpc_calls("receipts_by_hash").increment(1);

        let receipts = base_metrics::time!(Metrics::request_duration("receipts_by_hash"), {
            self.inner.get_block_receipts(hash.into()).await
        })
        .inspect_err(|_e| {
            Metrics::chain_rpc_errors("receipts_by_hash").increment(1);
        })?
        .ok_or(AlloyChainProviderError::BlockNotFound(hash.into()))?;
        let consensus_receipts = receipts
            .into_iter()
            .map(|r| r.inner.into_primitives_receipt().as_receipt().cloned())
            .collect::<Option<Vec<_>>>()
            .ok_or(AlloyChainProviderError::ReceiptsConversion(hash))?;

        self.receipts_by_hash_cache.put(hash, consensus_receipts.clone());

        Metrics::cache_entries("receipts_by_hash").increment(1);

        Ok(consensus_receipts)
    }
```

**File:** crates/consensus/derive/src/attributes/stateful.rs (L126-140)
```rust
            }
            let receipts =
                self.receipts_fetcher.receipts_by_hash(epoch.hash).await.map_err(Into::into)?;
            let deposits =
                derive_deposits(epoch.hash, &receipts, self.rollup_cfg.deposit_contract_address)
                    .await
                    .map_err(|e| PipelineError::BadEncoding(e).crit())?;
            let (updates, errors) = sys_config.update_with_receipts(
                &receipts,
                self.rollup_cfg.l1_system_config_address,
                self.rollup_cfg.is_ecotone_active(header.timestamp),
            );
            for kind in &updates {
                info!(target: "attributes", epoch = epoch.number, %kind, "Applied system config update");
            }
```
