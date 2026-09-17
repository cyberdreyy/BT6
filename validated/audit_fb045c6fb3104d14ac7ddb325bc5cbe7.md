## Title
Uncontrolled Recursion in Merkle-Patricia Trie Walker Causes Stack Exhaustion in the Fault-Proof Program - (File: `crates/proof/mpt/src/node.rs`)

### Summary
The libxml2 `CVE-2026-0990` bug class is "uncontrolled recursion via an attacker-supplied linked structure resolved with no depth limit, causing stack exhaustion / crash." Base's `TrieNode` Merkle-Patricia-Trie walker in the fault-proof program exhibits the same pattern: it recursively unblinds and traverses a chain of trie nodes fetched from an oracle, with no maximum-depth bound, where each node's child pointer is attacker-influenced key material resolved against the preimage oracle.

### Finding Description
`TrieNode::open`, `TrieNode::insert`, `TrieNode::unblind`, `TrieNode::collapse_if_possible`, and `OrderedListWalker::fetch_leaves` are all mutually/self recursive with no depth counter or maximum-recursion guard: [1](#0-0) 

Each `Extension`/`Blinded` node's child is resolved by calling `fetcher.trie_node_by_hash(commitment)`: [2](#0-1) 

In the fault-proof program, this fetcher is backed by the preimage oracle, which only verifies that the value hashes to the requested key — it performs no depth or content validation beyond RLP decodability: [3](#0-2) [4](#0-3) 

Because the oracle is keyed by `keccak256(value)`, any party supplying preimages during host preimage-serving (e.g. a challenger/defender providing account/storage proof nodes for a dispute game, or L1/L2 state used during derivation) can trivially construct a long, valid chain of nested `Extension`/`Branch` nodes (`TrieNode::Extension { node: Box<Self::Blinded{...}> }` chained thousands of levels deep, each with a correct keccak256 hash — no collision required, since the attacker is only proving membership/traversal of their own crafted, but validly-hashed, node chain) that the walker will recurse through without bound. `OrderedListWalker::fetch_leaves` exhibits the identical unbounded recursive pattern when traversing derivation-relevant ordered lists (e.g., transaction lists) built from an MPT root: [5](#0-4) 

This is structurally analogous to `xmlCatalogXMLResolveURI`'s unbounded recursive resolution of attacker-controlled catalog entries: a chain of self/cross-referencing nodes is walked with no recursion-depth ceiling, and the terminal condition is entirely attacker-controlled data rather than a fixed bound.

### Impact Explanation
The fault-proof program (`crates/proof/proof`, `crates/proof/mpt`) is executed either natively by the host or inside the deterministic fault-proof VM (op-program-style) as part of dispute-game resolution and safe-head derivation. A stack overflow while walking a maliciously deep, but hash-valid, trie chain causes the process to crash (SIGSEGV / VM trap) before it can compute an output root. This is a concrete Base-reachable Medium/High impact: it can prevent the fault-proof program from producing a valid provable output root for a given claim, effectively halting/DoS'ing dispute-game resolution for the affected block/claim (node halt / inability to derive a correct provable output), which is one of the explicitly in-scope impact categories.

### Likelihood Explanation
Exploitation requires only the ability to submit account/storage/state trie preimages that the fault-proof program will walk — reachable by any dispute-game participant supplying proof data, or via L1/L2 derivation inputs that are ultimately resolved through `TrieProvider::trie_node_by_hash`. Constructing a deeply nested but hash-valid `Extension`/`Blinded` chain requires no cryptographic break, only ordinary keccak256 computation, making this a low-cost, deterministic trigger. This raises it above a purely theoretical concern, though it is conditioned on the specific proof/derivation path being reached with attacker-supplied preimages during a dispute.

### Recommendation
Add an explicit maximum recursion/traversal depth (mirroring Ethereum's real-world MPT depth bounds, e.g. 64–ish nibbles for account tries, larger but still bounded for other list encodings) to `TrieNode::open`, `TrieNode::insert`, `TrieNode::unblind`-driven recursion, and `OrderedListWalker::fetch_leaves`, returning a `TrieNodeError` once the bound is exceeded instead of recursing indefinitely. Alternatively, convert these traversal functions to explicit iterative work-stacks to remove native call-stack dependence entirely.

### Proof of Concept
1. As a dispute-game participant/host preimage provider, construct a chain of `TrieNode::Extension { prefix: <1 nibble>, node: Box::new(TrieNode::Blinded { commitment: hash_of_next }) }` nodes, N levels deep (N large enough to exceed the runtime stack, e.g. tens of thousands), each correctly `keccak256`-hashed so the oracle's `check_preimage`/hash verification passes.
2. Register these preimages into the `SharedKeyValueStore`/oracle under their real keccak256 keys, and set the outermost hash as the reachable trie root for an account/storage proof or ordered list used by the fault-proof program (e.g. via `OracleL1ChainProvider`/`OracleL2ChainProvider::trie_node_by_hash`).
3. Trigger a code path that calls `TrieNode::open`/`OrderedListWalker::hydrate` on this root (e.g., account/storage proof resolution during derivation or dispute-game execution).
4. Observe the fault-proof program crash via stack overflow (`crates/utilities/cli/src/sigsegv.rs`'s handler even documents this exact "most backtraces are stack overflow, most stack overflows are from recursion" scenario) before an output root can be produced [6](#0-5) .

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

**File:** crates/proof/mpt/src/node.rs (L153-181)
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

**File:** crates/proof/mpt/src/list_walker.rs (L86-127)
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
```

**File:** crates/utilities/cli/src/sigsegv.rs (L158-160)
```rust
        // Begin elaborating return addrs into symbols and writing them directly to stderr
        // Most backtraces are stack overflow, most stack overflows are from recursion
        // Check for cycles before writing 250 lines of the same ~5 symbols
```
