## Analysis

CVE-2022-20785 is a **malformed-input parser DoS**: ClamAV's HTML parser hangs/crashes when fed a maliciously structured document because the parser recurses/loops over attacker-shaped nested content without a depth or resource bound. The matching bug class to search for in Base is: *a parser/traversal routine over untrusted, attacker-shaped nested data with no depth limit, reachable through an in-scope path (the fault-proof program and MPT)*.

I found this pattern in the Merkle-Patricia-Trie crate used by the fault-proof program, contrasted against the CBOR parser in the same repo, which *does* enforce an explicit nesting-depth cap — showing the project is aware of this bug class but did not apply the same mitigation to the MPT trie-walking code. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) 

### Title
Unbounded recursion in `OrderedListWalker::fetch_leaves` / `TrieNode` traversal permits stack-exhaustion DoS of the fault-proof program via a pathologically-shaped MPT - (File: `crates/proof/mpt/src/list_walker.rs`)

### Summary
`OrderedListWalker::fetch_leaves` (used to derive the ordered transactions/receipts list during output-root/fault-proof verification) and the sibling `TrieNode::open`/`insert`/`delete`/`unblind` methods recurse into child/blinded trie nodes with **no depth limit**. Each `Blinded` node is resolved by fetching its preimage through `TrieProvider::trie_node_by_hash` (backed by the untrusted preimage oracle in the fault-proof program, `crates/proof/proof/src/l1/chain_provider.rs`) and immediately recursing. A crafted/pathological trie (e.g., a chain of nested `Extension`/`Branch` nodes engineered to share long key prefixes) can force recursion far beyond a safe stack depth, causing a stack overflow and crashing the fault-proof program.

Note the codebase's own CBOR parser (`crates/proof/tee/registrar/src/cbor.rs`) explicitly defends against exactly this bug class with a `MAX_CBOR_NESTING_DEPTH` check on every recursive `read_at` call — this defense was never applied to the MPT trie-walking code path.

### Finding Description
The `fetch_leaves` function walks a `TrieNode` tree recursively:
- `TrieNode::Branch` iterates up to 17 children and recurses into each.
- `TrieNode::Blinded` triggers a preimage fetch (`Self::get_trie_node`) and then unconditionally recurses on the fetched node.
- `TrieNode::Extension` recurses on its single child (after resolving if blinded).

None of these branches track or cap recursion depth. The same unbounded-recursion pattern exists in `TrieNode::open`, `insert`, `delete`, and `unblind` in `crates/proof/mpt/src/node.rs`, all of which recurse through `Blinded` nodes by fetching new preimages and calling themselves again.

Because MPT node identity is content-addressed (`keccak256`), an adversary who controls the shape of the underlying data structure (e.g., a malicious/dishonest party in a dispute-game, or a block producer choosing state/storage trie key material) can engineer a trie with a very long, degenerate extension/branch chain (a well-known technique for producing pathological/near-linear trie shapes by selecting keys with long shared prefixes). Because `fetch_leaves`/`open`/`insert`/`delete` follow every blinded pointer and Recursively unwind the chain without any depth ceiling, walking such a trie during output-root or transaction/receipt-list validation in the fault-proof program can drive recursion depth into the thousands, exhausting the call stack.

This mirrors the bug class in CVE-2022-20785: an attacker-shaped nested input structure drives a parser/traversal routine into unbounded recursive processing, causing a crash/denial of service, rather than being rejected early with a bounded check (as the project itself does for CBOR).

### Impact Explanation
A stack overflow in the fault-proof program halts/crashes the process performing output-root verification or challenge validation — a concrete node/service halt for the dispute-game/fault-proof path (matching the accepted "node halt" / "wrong provable output root" impact category), since the program cannot complete verification of the disputed claim if it crashes mid-traversal.

### Likelihood Explanation
Reaching this requires an attacker to have (or construct) a set of MPT node preimages, hashing correctly, that form a degenerate deep chain and to get the fault-proof program to walk it (e.g., via `OrderedListWalker` deriving transactions/receipts lists, or `TrieNode::open` during state/storage proof traversal) while validating an L1/L2 derived structure. Constructing colliding/long-shared-prefix keys to deepen a trie is a known, previously-documented Ethereum client attack technique, and no code path in `crates/proof/mpt` currently rejects or bounds such structures, unlike the CBOR parser in the same repo which explicitly defends against nested-depth abuse.

### Recommendation
Add an explicit recursion/traversal depth limit (analogous to `MAX_CBOR_NESTING_DEPTH` in `crates/proof/tee/registrar/src/cbor.rs`) to `OrderedListWalker::fetch_leaves` and to `TrieNode::open`/`insert`/`delete`/`unblind`, returning an error (`TrieNodeError`/`OrderedListWalkerError`) once a configured maximum depth is exceeded, instead of recursing unconditionally through every `Blinded`/`Branch`/`Extension` node.

### Proof of Concept
1. Construct a chain of N `TrieNode::Extension` nodes, each RLP-encoded and `keccak256`-hashed, where extension `i` points (via `Blinded { commitment }`) to extension `i+1`'s hash, terminating in a `Leaf`.
2. Register each node's RLP bytes as a preimage keyed by its hash in the `TrieProvider`/oracle backing `OracleL1ChainProvider` (or any `TrieProvider` implementation reachable in the fault-proof program).
3. Set the root to the hash of extension `0`, and invoke `OrderedListWalker::try_new_hydrated(root, &fetcher)` (or `TrieNode::open`/`insert` on the root).
4. For sufficiently large N (bounded only by the total preimage set size, not by any code-enforced limit), the recursive calls in `fetch_leaves`/`open` exhaust the stack, crashing the process — demonstrating the missing depth bound.

### Citations

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

**File:** crates/proof/mpt/src/list_walker.rs (L86-129)
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
