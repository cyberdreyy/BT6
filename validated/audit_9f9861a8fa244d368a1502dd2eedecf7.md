### Title
Uncontrolled recursion depth in `TrieNode::decode` causes stack overflow on attacker-supplied fault-proof trie preimages - (File: `crates/proof/mpt/src/node.rs`)

### Summary
`TrieNode::decode` and its helper `try_decode_leaf_or_extension_payload` recursively decode nested RLP list nodes (extension → extension → … or branch → branch → …) with no bound on nesting depth. In the fault-proof program, the raw bytes behind any trie-node hash are supplied by an adversarial party (the dispute-game opponent / preimage host), which fully controls the content as long as `keccak256(content) == requested_key`. A crafted preimage containing many levels of embedded (unblinded, <32-byte) extension/branch nodes drives `TrieNode::decode` into deep, attacker-controlled recursion within a single call, which can exhaust the stack and crash the process performing state derivation — this is the direct structural analogue of the ParquetSharp `DecimalConverter.ReadDecimal` bug, where an attacker-controlled width parameter drove an unbounded stack allocation.

### Finding Description
`TrieNode::decode` peeks the RLP header and, for list-encoded values, dispatches purely on element count: [1](#0-0) 

- If the list has `BRANCH_LIST_LENGTH` (17) elements, it decodes a `Vec::<Self>::decode(buf)`, which itself calls `TrieNode::decode` recursively for every one of the (up to 16) children.
- If the list has `LEAF_OR_EXTENSION_LIST_LENGTH` (2) elements, it calls `try_decode_leaf_or_extension_payload`, which for an extension-type prefix recursively calls `Self::decode(buf)` again on the inner node: [2](#0-1) 

Neither the branch nor the extension path caps how many times this recursion may be triggered from a single input buffer, nor is there any depth accounting anywhere in this module (`grep` for `depth`/`max_depth` in `node.rs` returns no results). The only implicit limiter — hashing (“blinding”) of nodes over 32 bytes — is enforced when *encoding* a `TrieNode`: [3](#0-2) 
but `decode` never re-validates that an embedded (non-32-byte, non-list-of-32-bytes) child actually satisfies that ≤32-byte embedding rule before recursing into it. So a hand-crafted byte string is free to contain a long chain of small nested extension/branch RLP nodes, which is syntactically valid input to `TrieNode::decode` and will recurse once per nesting level.

These bytes are attacker-controlled in the fault-proof stack: `TrieNode::decode` is invoked directly on data pulled from the (adversarial) preimage oracle in both the L1 and L2 chain providers used by the fault-proof program: [4](#0-3) [5](#0-4) 
and via the executor's `TrieDB`, which walks the trie during stateless block execution by unblinding and opening nodes on demand: [6](#0-5) [7](#0-6) 

Because a preimage's `PreimageKey` is derived from `keccak256(content)`, whoever supplies the preimage (the losing/dishonest side of a dispute game, or a malicious host process) can pick literally any byte string as the "preimage" for a hash they claim, as long as the game/oracle protocol accepts it as matching that key — the content is not otherwise constrained to be a well-formed, size-bounded Merkle node.

### Impact Explanation
In the on-chain fault dispute game, the fault-proof program (this crate) is the arbiter of truth: it must execute deterministically and produce a correct output root regardless of what data an adversarial party feeds it as trie-node preimages. If a malicious dispute-game participant can force the honest program's process (or the op-challenger acting on its behalf) to crash via stack overflow while deriving state/output roots, this is a denial-of-service against the proof system: the honest party can be prevented from computing/verifying a claim, potentially causing a wrong output root to go unchallenged or a dispute-game participant to fail to respond in time. This maps to the report's category "wrong provable output root, node halt" under a single dispute-move/preimage-serving path, which the task scope explicitly allows.

### Likelihood Explanation
Reaching this requires acting as the (adversarial) side of a dispute game or preimage host that supplies raw trie-node bytes for a hash it controls — which is precisely the threat model the fault-proof system is designed to be resilient against. No additional privilege beyond being a dispute-game participant is needed; the crafted bytes are ordinary, syntactically valid RLP list nodes, requiring no cryptographic break, only construction of nested small lists. The main uncertainty is the exact stack-frame cost of one recursion level and how many nesting levels are needed relative to the runtime stack size of the fault-proof program/host process — this was not verified with a compiled measurement, so likelihood should be treated as plausible but unconfirmed without further testing.

### Recommendation
Add an explicit, enforced maximum recursion/nesting depth (and/or total node count) to `TrieNode::decode` (and to `try_decode_leaf_or_extension_payload`), rejecting RLP structures that exceed the maximum practical trie depth (e.g., 64 hex nibbles for a 32-byte key, which bounds legitimate extension/leaf chains at the protocol level). Alternatively, convert the decode into an explicit iterative work-stack algorithm instead of native recursion so an attacker cannot control the process call stack directly, and additionally validate that embedded (non-blinded) child payloads are ≤32 bytes as the encoding side already assumes.

### Proof of Concept
Conceptual construction (not compiled/executed, since only static analysis was available):
1. Build an RLP list of 2 elements (`[path, child]`) where `path`'s high nibble marks it as an extension node and `child` is itself another 2-element RLP list of the same shape.
2. Nest this construction N times (each level only a few bytes, all embedded rather than 32-byte hash references), producing a single byte blob whose total size is small (e.g., tens of KB) but whose nesting depth N is in the thousands.
3. Register this blob as the preimage for `keccak256(blob)` in the dispute-game/preimage-oracle path that the fault-proof program consults (`OracleL1ChainProvider::trie_node_by_hash` / `OracleL2ChainProvider::trie_node_by_hash`), then have the honest verifier's `TrieNode::open`/`TrieNode::decode` resolve that hash during state derivation.
4. `TrieNode::decode` → `try_decode_leaf_or_extension_payload` → `Self::decode` recurses N times in a single call, exhausting the stack of the process performing verification.

### Citations

**File:** crates/proof/mpt/src/node.rs (L57-65)
```rust
/// The [`alloy_rlp::Encodable`] and [`alloy_rlp::Decodable`] traits are implemented for
/// [`TrieNode`], allowing for RLP encoding and decoding of the types for storage and retrieval. The
/// implementation of these traits will implicitly blind nodes that are longer than 32 bytes in
/// length when encoding. When decoding, the implementation will leave blinded nodes in place.
///
/// ## SAFETY
/// As this implementation only supports uniform key sizes, the [`TrieNode`] data structure will
/// fail to behave correctly if confronted with keys of varying lengths. Namely, this is because it
/// does not support the `value` field in branch nodes, just like the Ethereum Merkle Patricia Trie.
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

**File:** crates/proof/executor/src/db/mod.rs (L123-153)
```rust
    /// Fetches the [`TrieAccount`] of an account from the trie DB.
    ///
    /// ## Takes
    /// - `address`: The address of the account.
    ///
    /// ## Returns
    /// - `Ok(Some(TrieAccount))`: The [`TrieAccount`] of the account.
    /// - `Ok(None)`: If the account does not exist in the trie.
    /// - `Err(_)`: If the account could not be fetched.
    pub fn get_trie_account(
        &mut self,
        address: &Address,
        block_number: u64,
    ) -> TrieDBResult<Option<TrieAccount>> {
        // Send a hint to the host to fetch the account proof.
        self.hinter
            .hint_account_proof(*address, block_number)
            .map_err(|e| TrieDBError::Provider(e.to_string()))?;

        // Fetch the account from the trie.
        let hashed_address_nibbles = Nibbles::unpack(keccak256(address.as_slice()));
        let Some(trie_account_rlp) = self.root_node.open(&hashed_address_nibbles, &self.fetcher)?
        else {
            return Ok(None);
        };

        // Decode the trie account from the RLP bytes.
        TrieAccount::decode(&mut trie_account_rlp.as_ref())
            .map_err(TrieNodeError::RLPError)
            .map_err(Into::into)
            .map(Some)
```
