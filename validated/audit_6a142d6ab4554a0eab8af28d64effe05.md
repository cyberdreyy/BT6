### Title
Unbounded recursion in `TrieNode` decode/traversal causes uncatchable stack-overflow abort of the fault-proof program on attacker-crafted MPT preimages - (File: `crates/proof/mpt/src/node.rs`)

### Summary
The joi advisory describes a recursive schema validator (`link()`) that has no depth limit, so a deeply nested attacker-supplied object causes an uncaught `RangeError` (stack overflow) instead of a normal validation error, crashing or confusing the caller. `base-proof-mpt`'s `TrieNode` implements the same anti-pattern in Rust: its RLP `Decodable::decode`, `open`, `unblind`, and `collapse_if_possible` methods recurse into child/extension nodes with no depth bound, relying entirely on the caller supplying "well-formed" trie data. In the fault-proof program, trie-node preimages are fetched from an oracle (`OracleL1ChainProvider`/`OracleL2ChainProvider`) whose only integrity check is that the returned bytes hash to the requested key — the *shape* of the decoded structure is otherwise unconstrained. A dispute-game participant acting as (or controlling) the preimage host can serve a chain of nested `Extension`/`Blinded` nodes, each satisfying its keccak preimage check, that is arbitrarily deep. Decoding or traversing this structure recurses without bound and overflows the native stack — an event Rust cannot catch with `Result`/`?`, so it aborts the process exactly like joi's uncatchable `RangeError`.

