## Analysis

CVE-2020-36373 is a stack-overflow DoS from unbounded/uncapped recursion in a parser processing attacker-crafted nested input. The equivalent pattern exists in Base's Merkle-Patricia-Trie node implementation, which is used by the fault-proof program to walk untrusted trie data pulled from the preimage oracle.

### Title
Unbounded recursion in `TrieNode::decode`/`open`/`unblind`/`insert`/`delete` enables stack-overflow DoS of the fault-proof program via crafted deep MPT chains - (File: `crates/proof/mpt/src/node.rs`)

### Summary
`TrieNode::decode` recursively decodes RLP-encoded extension/branch nodes with no depth limit, and `TrieNode::open`/`unblind`/`insert`/`delete` recursively walk/fetch trie nodes from an untrusted `TrieProvider` (preimage oracle) with no bound on recursion depth, mirroring the class of bug in CVE-2020-36373 (`parse_shifts` recursing on crafted input without a depth guard).

### Finding Description
`TrieNode::decode` recursively invokes `Self::decode(buf)` for `Extension` node children when the child is small enough to be inlined rather than blinded (children are only blinded/hashed when `node.length() >= B256::ZERO.len()`, i.e. ≥ 32 bytes encoded): [1](#0-0) 
This means a single RLP blob (e.g. a single preimage fetched by hash) can contain thousands of nested, sub-32-byte `Extension` node "shells", and `TrieNode::decode` will recurse once per nesting level with no maximum-depth check anywhere in the crate (`grep` for `MAX_DEPTH`/`depth limit` in `crates/proof/**` returns no matches).

Separately, `TrieNode::open`, `unblind`, `insert`, and `delete` are also unboundedly recursive over trie depth, fetching blinded nodes from the caller-supplied `TrieProvider` at each level: [2](#0-1) [3](#0-2) 

In the fault-proof program, `OracleL1ChainProvider::trie_node_by_hash` decodes exactly this untrusted, oracle-supplied preimage data: [4](#0-3) 
The preimage oracle only guarantees `keccak256(data) == key`; it does not constrain the internal RLP nesting structure of `data`. A genuine Ethereum state/storage MPT is bounded to ~64 levels because keys are keccak-hashed, but nothing in `TrieNode::decode` enforces that assumption — a dispute-game participant supplying preimages to the fault-proof program's oracle can hand it a single malformed "trie node" blob whose nested `Extension` shells recurse far beyond any real trie depth.

### Impact Explanation
A dispute-game participant (challenger/prover providing preimages consumed by the fault-proof program's `TrieProvider`) can trigger unbounded recursion in `TrieNode::decode`/`open`, crashing the fault-proof program via stack overflow before it computes the correct output root. This directly falls into the in-scope "fault-proof program and MPT" category and can result in the honest party being unable to execute/verify the claim, risking an incorrect output root prevailing in a dispute — a fund-theft/fraudulent-withdrawal vector, or at minimum a denial of the fault-proof program.

### Likelihood Explanation
Exploitation requires only constructing one RLP blob with deeply nested short `Extension` node encodings and getting it accepted as a preimage (any bytes whose keccak256 matches the referenced hash are accepted by the oracle's preimage contract — the attacker controls the bytes, so the hash is simply computed from their chosen bytes). No privileged access is needed beyond being a dispute-game participant able to supply preimage data, which is an explicitly in-scope actor.

### Recommendation
Add an explicit maximum recursion/nesting depth (bounded to realistic MPT depth, e.g. 64–128) to `TrieNode::decode`, `open`, `unblind`, `insert`, and `delete`, returning a decode/traversal error once exceeded, analogous to the `MAX_CBOR_NESTING_DEPTH` guard already used in `crates/proof/tee/registrar/src/cbor.rs`: [5](#0-4) 

### Proof of Concept
1. Construct nested `TrieNode::Extension { prefix, node }` values where each inner `node` is itself an RLP-encoded `Extension` whose encoded length stays under 32 bytes (so it is embedded inline rather than blinded per `Encodable::encode`'s `node.length() >= B256::ZERO.len()` check).
2. Nest N (e.g., 100,000) such extensions into a single byte blob; store it as a preimage keyed by its own `keccak256` hash.
3. Have a dispute-game participant supply this blob to the fault-proof program's preimage oracle as the value at the trie root (or any node) hash it is asked to resolve.
4. When the fault-proof program calls `TrieNode::decode` (via `OracleL1ChainProvider::trie_node_by_hash`) or subsequently `TrieNode::open` on the node, the recursive descent through N inline `Extension` levels overflows the stack, crashing the fault-proof program process.

### Citations

**File:** crates/proof/mpt/src/node.rs (L124-138)
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
```

**File:** crates/proof/mpt/src/node.rs (L153-182)
```rust
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

**File:** crates/proof/mpt/src/node.rs (L454-461)
```rust
            PREFIX_EXTENSION_EVEN | PREFIX_EXTENSION_ODD => {
                // Extension node
                let extension_node_value = Self::decode(buf).map_err(TrieNodeError::RLPError)?;
                Ok(Self::Extension {
                    prefix: unpack_path_to_nibbles(first, path[1..].as_ref()),
                    node: Box::new(extension_node_value),
                })
            }
```

**File:** crates/proof/proof/src/l1/chain_provider.rs (L125-143)
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
}
```

**File:** crates/proof/tee/registrar/src/cbor.rs (L13-16)
```rust
const MAX_CBOR_NESTING_DEPTH: usize = 64;
const MAX_PCRS: usize = 32;
const MAX_CABUNDLE_CERTS: usize = 32;
const MAX_CABUNDLE_CERT_BYTES: usize = 1024;
```
