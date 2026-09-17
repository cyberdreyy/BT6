## Analysis

The reported CVE describes an unbounded-recursion stack overflow in `fig2dev`'s `read_objects`, triggered by attacker-controlled, deeply-nested input with no recursion-depth guard. The reachable analog in Base is the `TrieNode` RLP decoder/traversal implementation in the fault-proof MPT crate, which recurses without any depth limit — unlike the CBOR attestation parser in the same codebase, which explicitly bounds nesting (`MAX_CBOR_NESTING_DEPTH = 64`) to defend against exactly this bug class.

### Title
Unbounded recursion in `TrieNode` RLP decode/traversal enables stack-overflow DoS of the fault-proof program - (File: `crates/proof/mpt/src/node.rs`)

### Summary
`TrieNode::decode` (the `Decodable` impl) and the traversal helpers `open`, `insert`, `delete`, and `collapse_if_possible` recurse into child/extension nodes with no depth counter or bound, unlike the project's own CBOR parser which enforces `MAX_CBOR_NESTING_DEPTH`.

### Finding Description
`TrieNode::decode` handles `Extension` nodes by recursively calling `Self::decode(buf)` on the inner payload with no depth tracking: [1](#0-0) 

The overall `Decodable` dispatcher for `TrieNode` (`Branch`/`Leaf`/`Extension`/`Blinded`/`Empty`) similarly recurses via `Vec::<Self>::decode` for branches and `try_decode_leaf_or_extension_payload` for extensions, again with no depth cap: [2](#0-1) 

Critically, unlike the ordinary Ethereum MPT semantics (where blinding to a 32-byte commitment happens whenever an encoded node exceeds 32 bytes), `TrieNode::decode`'s non-list branch only enforces the 0/32-byte payload-length invariant for the *terminal* node — it does not reject a list-typed (`Extension`) node whose total encoded length exceeds 32 bytes: [3](#0-2) 

This means a caller supplying raw, untrusted bytes can construct an RLP blob that is a long chain of nested `Extension` list headers, each pointing to another list, without ever needing to satisfy the "inline nodes must be ≤32 bytes" constraint that only exists on the encode path. `TrieNode::decode` will recurse once per nesting level with no bound, in contrast to `CborItem::read_at`'s explicit `depth > MAX_CBOR_NESTING_DEPTH` guard used for the same class of untrusted, recursively-structured input: [4](#0-3) 

The same unbounded recursion pattern exists in the trie-mutation/traversal paths that recurse on `Blinded` nodes as they're revealed (`open`, `insert`, `delete`, `collapse_if_possible`): [5](#0-4) [6](#0-5) 

The primary reachable path is the fault-proof program's preimage oracle: `OracleL2ChainProvider::trie_node_by_hash` feeds raw preimage-oracle bytes directly into `TrieNode::decode`: [7](#0-6) 

and `base-proof-executor`'s `TrieDB` calls `TrieNode::open`/`unblind` (which itself calls `fetcher.trie_node_by_hash` → `TrieNode::decode`) while walking account/storage tries during dispute-game state execution: [8](#0-7) 

### Impact Explanation
A stack overflow inside the fault-proof program (which runs inside the dispute-game's fault-proof VM/host) causes an unrecoverable crash of that program during a dispute-game step, i.e., a node/program halt on that specific execution path. Since the fault-proof program is the mechanism producing the provable output root for a dispute game, a crash there prevents deriving a correct output root for the disputed claim — a "node halt / wrong provable output root" class of impact per the validation criteria, provided a party can get such a malformed node accepted as a valid preimage in an active dispute.

### Likelihood Explanation
Exploitability hinges on whether an attacker can supply a preimage blob matching a keccak256 commitment referenced during the trace that also encodes deep `Extension` nesting. Preimage-oracle entries are keyed by their own hash, so an attacker must control (or self-produce) both the referenced commitment and the bytes behind it — e.g., a dispute-game participant constructing their own claimed output-root/state that legitimately, or via a malformed local blob accepted by their own preimage server, satisfies this. This is a narrower bar than `fig2dev`'s local-file case, and I was not able to fully verify from the index whether earlier layers (block/header validation, RLP length sanity checks before invoking `TrieNode::decode`) already reject oversized nested blobs before they reach this recursive decoder — this bounds confidence in exploitability and should be checked against the full preimage-oracle validation pipeline.

### Recommendation
Add an explicit recursion-depth counter/limit to `TrieNode::decode` (and to the `open`/`insert`/`delete`/`collapse_if_possible` traversal recursions), mirroring the `MAX_CBOR_NESTING_DEPTH` pattern already used in `crates/proof/tee/registrar/src/cbor.rs`. Additionally, consider rejecting `Extension`/`Branch` decodes whose total encoded length exceeds the 32-byte inline-node threshold, restoring the blind-vs-inline invariant on the decode path to match the encode path.

### Proof of Concept
Construct a preimage byte string consisting of thousands of nested RLP list headers of `LEAF_OR_EXTENSION_LIST_LENGTH` (2-element lists) each wrapping another such list as its second element, terminated by a minimal leaf payload. Feed this via a `TrieProvider`/`PreimageOracle` implementation as the preimage for a `Blinded` commitment reached during trie traversal (e.g., via `OracleL2ChainProvider::trie_node_by_hash` or `TrieNode::open`'s `unblind` call). `TrieNode::decode` will recurse once per nesting level with no depth check, exhausting the call stack.

### Citations

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

**File:** crates/proof/executor/src/db/mod.rs (L132-153)
```rust
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
