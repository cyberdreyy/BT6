## Analysis

The Elasticsearch Grok CVE (CVE‑2021‑22144) is a stack‑exhaustion bug caused by an unbounded recursive parser. The closest reachable analog in this codebase is the RLP decoder for Merkle‑Patricia‑Trie nodes used by the fault‑proof program.

`TrieNode::decode` in `crates/proof/mpt/src/node.rs` recurses into itself with no depth bound whenever it encounters an `Extension` node whose child is "embedded" (RLP length < 32 bytes) rather than blinded by a hash: [1](#0-0) [2](#0-1) 

Encoding of an `Extension` node only blinds (hashes) the child when its own RLP length is `>= 32` bytes — a short/minimal child (e.g. an `Empty` node, or another tiny `Extension`) stays embedded raw inside the parent's bytes: [3](#0-2) 

`Decodable for TrieNode` is invoked directly on preimage-oracle bytes in both the L1 and L2 chain providers used by the fault‑proof client program, with no recursion-depth or nesting guard: [4](#0-3) [5](#0-4) 

Elsewhere in the codebase, similar untrusted-input parsers (RLP-decoded `calls` phases, JSON validity predicates, observability-event data nesting) are explicitly bounded (`MAX_CALL_PHASES_PER_TX`, `DEFAULT_MAX_VALIDITY_PREDICATES`, `MAX_DATA_VALIDATION_DEPTH`): [6](#0-5) [7](#0-6) 
— but `TrieNode::decode`'s nested-node recursion has no equivalent bound.

### Title
Uncontrolled Recursion in `TrieNode::decode` via Nested Embedded MPT Nodes — (File: `crates/proof/mpt/src/node.rs`)

### Summary
`TrieNode::decode` recursively decodes embedded (non-blinded) `Extension`/`Branch` child nodes with no bound on nesting depth. A dispute-game participant who controls the byte content behind a claimed/committed trie-root hash (the exact object being contested in a fault-proof dispute) can construct a deeply nested chain of minimal embedded nodes that, when decoded by `OracleL1ChainProvider::trie_node_by_hash` / `OracleL2ChainProvider::trie_node_by_hash`, drives unbounded native-stack recursion in the fault‑proof program.

### Finding Description
`TrieNode::decode` (`crates/proof/mpt/src/node.rs:567-604`) dispatches on the RLP header: for a 2-element list it calls `try_decode_leaf_or_extension_payload`, which for the `Extension` case recursively calls `Self::decode(buf)` on the pointer node (`node.rs:454-461`). Per the encoder (`node.rs:527-537`), a child is embedded raw (not blinded to a 32-byte hash) whenever its own RLP length is `< 32` bytes — which is true for trivially small nodes such as `Empty` or another minimal `Extension`. Nothing in the decoder tracks or limits recursion depth.

The decoder is fed directly with bytes obtained from the preimage oracle — data whose only invariant enforced by the honest program is `keccak256(bytes) == requested_hash` (the requested hash is resolved recursively from a single root hash that is itself attacker-controlled input in a dispute: the claimed L2 output root / L1 root supplied as part of `BootInfo`, or the root reached transitively while walking that trie). Because the honest program's only check on preimage content is the hash-preimage relation, an adversarial dispute-game participant can freely pick any byte string as the "trie node" content as long as it hashes to the value they themselves committed as their disputed claim, and can pack that content with hundreds/thousands of nested minimal `Extension`/`Empty` structures. Decoding this single blob then recurses once per nesting level with no cap, exhausting the call stack of the fault‑proof client program (native execution or inside the Cannon/other VM used for on-chain dispute resolution).

This mirrors the Grok-parser class of bug (CWE‑674, uncontrolled recursion) — the parser accepts attacker-authored recursive structure and recurses proportional to nesting depth with no limit, unlike sibling parsers in this codebase (EIP‑8130 `calls` phases, JSON validity predicates, observability event `data`) that all impose explicit depth/count caps.

### Impact Explanation
A stack overflow while decoding a trie node during the fault‑proof program's execution is a hard crash (panic/abort) of the program that is supposed to deterministically re-execute and verify a disputed L2 state transition. Because the fault-proof program is the arbiter of dispute-game correctness, causing it to crash/halt instead of producing the correct output prevents the honest party from computing and submitting the correct output root within the dispute window, undermining the fault-proof system's ability to resolve challenges correctly (node halt / inability to serve a correct provable output for that dispute). This qualifies as a Medium-severity Denial of Service against a component explicitly in-scope ("the fault-proof program and MPT").

### Likelihood Explanation
Exploitation requires only crafting an RLP-recursive byte blob whose keccak256 matches a hash value that the attacker controls: the claimed/committed root submitted as part of a dispute (`BootInfo`/claimed output root) or, transitively, any node reached while the honest party walks that attacker-chosen trie during response. Since the attacker fully controls the content behind their own claimed root in a dispute game, this precondition is trivially satisfiable — no cryptographic hash preimage break is needed for the top node, and each subsequent nested "child" is simply embedded plaintext under the attacker's control, not a separately-hashed value. No special privileges beyond being a dispute-game participant are required.

### Recommendation
Add an explicit recursion/nesting depth bound to `TrieNode::decode` (e.g., an explicit iterative decode using an auxiliary stack, or a depth counter threaded through decode calls capped to the maximum theoretically valid MPT depth, ~64 nibbles for account/storage tries, with a hard reject beyond that), consistent with the bounding pattern already used for `MAX_CALL_PHASES_PER_TX`, `DEFAULT_MAX_VALIDITY_PREDICATES`, and `MAX_DATA_VALIDATION_DEPTH` elsewhere in the codebase.

### Proof of Concept
1. Construct `inner = TrieNode::Empty` RLP-encoded (1 byte: `0x80`).
2. Repeatedly wrap: `node_i = TrieNode::Extension { prefix: <1 nibble>, node: Box::new(node_{i-1}) }`, encoding it via `Encodable for TrieNode` — as long as `node_{i-1}.length() < 32`, the encoder embeds it raw rather than blinding it (`node.rs:531-536`), so each wrap adds only a few bytes and stays under the embedding threshold for many iterations by keeping the path nibble count minimal per level, or by using `Branch` with 16 `Empty` slots and one embedded child to similarly avoid blinding.
3. Repeat nesting thousands of times to produce one RLP blob whose top-level keccak256 hash is `H`.
4. As the dispute-game participant, commit `H` as the (disputed) trie root for the relevant claim; when the fault-proof program calls `trie_node_by_hash(H)` (`crates/proof/proof/src/l1/chain_provider.rs:128-142` or `l2/chain_provider.rs:176-190`) and receives this blob from the preimage oracle (which validates only `keccak256(bytes) == H`), `TrieNode::decode` recurses once per nesting level and overflows the stack, crashing the fault-proof program before it can produce a verdict.

### Citations

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

**File:** crates/proof/mpt/src/node.rs (L527-537)
```rust
            Self::Extension { prefix, node } => {
                // Encode the extension node's header, prefix, and pointer node.
                Header { list: true, payload_length }.encode(out);
                alloy_trie::nodes::encode_path_leaf(prefix, false).as_slice().encode(out);
                if node.length() >= B256::ZERO.len() {
                    let hash = node.blind();
                    hash.encode(out);
                } else {
                    node.encode(out);
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

**File:** crates/common/consensus/src/transaction/eip8130/tx.rs (L140-158)
```rust
    fn decode_calls(buf: &mut &[u8]) -> alloy_rlp::Result<Vec<Vec<Call>>> {
        let header = Header::decode(buf)?;
        if !header.list {
            return Err(alloy_rlp::Error::UnexpectedString);
        }
        if buf.len() < header.payload_length {
            return Err(alloy_rlp::Error::InputTooShort);
        }

        let (mut payload, rest) = buf.split_at(header.payload_length);
        let mut phases = Vec::new();
        while !payload.is_empty() {
            if phases.len() >= Eip8130Constants::MAX_CALL_PHASES_PER_TX {
                return Err(alloy_rlp::Error::Custom("too many EIP-8130 call phases"));
            }
            phases.push(Vec::<Call>::decode(&mut payload)?);
        }
        *buf = rest;
        Ok(phases)
```

**File:** crates/common/observability-events/src/event.rs (L411-424)
```rust
fn find_forbidden_data_key(data: &Map<String, Value>, depth: usize) -> Option<ForbiddenDataReason> {
    if depth > MAX_DATA_VALIDATION_DEPTH {
        return Some(ForbiddenDataReason::TooDeep);
    }
    for (key, value) in data {
        if is_forbidden_data_key(key) {
            return Some(ForbiddenDataReason::Key(key.clone()));
        }
        if let Some(reason) = find_forbidden_data_value(value, depth + 1) {
            return Some(reason);
        }
    }
    None
}
```
