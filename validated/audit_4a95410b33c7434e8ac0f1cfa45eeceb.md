### Title
Unbounded recursion in `TrieNode` RLP decode/open path allows stack-overflow DoS of the fault-proof program via a maliciously nested MPT proof - (File: `crates/proof/mpt/src/node.rs`)

### Summary
`TrieNode::decode` and `TrieNode::open`/`unblind` recursively descend into RLP-encoded extension and branch nodes with no depth limit, unlike other untrusted-input parsers in the same repository (e.g. the CBOR attestation parser, which enforces `MAX_CBOR_NESTING_DEPTH`). An attacker who controls L1 preimage/trie data consumed by the fault-proof program can supply a deeply nested chain of extension/blinded nodes to overflow the stack.

### Finding Description
`TrieNode::decode` (`Decodable` impl) recurses into `Self::decode` for `Extension` nodes and into `Vec::<Self>::decode` for `Branch` nodes with no depth ceiling: [1](#0-0) 

Likewise, `TrieNode::open` follows `Extension`/`Blinded` chains recursively, calling `node.unblind(fetcher)` then `node.open(...)` again, and `unblind` fetches attacker/L1-supplied preimage bytes and re-parses them via the same recursive `decode`: [2](#0-1) 

This is structurally the same bug class as the smol-toml advisory: recursive descent over attacker-controlled input with no explicit depth bound, in a context where the parsed input (TOML comments / here, RLP trie nodes) is externally supplied and expected to be handled defensively. By contrast, the CBOR attestation parser in this same codebase explicitly guards against this class of issue with a `MAX_CBOR_NESTING_DEPTH` check at every recursive call: [3](#0-2) 

The trie-node decoder has no analogous check anywhere in `crates/proof/mpt/src/node.rs`.

### Impact Explanation
The fault-proof program (part of the dispute-game / MPT stack explicitly in scope) walks Merkle-Patricia proofs built from L1/L2 state and receipts using `TrieNode::open`/`unblind`/`decode` to resolve account and storage values needed to compute the provable output root. An attacker able to influence the preimages fed to this pipeline (e.g., a dispute-game participant supplying a crafted witness/proof, or a node processing an adversarial trie proof during derivation/execution) can construct a long chain of nested `Extension`/`Blinded` nodes. Each level requires only ~33 bytes (a blinded 32-byte commitment triggers another `unblind` + recursive `decode`/`open` call), so a proof of a few hundred KB can encode thousands of nesting levels, exhausting the call stack and crashing the process (fault-proof program or node) that decodes it — a node/prover halt (Medium: availability impact only, no direct fund theft or state corruption), matching CWE-674 (uncontrolled recursion).

### Likelihood Explanation
Likelihood is Medium: reaching this code path requires the attacker to get a maliciously nested trie proof into the fault-proof/witness pipeline (e.g., as part of a dispute-game proof or preimage set), which is a real and expected external-input path but not as trivially reachable as, say, a raw RPC call. Since the codebase already went to the trouble of adding explicit depth guards to its CBOR parser (used for closely related TEE attestation verification), it demonstrates that the project treats this bug class as a real concern, and the MPT decoder was overlooked.

### Recommendation
Add an explicit recursion-depth parameter (mirroring `CborItem::read_at`'s `depth`/`MAX_CBOR_NESTING_DEPTH` pattern) to `TrieNode::decode`, `TrieNode::open`, and `TrieNode::unblind`, capping nesting at the maximum theoretically valid Ethereum trie depth (64 nibbles) and returning an error (`TrieNodeError`) once exceeded, so malformed/adversarial proofs are rejected instead of causing unbounded recursion.

### Proof of Concept
Construct an RLP-encoded chain of `TrieNode::Extension` nodes (or alternating `Blinded` commitments resolved via a malicious `TrieProvider::trie_node_by_hash` that always returns another `Extension`/`Blinded` node) nested to a depth of tens of thousands, then call:
```rust
let mut node = TrieNode::decode(&mut crafted_rlp_bytes.as_slice());
// or
root_node.open(&path, &malicious_fetcher);
```
This will recurse until the stack is exhausted, crashing the process — analogous to `require("smol-toml").parse('# comment\n'.repeat(8000) + 'key = "value"')` crashing the smol-toml parser via unbounded recursive comment-skipping.

**Note on confidence:** I was unable to fully trace, within the available tool budget, the exact call sites in `crates/proof/proof/src/l1/chain_provider.rs` / `l2/chain_provider.rs` that feed externally-supplied preimages into `TrieNode::open`/`unblind` during fault-proof execution, so the precise end-to-end reachability from an untrusted dispute-game participant could not be fully confirmed with a concrete call chain; this should be verified further (e.g., via a Devin session with full repo access) before treating this as fully proven.

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
