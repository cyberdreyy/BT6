### Title
Unbounded recursive RLP decoding of `TrieNode` causes stack-overflow crash in the fault-proof program — (File: `crates/proof/mpt/src/node.rs`)

### Summary
`TrieNode::decode` and its helper `try_decode_leaf_or_extension_payload` recursively call `Self::decode` (directly, and transitively via the derived `Decodable for Vec<Self>` used for branch nodes) with **no depth limit**, unlike the project's own CBOR parser (`crates/proof/tee/registrar/src/cbor.rs`) which explicitly caps nesting via `MAX_CBOR_NESTING_DEPTH`. A single crafted preimage blob containing deeply nested, non-blinded `Extension`/`Branch` payloads will recurse until the native stack is exhausted, matching the bug class in CVE-2021-32256 (unbounded recursive descent in `demangle_type` causing a stack-overflow crash).

### Finding Description
`TrieNode::decode` peeks the RLP header and, for list items, dispatches on element count: [1](#0-0) 

For a 2-element list it calls `try_decode_leaf_or_extension_payload`, which — if the path nibble marks an *extension* node — recurses directly into `Self::decode(buf)` on the inner bytes, with no depth counter or size ceiling: [2](#0-1) 

For a 17-element list (branch node) it calls `Vec::<Self>::decode(buf)`, which (via `alloy_rlp`'s derived list decoding) invokes `TrieNode::decode` once per child — each child can again be an arbitrarily deep `Branch`/`Extension`: [3](#0-2) 

Critically, the 32-byte "blinding" size limit documented on the type is only enforced on the **encode** path (`blind`), not on **decode**: [4](#0-3) [5](#0-4) 

This means a single buffer served for one `PreimageKey` lookup can contain thousands of nested, non-hash-blinded `Extension`/`Branch` levels — there is no requirement that inner nodes be individually hashed and re-fetched; decoding recurses purely on in-memory bytes. This decoder is reached by the fault-proof program's `TrieProvider` implementations, which decode oracle-fetched bytes without any pre-validation of nesting depth: [6](#0-5) [7](#0-6) 

and is invoked transitively from `TrieNode::unblind`/`open` during account/storage trie traversal in the stateless executor: [8](#0-7) 

In the OP-style fault dispute game, the party submitting a disputed claim effectively chooses the raw bytes underlying every hash it references from that point in the trace onward (the preimage is self-consistent — hash = keccak256(attacker-chosen bytes)), so it can freely embed a pathological, deeply-nested trie-node blob as the preimage for any node hash on the disputed execution path.

### Impact Explanation
A malicious dispute-game participant (or a party submitting an adversarial L1/L2 preimage set to the fault-proof program's oracle) can construct a state/account/receipts trie preimage with unbounded nesting depth. When an honest challenger/verifier's `op-program`/fault-proof executor decodes this preimage via `TrieNode::decode`, the process stack-overflows and crashes (SIGSEGV/abort), halting execution of the fault-proof program for that trace step. This prevents the honest party from computing/asserting the correct output root at that step of the dispute game, which can allow an invalid claim to go unchallenged — a "wrong provable output root" / node-halt condition explicitly in scope, with a direct path to permanent freezing/loss of funds if a fraudulent withdrawal claim resolves unchallenged.

### Likelihood Explanation
Any actor able to inject a preimage decoded via `TrieNode::decode` (i.e., any dispute-game participant supplying execution-trace data through the preimage oracle) can trigger this deterministically with a single crafted blob — no cryptographic break or timing race is required, only the ability to choose bytes that self-consistently hash to the referenced key, which is inherent to how fault-proof preimages work.

### Recommendation
Add an explicit recursion-depth counter (mirroring `MAX_CBOR_NESTING_DEPTH` in `crates/proof/tee/registrar/src/cbor.rs`) threaded through `TrieNode::decode`, `try_decode_leaf_or_extension_payload`, and the branch-node `Vec::<Self>::decode` path, bounded to the maximum possible trie depth (e.g., 64 nibbles for 256-bit keccak keys), and reject/error out once exceeded instead of recursing further. Alternatively, convert the recursive descent into an explicit iterative/worklist-based decoder.

### Proof of Concept
1. Construct RLP bytes for a 2-item list `[encoded_path, inner]` where `inner` is itself another 2-item extension-node list `[encoded_path, inner2]`, and repeat this nesting N times (N in the tens of thousands), terminating in a valid leaf.
2. Serve this blob as the response to a `PreimageKey::new(hash_of(blob), PreimageKeyType::Keccak256)` request (self-consistent since `hash_of(blob)` is computed from the attacker's own chosen bytes) at the point in a dispute-game trace where the fault-proof program calls `TrieNode::unblind`/`open` on that hash, e.g. reached from `crates/proof/proof/src/l1/chain_provider.rs:128-142` or `crates/proof/executor/src/db/mod.rs:132-154`.
3. Running the fault-proof program against this trace step causes `TrieNode::decode` (`crates/proof/mpt/src/node.rs:567`) to recurse N times, exhausting the stack and crashing the process before it can produce the correct execution result.

### Citations

**File:** crates/proof/mpt/src/node.rs (L53-60)
```rust
/// In the Ethereum Merkle Patricia Trie, nodes longer than an encoded 32 byte string (33 total
/// bytes) are blinded with [keccak256] hashes. When a node is "opened", it is replaced with the
/// [`TrieNode`] that is decoded from to the preimage of the hash.
///
/// The [`alloy_rlp::Encodable`] and [`alloy_rlp::Decodable`] traits are implemented for
/// [`TrieNode`], allowing for RLP encoding and decoding of the types for storage and retrieval. The
/// implementation of these traits will implicitly blind nodes that are longer than 32 bytes in
/// length when encoding. When decoding, the implementation will leave blinded nodes in place.
```

**File:** crates/proof/mpt/src/node.rs (L110-122)
```rust
    /// Blinds the [`TrieNode`].. Alternatively, if the [`TrieNode`] is a [`TrieNode::Blinded`] node
    /// already, its commitment is returned directly.
    pub fn blind(&self) -> B256 {
        match self {
            Self::Blinded { commitment } => *commitment,
            Self::Empty => EMPTY_ROOT_HASH,
            _ => {
                let mut rlp_buf = Vec::with_capacity(self.length());
                self.encode(&mut rlp_buf);
                keccak256(rlp_buf)
            }
        }
    }
```

**File:** crates/proof/mpt/src/node.rs (L124-182)
```rust
    /// Unblinds the [`TrieNode`] if it is a [`TrieNode::Blinded`] node.
    pub fn unblind<F: TrieProvider>(&mut self, fetcher: &F) -> TrieNodeResult<()> {
        if let Self::Blinded { commitment } = self {
            if *commitment == EMPTY_ROOT_HASH {
                // If the commitment is the empty root hash, the node is empty, and we don't need to
                // reach out to the fetcher.
                *self = Self::Empty;
            } else {
                *self = fetcher
                    .trie_node_by_hash(*commitment)
                    .map_err(|e| TrieNodeError::Provider(e.to_string()))?;
            }
        }
        Ok(())
    }

    /// Walks down the trie to a leaf value with the given key, if it exists. Preimages for blinded
    /// nodes along the path are fetched using the `fetcher` function, and persisted in the inner
    /// [`TrieNode`] elements.
    ///
    /// ## Takes
    /// - `self` - The root trie node
    /// - `path` - The nibbles representation of the path to the leaf node
    /// - `fetcher` - The preimage fetcher for intermediate blinded nodes
    ///
    /// ## Returns
    /// - `Err(_)` - Could not retrieve the node with the given key from the trie.
    /// - `Ok(None)` - The node with the given key does not exist in the trie.
    /// - `Ok(Some(_))` - The value of the node
    pub fn open<'a, F: TrieProvider>(
        &'a mut self,
        path: &Nibbles,
        fetcher: &F,
    ) -> TrieNodeResult<Option<&'a mut Bytes>> {
        match self {
            Self::Branch { stack } => {
                let branch_nibble = path.get(0).ok_or(TrieNodeError::PathTooShort)? as usize;
                stack
                    .get_mut(branch_nibble)
                    .map(|node| node.open(&path.slice(BRANCH_NODE_NIBBLES..), fetcher))
                    .unwrap_or(Ok(None))
            }
            Self::Leaf { prefix, value } => Ok((path == prefix).then_some(value)),
            Self::Extension { prefix, node } => {
                if path.slice(..prefix.len()) == *prefix {
                    // Follow extension branch
                    node.unblind(fetcher)?;
                    node.open(&path.slice(prefix.len()..), fetcher)
                } else {
                    Ok(None)
                }
            }
            Self::Blinded { .. } => {
                self.unblind(fetcher)?;
                self.open(path, fetcher)
            }
            Self::Empty => Ok(None),
        }
    }
```

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

**File:** crates/proof/mpt/src/node.rs (L567-590)
```rust
impl Decodable for TrieNode {
    /// Attempts to decode the [`TrieNode`].
    fn decode(buf: &mut &[u8]) -> alloy_rlp::Result<Self> {
        // Peek at the header to determine the type of Trie node we're currently decoding.
        let header = Header::decode(&mut (**buf).as_ref())?;

        if header.list {
            // Peek at the RLP stream to determine the number of elements in the list.
            let list_length = rlp_list_element_length(&mut (**buf).as_ref())?;

            match list_length {
                BRANCH_LIST_LENGTH => {
                    let list = Vec::<Self>::decode(buf)?;
                    Ok(Self::Branch { stack: list })
                }
                LEAF_OR_EXTENSION_LIST_LENGTH => {
                    // Advance the buffer to the start of the list payload.
                    buf.advance(header.length());
                    // Decode the leaf or extension node's raw payload.
                    Self::try_decode_leaf_or_extension_payload(buf)
                        .map_err(|_| alloy_rlp::Error::UnexpectedList)
                }
                _ => Err(alloy_rlp::Error::UnexpectedLength),
            }
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

**File:** crates/proof/proof/src/l2/chain_provider.rs (L173-190)
```rust
impl<T: CommsClient> TrieProvider for OracleL2ChainProvider<T> {
    type Error = OracleProviderError;

    fn trie_node_by_hash(&self, key: B256) -> Result<TrieNode, OracleProviderError> {
        // On L2, trie node preimages are stored as keccak preimage types in the oracle. We assume
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
