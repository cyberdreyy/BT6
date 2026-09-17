### Title
Unbounded recursion in `TrieNode::decode` allows a stack-overflow DoS of the fault-proof program via attacker-crafted preimage data - (File: `crates/proof/mpt/src/node.rs`)

### Summary
`TrieNode::decode` (used by the fault-proof program to decode Merkle-Patricia-Trie node preimages fetched from the preimage oracle) recurses through `try_decode_leaf_or_extension_payload` → `Self::decode` for `Extension` nodes, and through `Vec::<Self>::decode` for `Branch` nodes, with no explicit recursion-depth limit, unlike the CBOR decoder elsewhere in the codebase (`crates/proof/tee/registrar/src/cbor.rs`), which enforces `MAX_CBOR_NESTING_DEPTH`. [1](#0-0) [2](#0-1) [3](#0-2) 

### Finding Description
`TrieNode::decode` dispatches on RLP list length: a `BRANCH_LIST_LENGTH` (17) list recurses via `Vec::<Self>::decode`, and a `LEAF_OR_EXTENSION_LIST_LENGTH` (2) list calls `try_decode_leaf_or_extension_payload`, which for an `Extension` node recursively calls `Self::decode` again on the child pointer bytes. [4](#0-3) 
This decoder is invoked on preimage bytes fetched by the fault-proof program's `TrieProvider` implementations (`OracleL1ChainProvider::trie_node_by_hash`, `OracleL2ChainProvider::trie_node_by_hash`), which pull data directly from the preimage oracle keyed by `keccak256`. [5](#0-4) [6](#0-5) 
Because the oracle's keccak256 preimage type is content-addressed by the hash of the submitted bytes, a dispute-game participant proposing a large-preimage submission controls the raw bytes entirely; the fault-proof program will decode whatever bytes hash to the requested key. No depth counter is threaded through `TrieNode::decode`/`try_decode_leaf_or_extension_payload`/`Vec::<Self>::decode`, in contrast to the explicit, tested depth-bounding pattern used for CBOR parsing elsewhere in the repo (`CborItem::read_at`, which rejects nesting beyond `MAX_CBOR_NESTING_DEPTH`). [3](#0-2) 

This mirrors the CVE-2025-65410 bug class: attacker-controlled input drives an unbounded/insufficiently-bounded recursive parser, leading to native stack exhaustion (SIGSEGV/DoS) rather than a graceful decode error.

### Impact Explanation
A stack overflow inside the fault-proof program during dispute-game execution would crash the process performing the state-transition verification (op-program/Cannon-style fault proof, run on both L1 and L2 trie providers). This can prevent a legitimate output-root computation from completing, i.e. block the fault-proof program from producing a provable output, which maps to a "node halt / wrong provable output root" impact category for the dispute-game participant path.

However, this claim carries meaningful uncertainty: in a well-formed, canonical Ethereum-style Merkle-Patricia-Trie with 32-byte hashed keys, real Extension/Branch chains are naturally bounded to ~64 nibbles of path depth, which is far too shallow to overflow a normal thread stack (unlike GNU Unrtf's filename-driven recursion, which had no such natural ceiling). The genuinely exploitable case requires the attacker to supply preimage bytes that are *not* constrained by an actual on-chain trie topology — i.e., bytes whose keccak256 happens to be requested by the program as a node commitment but which the attacker crafted purely to satisfy the RLP shape checks (17-element list / 2-element list) at every recursion level. Confirming this is genuinely reachable would require verifying whether the large-preimage-proposal mechanism used by the dispute contract allows a participant to substitute a hash that the fault-proof program will actually dereference as a real state/storage/account trie pointer during a specific execution trace, which was not fully verifiable from the available code alone.

### Likelihood Explanation
Medium-low. The recursive call sites are unguarded, so the code-level defect is real and directly analogous to the CVE's bug class (crafted input to an unbounded/weakly-bounded recursive decoder). But triggering deep enough recursion to actually overflow the stack requires the attacker's crafted bytes to be dereferenced by the honest fault-proof trace at a specific hash the program will request, and to chain many levels of validly-shaped (17- or 2-element) RLP lists — a much stronger precondition than the CVE's simple "attacker directly supplies filename string" case.

### Recommendation
Add an explicit recursion-depth counter/limit to `TrieNode::decode` and `try_decode_leaf_or_extension_payload` (threaded through `Vec::<Self>::decode` calls for branch children), analogous to `MAX_CBOR_NESTING_DEPTH` in `crates/proof/tee/registrar/src/cbor.rs`, and return a decode error once a reasonable bound (e.g., 64–128, matching the maximum possible nibble-path depth of a real MPT) is exceeded.

### Proof of Concept
Not independently verified end-to-end. A conceptual PoC would construct nested RLP bytes of the form:
`rlp([path, rlp([path, rlp([path, ... ])])])`
each individually shaped as a valid 2-element (`LEAF_OR_EXTENSION_LIST_LENGTH`) RLP list so that `TrieNode::decode` keeps recursing through `try_decode_leaf_or_extension_payload` → `Self::decode`, repeated until the call stack of the fault-proof program overflows, then arrange (via the large-preimage-proposal path or a controlled account/storage layout) for the program to request `keccak256` of these bytes as a trie-node commitment during dispute execution. Full exploitability was not confirmed because the exact mechanism by which a participant can force the program to dereference such attacker-chosen bytes as a legitimate trie pointer was not traced through the dispute-game/preimage-oracle contract logic in this session.

### Citations

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
