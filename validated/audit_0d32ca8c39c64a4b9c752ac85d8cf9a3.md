Both the CBOR decoder used for TEE attestation parsing (`crates/proof/tee/registrar/src/cbor.rs`) already enforces a `MAX_CBOR_NESTING_DEPTH` bound, so that path is not vulnerable. However, the Merkle Patricia Trie node decoder used throughout the fault-proof stack has the same unbounded-recursion pattern described in the `yaml` advisory (CWE-674).

### Title
Unbounded recursion in `TrieNode::decode` allows stack-overflow DoS of the fault-proof program via a single crafted preimage - (File: crates/proof/mpt/src/node.rs)

### Summary
`TrieNode::decode` recursively calls itself for `Extension` nodes with no depth bound, mirroring the exact bug class in the `yaml` advisory (CWE-674, uncontrolled recursion). A single RLP blob returned from the fault-proof preimage oracle can encode thousands of nested inline `Extension` nodes, each only a few bytes, causing `TrieNode::decode` to recurse until the call stack is exhausted.

### Finding Description
`TrieNode::decode` inspects the RLP header and, for a 2-element list, dispatches to `try_decode_leaf_or_extension_payload`, which for the `PREFIX_EXTENSION_EVEN`/`PREFIX_EXTENSION_ODD` case calls `Self::decode(buf)` again to decode the pointed-to child node [1](#0-0) . This recursive call has no depth counter or limit, unlike the CBOR decoder in the same codebase which explicitly tracks `depth` and rejects streams beyond `MAX_CBOR_NESTING_DEPTH` [2](#0-1) .

Per the encoding rules, an `Extension` node only "blinds" (hashes) its child pointer when the child's encoded length is ≥ 32 bytes; children shorter than that are embedded inline in the same buffer [3](#0-2) . This means a single preimage value can contain an arbitrarily deep chain of tiny inline `Extension` nodes (a few bytes per level), and `TrieNode::decode` will recurse through the entire chain in one call, exactly analogous to the `[[[[...]]]]` flow-sequence trick in the `yaml` PoC.

This decoder is the core primitive of the fault-proof/stateless-execution trie backend, documented as "a recursive, in-memory implementation of Ethereum's hexary Merkle Patricia Trie" [4](#0-3) . It is invoked directly on preimage-oracle bytes in the L1 trie provider used by the fault-proof program: [5](#0-4) 

The preimage oracle only checks that `keccak256(preimage) == requested_key` before storing/serving the bytes [6](#0-5) ; it performs no structural validation of the trie-node content. In the on-chain/local preimage-oracle flow used during dispute-game execution, a participant supplies both the preimage bytes and thereby self-selects the corresponding key — the oracle is "self-authenticating" and cannot detect a maliciously deep nesting chain before handing the bytes to `TrieNode::decode`.

### Impact Explanation
Any dispute-game participant, or a host program answering `TrieProvider::trie_node_by_hash`/hint requests, can supply a small (few-KB) crafted RLP blob that decodes into thousands of nested inline `Extension` nodes. Feeding this into `TrieNode::decode` — which happens whenever the fault-proof executor opens an account/storage trie node during trace replay — exhausts the call stack and aborts the process (`RangeError`-equivalent stack overflow / Rust process abort, since Rust has no catchable stack-overflow exception). This halts the fault-proof program mid-execution, which can stall or corrupt the dispute-game execution trace step that depends on deterministic completion of the trie walk, directly impacting the ability to produce a correct provable output for a dispute-game step.

### Likelihood Explanation
The trigger requires only a small, easily constructible RLP payload (a chain of minimal `Extension` nodes) and no privileged access — any actor able to inject or influence a preimage consumed by the fault-proof trie decoder (e.g., a dispute-game participant driving local preimage submission, or a compromised/malicious data source behind the `TrieProvider`) can reach `TrieNode::decode` with attacker-chosen bytes.

### Recommendation
Add an explicit recursion-depth counter/parameter to `TrieNode::decode` (and its `try_decode_leaf_or_extension_payload` helper), mirroring the `depth`/`MAX_CBOR_NESTING_DEPTH` pattern already used in `crates/proof/tee/registrar/src/cbor.rs`, and return a decode error once a sane maximum trie depth (e.g., 64–128, well above any realistic hexary-MPT depth for 32-byte keys) is exceeded.

### Proof of Concept
Construct nested inline `Extension` nodes: encode a `TrieNode::Leaf` as the innermost node, then repeatedly wrap it in `TrieNode::Extension { prefix: Nibbles::from_nibbles([0x0]), node: Box::new(inner) }` while `inner.length() < 32` (so no blinding occurs), for several thousand iterations, and RLP-encode the outermost node into one contiguous buffer. Feed that buffer to `TrieNode::decode(&mut buf.as_slice())` (as done in `OracleL1ChainProvider::trie_node_by_hash`) — the process will overflow the stack, analogous to the `'['.repeat(5000) + '1' + ']'.repeat(5000)` PoC in the `yaml` advisory.

### Citations

**File:** crates/proof/mpt/src/node.rs (L452-461)
```rust
        // Check the high-order nibble of the path to determine the type of node.
        match first_nibble {
            PREFIX_EXTENSION_EVEN | PREFIX_EXTENSION_ODD => {
                // Extension node
                let extension_node_value = Self::decode(buf).map_err(TrieNodeError::RLPError)?;
                Ok(Self::Extension {
                    prefix: unpack_path_to_nibbles(first, path[1..].as_ref()),
                    node: Box::new(extension_node_value),
                })
            }
```

**File:** crates/proof/mpt/src/node.rs (L527-537)
```rust
            Self::Extension { prefix, node } => {
                // Encode the extension node's header, prefix, and pointer node.
                Header { list: true, payload_length }.encode(out);
                alloy_trie::nodes::encode_path_leaf(prefix, false).as_slice().encode(out);
                if node.length() >= B256::ZERO.len() {
                    let hash = node.blind();
                    hash.encode(out);
                } else {
                    node.encode(out);
                }
            }
```

**File:** crates/proof/tee/registrar/src/cbor.rs (L94-100)
```rust
    /// Decodes an item with `depth` enclosing containers already entered.
    pub fn read_at(bytes: &[u8], start: usize, depth: usize) -> PlannerResult<Self> {
        if depth > MAX_CBOR_NESTING_DEPTH {
            return Err(PlannerError::Cose(format!(
                "CBOR nesting exceeds maximum depth {MAX_CBOR_NESTING_DEPTH}"
            )));
        }
```

**File:** crates/proof/mpt/README.md (L1-10)
```markdown
# `base-proof-mpt`

A recursive, in-memory implementation of Ethereum's hexary Merkle Patricia Trie (MPT).

## Overview

Implements Ethereum's Merkle Patricia Trie with support for retrieval, insertion, deletion, and
root computation via RLP-encoded trie node encoding. Starting from a trie root hash, `TrieNode`
lazily fetches and caches node preimages via `TrieProvider`, enabling stateless block execution
without storing the full state. Designed as the trie backend for [`base-proof-executor`](../executor).
```

**File:** crates/proof/proof/src/l1/chain_provider.rs (L125-142)
```rust
impl<T: CommsClient> TrieProvider for OracleL1ChainProvider<T> {
    type Error = OracleProviderError;

    fn trie_node_by_hash(&self, key: B256) -> Result<TrieNode, Self::Error> {
        // On L1, trie node preimages are stored as keccak preimage types in the oracle. We assume
        // that a hint for these preimages has already been sent, prior to this call.
        crate::block_on(async move {
            TrieNode::decode(
                &mut self
                    .oracle
                    .get(PreimageKey::new(*key, PreimageKeyType::Keccak256))
                    .await
                    .map_err(OracleProviderError::Preimage)?
                    .as_ref(),
            )
            .map_err(OracleProviderError::Rlp)
        })
    }
```

**File:** crates/proof/host/src/handler.rs (L1178-1188)
```rust
            let preimage: Bytes = providers.l2.client().request("debug_dbGet", &[hash]).await?;
            let actual_hash = keccak256(preimage.as_ref());
            if actual_hash != hash {
                return Err(HostError::StateNodePreimageHashMismatch {
                    expected: hash,
                    actual: actual_hash,
                });
            }

            let mut kv_write_lock = kv.write().await;
            kv_write_lock.set(PreimageKey::new_keccak256(*hash).into(), preimage.into())?;
```
