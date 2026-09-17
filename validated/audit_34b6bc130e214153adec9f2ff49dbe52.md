### Title
Unbounded recursion in `TrieNode` traversal/decoding allows stack-overflow DoS of the fault-proof program via a crafted MPT preimage chain - (File: `crates/proof/mpt/src/node.rs`)

### Summary
`base-proof-mpt`'s `TrieNode` implements a hexary Merkle Patricia Trie exactly the way the CVE-2025-55095 bug class describes for USBX's partition mounter: a self-referential, attacker-influenced data structure is walked with plain recursion and no depth/cycle bound, so a crafted preimage chain can force the recursion to run until the call stack overflows.

### Finding Description
`TrieNode::open`, `TrieNode::insert`, `TrieNode::delete`, `TrieNode::unblind`, and `TrieNode::collapse_if_possible` in [1](#0-0)  and [2](#0-1)  all recurse into child/blinded nodes with no depth counter and no cycle/visited-set tracking — directly analogous to `_ux_host_class_storage_media_mount()`'s unbounded self-recursion on attacker-supplied partition entries. The same pattern exists in `OrderedListWalker::fetch_leaves` at [3](#0-2) .

The recursion is driven by `TrieNode::Blinded { commitment }` nodes, which are resolved via `unblind()` by calling out to a `TrieProvider`/preimage oracle keyed by `keccak256(value)`y [4](#0-3) . In the fault-proof / dispute-game path, preimages are supplied by an untrusted party (a dispute-game participant / host) and are only checked for `keccak256(value) == key` self-consistency — never for real-world plausibility or trie shape — as seen in `Oracle::check_preimage` [5](#0-4)  and `PreimageStore::check_preimage` [6](#0-5) , and similarly in the L1/L2 oracle-backed `TrieProvider` implementations [7](#0-6) .

Because self-consistency (`hash(value) == key`) is trivial to satisfy for an attacker who freely chooses `value` (e.g., a chain of `Extension { node: Blinded(hash_of_next) }` nodes), a party controlling the preimage set can construct an arbitrarily long chain of blinded/extension nodes that is not bounded by the true 64-nibble depth of a real Ethereum account/storage trie. When `TrieDB` (used during fault-proof block re-execution, see `TrieDB::new` seeding `root_node` from an untrusted `state_root` [8](#0-7) ) or `OrderedListWalker` walks such a chain to resolve an account/storage/receipt/transaction lookup, each hop causes another stack frame via `unblind` → `open`/`insert`/`delete`/`fetch_leaves`, with no limit.

### Impact Explanation
The fault-proof program (`base-proof-executor`/`base-proof-mpt`/host programs under `crates/proof/`) runs in constrained, single-threaded execution environments (zkVM, Cannon/Asterisc-style VM, TEE enclave) with fixed, often small stack sizes. Triggering unbounded recursion during trie traversal causes a stack overflow, crashing/halting the fault-proof program before it can produce a valid output. In an interactive fault dispute game this can prevent the honest party's claim from being provably resolved, or force the challenge to fail to produce a step trace, effectively enabling a Medium-severity denial-of-service against the dispute-resolution / stateless-execution path — matching the CVSS 4.2 rating and impact class of the original CVE (a crash caused by malformed, recursively-structured input with no depth limiting).

### Likelihood Explanation
Reaching this requires supplying a preimage chain that is self-consistent (each node's keccak256 hash matches the key requested), which is trivial for anyone who controls the preimage source feeding the fault-proof/host program (a dispute-game participant supplying preimages, or a host generating a witness from adversarial data). No cryptographic break is needed — only ordinary hashing of attacker-chosen bytes, which is computationally free. The barrier to exploitation is low; the main uncertainty is how many recursion levels are needed to exhaust the specific runtime's stack (zkVM/Cannon/TEE), which was not verified in this codebase.

### Recommendation
Add an explicit maximum recursion/iteration depth (e.g., bound to the real-world maximum MPT depth, ~64–128) or convert `TrieNode::open`/`insert`/`delete`/`unblind`/`collapse_if_possible` and `OrderedListWalker::fetch_leaves` to iterative (loop-based) traversal with an explicit work-stack, rejecting/erroring out once the bound is exceeded, so an adversarial preimage chain cannot exhaust the call stack.

### Proof of Concept
Not independently executed; based on static analysis of the recursive implementation. Conceptually:
1. Construct `N` chained `TrieNode::Extension` (or `Branch`) nodes `n_0..n_N`, where `n_i` contains `Blinded(keccak256(encode(n_{i-1})))`.
2. Register each `(keccak256(encode(n_i)), encode(n_i))` pair into the preimage oracle/witness store consumed by the fault-proof program (passes `check_preimage` validation trivially since hashes are self-consistent).
3. Set the claimed state root (or list root) to `keccak256(encode(n_N))`.
4. When the fault-proof program resolves any key via `TrieNode::open`/`OrderedListWalker::fetch_leaves`, it will recurse `N` times through `unblind`, exhausting the stack for sufficiently large `N`. [9](#0-8) [3](#0-2)

### Citations

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

**File:** crates/proof/mpt/src/node.rs (L316-353)
```rust
    pub fn delete<F: TrieProvider>(&mut self, path: &Nibbles, fetcher: &F) -> TrieNodeResult<()> {
        match self {
            Self::Empty => Err(TrieNodeError::KeyNotFound),
            Self::Leaf { prefix, .. } => {
                if path == prefix {
                    *self = Self::Empty;
                    Ok(())
                } else {
                    Err(TrieNodeError::KeyNotFound)
                }
            }
            Self::Extension { prefix, node } => {
                let shared_nibbles = path.common_prefix_length(prefix);
                if shared_nibbles < prefix.len() {
                    return Err(TrieNodeError::KeyNotFound);
                } else if shared_nibbles == path.len() {
                    *self = Self::Empty;
                    return Ok(());
                }

                node.delete(&path.slice(prefix.len()..), fetcher)?;

                // Simplify extension if possible after the deletion
                self.collapse_if_possible(fetcher)
            }
            Self::Branch { stack } => {
                let branch_nibble = path.get(0).ok_or(TrieNodeError::PathTooShort)? as usize;
                stack[branch_nibble].delete(&path.slice(BRANCH_NODE_NIBBLES..), fetcher)?;

                // Simplify the branch if possible after the deletion
                self.collapse_if_possible(fetcher)
            }
            Self::Blinded { .. } => {
                self.unblind(fetcher)?;
                self.delete(path, fetcher)
            }
        }
    }
```

**File:** crates/proof/mpt/src/list_walker.rs (L86-128)
```rust
    /// Traverses a [`TrieNode`], returning all values of child [`TrieNode::Leaf`] variants.
    fn fetch_leaves(
        trie_node: &TrieNode,
        fetcher: &F,
    ) -> OrderedListWalkerResult<VecDeque<(Bytes, Bytes)>> {
        match trie_node {
            TrieNode::Branch { stack } => {
                let mut leaf_values = VecDeque::with_capacity(stack.len());
                for item in stack {
                    match item {
                        TrieNode::Blinded { commitment } => {
                            // If the string is a hash, we need to grab the preimage for it and
                            // continue recursing.
                            let trie_node = Self::get_trie_node(commitment.as_ref(), fetcher)?;
                            leaf_values.append(&mut Self::fetch_leaves(&trie_node, fetcher)?);
                        }
                        TrieNode::Empty => { /* Skip over empty nodes, we're looking for values. */
                        }
                        item => {
                            // If the item is already retrieved, recurse on it.
                            leaf_values.append(&mut Self::fetch_leaves(item, fetcher)?);
                        }
                    }
                }
                Ok(leaf_values)
            }
            TrieNode::Leaf { prefix, value } => {
                Ok(vec![(prefix.to_vec().into(), value.clone())].into())
            }
            TrieNode::Extension { node, .. } => {
                // If the node is a hash, we need to grab the preimage for it and continue
                // recursing. If it is already retrieved, recurse on it.
                match node.as_ref() {
                    TrieNode::Blinded { commitment } => {
                        let trie_node = Self::get_trie_node(commitment.as_ref(), fetcher)?;
                        Ok(Self::fetch_leaves(&trie_node, fetcher)?)
                    }
                    node => Ok(Self::fetch_leaves(node, fetcher)?),
                }
            }
            TrieNode::Empty => Ok(VecDeque::new()),
            _ => Err(TrieNodeError::InvalidNodeType.into()),
        }
```

**File:** crates/proof/tee/nitro-enclave/src/oracle.rs (L57-77)
```rust
    fn check_preimage(key: &PreimageKey, value: &[u8]) -> crate::Result<()> {
        let expected_hash: Option<[u8; 32]> = match key.key_type() {
            PreimageKeyType::Keccak256 => Some(keccak256(value).0),
            PreimageKeyType::Sha256 => Some(sha2::Sha256::digest(value).into()),
            // Blob keys are `keccak256(commitment ++ z)` and precompile keys are
            // `keccak256(address ++ input)` — neither can be re-derived from the
            // stored value alone, so we skip verifying them here and instead verify them
            // during derivation.
            PreimageKeyType::Local
            | PreimageKeyType::GlobalGeneric
            | PreimageKeyType::Blob
            | PreimageKeyType::Precompile => None,
        };

        if let Some(hash) = expected_hash
            && key != &PreimageKey::new(hash, key.key_type())
        {
            return Err(NitroError::InvalidPreimage(*key));
        }
        Ok(())
    }
```

**File:** crates/proof/zk/utils/src/witness/preimage_store.rs (L79-92)
```rust
/// Check that the preimage matches the expected hash.
pub fn check_preimage(key: &PreimageKey, value: &[u8]) -> PreimageOracleResult<()> {
    if let Some(expected_hash) = match key.key_type() {
        PreimageKeyType::Keccak256 => Some(keccak256(value).0),
        PreimageKeyType::Sha256 => Some(sha2::Sha256::digest(value).into()),
        PreimageKeyType::Local | PreimageKeyType::GlobalGeneric => None,
        PreimageKeyType::Precompile => unimplemented!("Precompile not supported in zkVM"),
        PreimageKeyType::Blob => unreachable!("Blob keys validated in blob witness"),
    } && key != &PreimageKey::new(expected_hash, key.key_type())
    {
        return Err(PreimageOracleError::InvalidPreimageKey);
    }
    Ok(())
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

**File:** crates/proof/executor/src/db/mod.rs (L52-66)
```rust
impl<F, H> TrieDB<F, H>
where
    F: TrieDBProvider,
    H: TrieHinter,
{
    /// Creates a new [`TrieDB`] with the given root node.
    pub fn new(parent_block_header: Sealed<Header>, fetcher: F, hinter: H) -> Self {
        Self {
            root_node: TrieNode::new_blinded(parent_block_header.state_root),
            storage_roots: Default::default(),
            parent_block_header,
            fetcher,
            hinter,
        }
    }
```