### Finding Description
`TrieNode::open` recurses into `Extension`/`Blinded`/`Branch` variants without any depth counter: [1](#0-0) 

`unblind` calls out to the `TrieProvider` and then the caller re-enters `open`/`decode` on the newly fetched node, so a chain of blinded nodes causes one recursive fetch+decode per level with no cap on chain length: [2](#0-1) 

RLP decoding of `Extension` nodes is directly recursive as well — `try_decode_leaf_or_extension_payload` calls `Self::decode(buf)` on the child, and `Decodable::decode` dispatches back into it for nested lists: [3](#0-2) [4](#0-3) 

`collapse_if_possible` similarly recurses when unblinding a single-child branch: [5](#0-4) 

The trie preimages consumed by these recursive routines are fetched from an oracle that only verifies the keccak256 hash of the returned bytes, not the resulting trie shape/depth: [6](#0-5) [7](#0-6) 

This oracle-backed `TrieProvider` feeds the `TrieDB` used by the fault-proof executor to compute the post-state root for every block during dispute-game execution: [8](#0-7) 

Because none of `open`, `unblind`, `decode`, or `collapse_if_possible` enforce a maximum nesting/chain depth, an attacker who controls the preimages served to the fault-proof program (a dispute-game participant, since preimage content is only integrity-checked by hash, not shape) can construct an extension/blinded-node chain deep enough to exhaust the native call stack. This directly parallels the joi issue: a `Result`-returning API (`TrieNodeResult<_>`) is bypassed by a stack overflow, which in Rust is an unrecoverable, uncatchable process abort rather than a propagated `Err`.

### Impact Explanation
A stack-overflow abort during MPT traversal/decoding crashes the entire fault-proof program mid-execution. Since the correctness of the dispute-game output root depends on this program completing execution, an attacker-controlled preimage host can prevent the honest party's program instance from ever computing a result, denying that party the ability to produce a valid claim/response in the dispute game. This falls squarely into the "node halt" / "wrong provable output root" impact category: the honest run is forced to abort rather than yield a validated result, undermining the game's ability to resolve to the correct output root within its interactive/verification flow.

### Likelihood Explanation
The only constraint on the served preimage bytes is that they hash to the requested key — nothing constrains how many attacker-chosen extension/blinded nodes can be chained before reaching a terminal leaf. Crafting such a chain is a purely computational (offline) task requiring only finding preimages for chosen keccak digests along a controlled path, which is within reach of a dispute-game participant serving preimages for their own claim/response. No cryptographic break is needed beyond standard preimage construction of the kind already used to build legitimate trie nodes.

### Recommendation
Introduce an explicit recursion/traversal depth limit (mirroring Ethereum's practical trie depth bounds, ~64-128 nibbles) in `TrieNode::open`, `unblind`, `collapse_if_possible`, and the recursive `Decodable::decode` path for `Extension` nodes, returning a `TrieNodeError` variant once the bound is exceeded instead of recursing further. Alternatively, convert these traversals to iterative (loop-based) implementations using an explicit stack so bounded heap-allocated storage is used instead of the native call stack, eliminating the possibility of an uncatchable overflow abort.

### Proof of Concept
1. Construct nodes `N_0 .. N_k` where `N_i` is `TrieNode::Extension { prefix: <1 nibble>, node: Box::new(Blinded(hash(N_{i+1}))) }`, and `N_k` is a normal `Leaf`.
2. Serve each `N_i`'s RLP encoding from a `TrieProvider`/preimage oracle keyed by `keccak256(rlp(N_i))`, matching the interface used by `OracleL1ChainProvider::trie_node_by_hash` / `OracleL2ChainProvider::trie_node_by_hash`.
3. Set the fault-proof program's claimed state root to `keccak256(rlp(N_0))` and trigger any state access requiring `TrieNode::open`/`unblind` traversal down this path (e.g., an account or storage lookup as in `eip_2935_history_lookup`, `crates/proof/proof/src/eip2935.rs:51-60`).
4. As `k` grows (tens of thousands of levels, easily satisfiable given each level only needs a fresh preimage), the recursive `unblind`→`open` (or `Decodable::decode`) call chain exhausts the stack and the process aborts with SIGSEGV/stack overflow instead of returning a `TrieNodeError`, exactly analogous to joi's uncaught `RangeError` from deeply nested `link()` schemas.

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

**File:** crates/proof/mpt/src/node.rs (L363-432)
```rust
    fn collapse_if_possible<F: TrieProvider>(&mut self, fetcher: &F) -> TrieNodeResult<()> {
        match self {
            Self::Extension { prefix, node } => match node.as_mut() {
                Self::Extension { prefix: child_prefix, node: child_node } => {
                    // Double extensions are collapsed into a single extension.
                    let new_prefix = Nibbles::from_nibbles_unchecked(
                        [prefix.to_vec(), child_prefix.to_vec()].concat(),
                    );
                    *self = Self::Extension { prefix: new_prefix, node: child_node.clone() };
                }
                Self::Leaf { prefix: child_prefix, value: child_value } => {
                    // If the child node is a leaf, convert the extension into a leaf with the full
                    // path.
                    let new_prefix = Nibbles::from_nibbles_unchecked(
                        [prefix.to_vec(), child_prefix.to_vec()].concat(),
                    );
                    *self = Self::Leaf { prefix: new_prefix, value: child_value.clone() };
                }
                Self::Empty => {
                    // If the child node is empty, convert the extension into an empty node.
                    *self = Self::Empty;
                }
                _ => {
                    // If the child is a (blinded?) branch then no need for collapse
                    // because deletion did not collapse the (blinded?) branch
                }
            },
            Self::Branch { stack } => {
                // Count non-empty children
                let mut non_empty_children = stack
                    .iter_mut()
                    .enumerate()
                    .filter(|(_, node)| !matches!(node, Self::Empty))
                    .collect::<Vec<_>>();

                if non_empty_children.len() == 1 {
                    let (index, non_empty_node) = &mut non_empty_children[0];

                    // If only one non-empty child and no value, convert to extension or leaf
                    match non_empty_node {
                        Self::Leaf { prefix, value } => {
                            let new_prefix = Nibbles::from_nibbles_unchecked(
                                [&[*index as u8], prefix.to_vec().as_slice()].concat(),
                            );
                            *self = Self::Leaf { prefix: new_prefix, value: value.clone() };
                        }
                        Self::Extension { prefix, node } => {
                            let new_prefix = Nibbles::from_nibbles_unchecked(
                                [&[*index as u8], prefix.to_vec().as_slice()].concat(),
                            );
                            *self = Self::Extension { prefix: new_prefix, node: node.clone() };
                        }
                        Self::Branch { .. } => {
                            *self = Self::Extension {
                                prefix: Nibbles::from_nibbles_unchecked([*index as u8]),
                                node: Box::new(non_empty_node.clone()),
                            };
                        }
                        Self::Blinded { .. } => {
                            non_empty_node.unblind(fetcher)?;
                            self.collapse_if_possible(fetcher)?;
                        }
                        _ => {}
                    };
                }
            }
            _ => {}
        }
        Ok(())
    }
```

**File:** crates/proof/mpt/src/node.rs (L439-469)
```rust
    fn try_decode_leaf_or_extension_payload(buf: &mut &[u8]) -> TrieNodeResult<Self> {
        // Decode the path and value of the leaf or extension node.
        let path = Bytes::decode(buf).map_err(TrieNodeError::RLPError)?;
        let Some(first_byte) = path.first() else {
            return Err(TrieNodeError::InvalidNodeType);
        };
        let first_nibble = first_byte >> NIBBLE_WIDTH;
        let first = match first_nibble {
            PREFIX_EXTENSION_ODD | PREFIX_LEAF_ODD => Some(first_byte & 0x0F),
            PREFIX_EXTENSION_EVEN | PREFIX_LEAF_EVEN => None,
            _ => return Err(TrieNodeError::InvalidNodeType),
        };

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
            PREFIX_LEAF_EVEN | PREFIX_LEAF_ODD => {
                // Leaf node
                let value = Bytes::decode(buf).map_err(TrieNodeError::RLPError)?;
                Ok(Self::Leaf { prefix: unpack_path_to_nibbles(first, path[1..].as_ref()), value })
            }
            _ => Err(TrieNodeError::InvalidNodeType),
        }
    }
```

**File:** crates/proof/mpt/src/node.rs (L567-604)
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

**File:** crates/proof/proof/src/l2/chain_provider.rs (L173-191)
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
}
```

**File:** crates/proof/executor/src/db/mod.rs (L97-121)
```rust
    /// Applies a [`BundleState`] changeset to the [`TrieNode`] and recomputes the state root hash.
    ///
    /// ## Takes
    /// - `bundle`: The [`BundleState`] changeset to apply to the trie DB.
    ///
    /// ## Returns
    /// - `Ok(B256)`: The new state root hash of the trie DB.
    /// - `Err(_)`: If the state root hash could not be computed.
    pub fn state_root(&mut self, bundle: &BundleState) -> TrieDBResult<B256> {
        debug!(target: "client_executor", "Recomputing state root");

        // Update the accounts in the trie with the changeset.
        self.update_accounts(bundle)?;

        // Recompute the root hash of the trie.
        let root = self.root_node.blind();

        debug!(
            target: "client_executor",
            "Recomputed state root: {root}",
        );

        // Extract the new state root from the root node.
        Ok(root)
    }
```
