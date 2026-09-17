### Title
Unbounded Recursive Depth in `TrieNode` RLP Decoding/Traversal Leads to Stack-Exhaustion DoS of the Fault-Proof Program - ([File: crates/proof/mpt/src/node.rs])

### Summary
The Merkle-Patricia-Trie node type used by the fault-proof program to decode/walk L1 and L2 state/storage trie preimages recurses once per trie level with no maximum-depth check, mirroring the MaterialX `xi:include` unbounded-recursion bug class (CWE-400): an attacker who controls the preimages fed into the trie (via the untrusted preimage oracle that back-fills `TrieProvider` during a dispute game) can build an arbitrarily deep chain of "extension" nodes and crash the verifier process via stack exhaustion.

### Finding Description
`TrieNode` is a recursive enum (`Leaf`, `Extension { node: Box<Self> }`, `Branch { stack: Vec<Self> }`, `Blinded`) with no depth-tracking parameter anywhere in its API: [1](#0-0) 

Its `Decodable` implementation recurses directly through `Self::decode` for extension-node children and through `Vec::<Self>::decode` for branch children, with zero recursion-depth guard: [2](#0-1) 

Extension-node payload decoding likewise recurses unconditionally into `Self::decode(buf)`: [3](#0-2) 

Beyond decoding, the runtime trie-walking methods `open`, `insert`, and `delete` are also unbounded mutual/self-recursions that call `unblind`, which fetches the *next* node's preimage from an external, attacker-influenced `TrieProvider` and immediately recurses again: [4](#0-3) 

Unlike the sibling CBOR parser in this same codebase — which explicitly enforces `MAX_CBOR_NESTING_DEPTH` on every recursive `read_at` call — [5](#0-4)  the MPT trie module has no analogous bound. In a canonical Ethereum trie the maximum extension-chain depth is naturally limited (≤64, bounded by the 32-byte keccak key length), but this implementation does not enforce that invariant at decode time or at `unblind`/`open` time — it trusts the preimage source to only ever supply canonically-shaped tries. In the fault-proof program's execution model, `TrieProvider`/preimages are supplied through an oracle that is populated during the dispute game from data that untrusted parties (any dispute-game participant) can submit as claimed "preimages," so a party can supply a chain of extension nodes vastly deeper than any real Ethereum trie could produce, exactly analogous to the MaterialX `xi:include` chain in the external report.

### Impact Explanation
If the fault-proof program crashes via stack overflow while walking/decoding a maliciously deep trie preimage chain during dispute-game execution, the program can no longer produce a valid state/storage proof output, which prevents honest resolution of the fault dispute game — a node/verifier halt condition for the specific proof execution, potentially disrupting the challenge/defense of an output root. This matches the "node halt" / "wrong provable output" impact classes called out in the report's validation criteria.

### Likelihood Explanation
The recursive `TrieNode` decode/traversal path is exercised whenever the fault-proof program processes state or storage proofs, and its recursion depth is driven entirely by attacker-suppliable preimage data with no cap, so a moderately sized (not requiring gigabytes, since native stack frames are much larger than MaterialX's) chain of extension nodes is sufficient to exhaust the stack. No special privilege is needed beyond participating as a dispute-game party supplying preimages/claims that get decoded by this module.

### Recommendation
Add an explicit depth parameter (or an iterative decode/walk implementation) to `TrieNode::decode`, `open`, `insert`, `delete`, and `collapse_if_possible`, capping recursion to the maximum theoretically possible depth for a canonical trie (e.g., 64 nibble levels for 32-byte keys), and reject preimages/paths that exceed it — mirroring the `MAX_CBOR_NESTING_DEPTH` guard already used in `crates/proof/tee/registrar/src/cbor.rs`.

### Proof of Concept
Construct a chain of RLP-encoded `TrieNode::Extension` nodes, each pointing (via an inline, non-blinded child, i.e., under 32 bytes when possible, or via `Blinded` commitments resolved through a malicious `TrieProvider`) to the next extension node, nested to a depth of hundreds of thousands of levels, then feed the root through `TrieNode::decode` or through `root_node.open(path, &malicious_fetcher)` where `malicious_fetcher.trie_node_by_hash` always returns the next link in the chain — analogous to the `template.mtlx` chain-generation script in the external report — to overflow the native call stack of the fault-proof program.

### Citations

**File:** crates/proof/mpt/src/node.rs (L83-96)
```rust
    /// An extension node is a 2-item pointer node with the encoding `rlp([encoded_path, key])`
    Extension {
        /// The path prefix of the extension
        prefix: Nibbles,
        /// The pointer to the child node
        node: Box<Self>,
    },
    /// A branch node refers to up to 16 child nodes with the encoding
    /// `rlp([ v0, ..., v15, value ])`
    Branch {
        /// The 16 child nodes and value of the branch.
        stack: Vec<Self>,
    },
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

**File:** crates/proof/mpt/src/node.rs (L453-461)
```rust
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

**File:** crates/proof/mpt/src/node.rs (L567-605)
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
        } else {
            match header.payload_length {
                0 => {
                    buf.advance(header.length());
                    Ok(Self::Empty)
                }
                32 => {
                    let commitment = B256::decode(buf)?;
                    Ok(Self::new_blinded(commitment))
                }
                _ => Err(alloy_rlp::Error::UnexpectedLength),
            }
        }
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
