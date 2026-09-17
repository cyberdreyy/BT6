### Title
Unbounded recursion via cyclic `TrieNode::Blinded` preimages in `TrieNode::insert`/`open`/`delete` — (File: `crates/proof/mpt/src/node.rs`)

### Summary
`TrieNode::unblind` resolves a `Blinded` node by calling `fetcher.trie_node_by_hash(commitment)` and replacing `self` with whatever the fetcher returns, without checking that the returned node is not itself another `Blinded` variant that ultimately points back into a cycle. `TrieNode::insert`, `open`, and `delete` all call `unblind` and then immediately recurse on `self` in the `Self::Blinded { .. }` match arm. If the preimage source returns a node graph that decodes to a `Blinded` node referencing itself (directly, or via a short cycle of `Blinded` nodes), these functions recurse without a depth bound, mirroring the pypdf `TreeObject.insert_child` infinite-loop bug class (CWE-835).

### Finding Description
`unblind` is defined as: [1](#0-0) 

`insert`, `open`, and `delete` each handle the `Self::Blinded` case identically — unblind then recurse on `self`: [2](#0-1) [3](#0-2) [4](#0-3) 

There is no cycle-detection or recursion-depth limit anywhere in this traversal logic. The `TrieProvider` trait that supplies node preimages is implemented over untrusted, externally supplied data paths in the fault-proof program — e.g. the L1/L2 oracle-backed providers decode raw preimage bytes into a `TrieNode` with no structural sanity check beyond RLP well-formedness: [5](#0-4) [6](#0-5) 

Because `blind()` for an already-`Blinded` node simply returns its stored `commitment` field rather than hashing content, a value that decodes into `TrieNode::Blinded { commitment: X }` is a syntactically legitimate preimage for hash `X` as long as its RLP encoding hashes to `X`. If `X`'s associated preimage decodes to `TrieNode::Blinded { commitment: X }` itself (or a short cycle A→B→A), every call to `unblind` followed by recursive `insert`/`open`/`delete` will fetch, decode, and recurse indefinitely, since nothing checks whether the newly unblinded node is still `Blinded` and equal/cyclic to a prior commitment.

### Impact Explanation
`TrieNode` from `base-proof-mpt` is the trie backend for `base-proof-executor`, which underlies the Base fault-proof program (`crates/proof/proof`, `crates/proof/client`). Since traversal/insertion of trie nodes is driven by state root data during execution witness derivation for the fault dispute-game program, a preimage set (attacker-controlled or maliciously crafted L1/L2 data referenced by the disputed state) that forms a cyclic `Blinded` chain can cause the fault-proof program to recurse without bound. This results in either a stack overflow/crash or an effectively infinite loop, preventing the program from producing a provable output root for the corresponding claim — i.e., a node/prover halt for that dispute step, blocking correct dispute-game resolution.

### Likelihood Explanation
Likelihood depends on whether the preimage oracle used in the actual dispute-game/fault-proof deployment permits an adversary to supply or influence the set of state preimages fed to `trie_node_by_hash`. If such preimages are derived exclusively from a verified, honestly-produced L1/L2 state and their integrity is fully constrained by the on-chain data being disputed, crafting a cyclic self-referential `Blinded` node requires the attacker to control a state trie encoding that resolves through the L1/L2 chain providers shown above. I could not fully verify from the available index whether an additional cross-check (e.g., validating decoded node RLP length/kind against expected structural invariants) exists further upstream in `base-proof-executor` or `base-proof-host` that would block this, so likelihood should be treated as uncertain without further live testing.

### Recommendation
Add a recursion/iteration depth bound (or explicit cycle detection) in `TrieNode::insert`, `open`, and `delete` when traversing through `Blinded` nodes, and reject the preimage if the newly resolved node is still `Blinded` referencing the same or a previously seen commitment. This mirrors the upstream pypdf fix, which bounded/guarded `TreeObject.insert_child` against cyclic structures.

### Proof of Concept
1. Construct two RLP-encoded byte sequences `P_A` and `P_B` such that:
   - `keccak256(P_A) == B (commitment B)`, and `P_A` RLP-decodes to `TrieNode::Blinded { commitment: B }`.
   - `keccak256(P_B) == A (commitment A)`, and `P_B` RLP-decodes to `TrieNode::Blinded { commitment: A }`.
2. Register both `A -> P_A` and `B -> P_B` in the preimage store backing a `TrieProvider` implementation (e.g., `OracleL1ChainProvider`/`OracleL2ChainProvider`).
3. Call `TrieNode::new_blinded(A).insert(path, value, &provider)` (or `.open(...)`/`.delete(...)`).
4. Execution enters `Self::Blinded` → `unblind` fetches `P_A` → `self` becomes `Blinded{B}` → recurse → `unblind` fetches `P_B` → `self` becomes `Blinded{A}` → recurse indefinitely, exhausting the call stack or looping forever.

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

**File:** crates/proof/mpt/src/node.rs (L176-181)
```rust
            Self::Blinded { .. } => {
                self.unblind(fetcher)?;
                self.open(path, fetcher)
            }
            Self::Empty => Ok(None),
        }
```

**File:** crates/proof/mpt/src/node.rs (L298-304)
```rust
            Self::Blinded { .. } => {
                // If a blinded node is approached, reveal the node and continue the insertion
                // recursion.
                self.unblind(fetcher)?;
                self.insert(path, value, fetcher)
            }
        }
```

**File:** crates/proof/mpt/src/node.rs (L348-352)
```rust
            Self::Blinded { .. } => {
                self.unblind(fetcher)?;
                self.delete(path, fetcher)
            }
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
