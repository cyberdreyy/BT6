### Title
Unbounded recursive decode of `TrieNode` allows attacker-controlled MPT preimages to overflow the stack in the fault-proof program - ([File: crates/proof/mpt/src/node.rs])

### Summary
`TrieNode::decode` (RLP decoder for Merkle Patricia Trie nodes) recurses once per `Extension` node and once per `Branch` child without any nesting-depth limit, mirroring the unbounded-recursion bug class in CVE-2022-45283 (`smil_parse_time_list` stack overflow from unbounded nested list parsing).

### Finding Description
`TrieNode::decode` dispatches on the RLP list length: a 2-element list is passed to `try_decode_leaf_or_extension_payload`, which for the `PREFIX_EXTENSION_*` case recursively calls `Self::decode(buf)` to decode the child node with no depth counter or bound: [1](#0-0) 

A 17-element list is decoded via `Vec::<Self>::decode(buf)`, which itself calls `TrieNode::decode` per branch slot, allowing recursion through `Branch` children as well: [2](#0-1) 

Because each `Extension`/`Branch` recursion level only needs a few RLP bytes (an encoded-path list plus a nested list header), an attacker who controls the preimage bytes fed into `TrieNode::decode` can construct a single, deeply nested (unblinded) trie node that drives thousands of recursive stack frames from a comparatively small input, exactly analogous to GPAC's `smil_parse_time_list` recursing on each `;`-separated list element without a depth cap.

The recursive `OrderedListWalker::fetch_leaves` helper exhibits the same unbounded-recursion pattern when walking `Branch`/`Extension` chains to collect leaves: [3](#0-2) 

`TrieNode` decoding is the shared primitive used by the state/account/storage trie database used inside the fault-proof program (`crates/proof/executor/src/db/mod.rs`) to open node preimages supplied by the untrusted preimage oracle during dispute-game execution — i.e., data an attacker fully controls when constructing a claim/response in the dispute game.

### Impact Explanation
If a dispute-game participant can supply a preimage that, when opened as a `TrieNode`, triggers deep enough `Extension`/`Branch` recursion to exhaust the fault-proof program's stack, the on-chain proof program would crash instead of producing a deterministic output root. This can halt fault-proof execution or force a different code path than honest nodes take, potentially producing a wrong provable output root or preventing valid dispute resolution — both are High-severity outcomes in this context (wrong output root / halted node equivalent for the proof program).

### Likelihood Explanation
Reaching this requires the attacker/dispute participant to control or influence which preimages get opened as `TrieNode`s during proof execution (e.g., a malicious response to a preimage-oracle read for account/storage data derived from L1/L2 state). Exploitability depends on whether the surrounding proof-program sandbox enforces a stack limit that turns overflow into a safe, bounded failure (as attempted generically for CLI processes in `crates/utilities/cli/src/sigsegv.rs`) rather than exploitable memory corruption, and on whatever preimage-authenticity checks (hash commitments) exist before a node is treated as opened — those checks constrain content but not structural nesting depth, so the recursion bound gap itself is real but the practical blast radius (crash vs. corruption) could not be fully confirmed from the available code.

### Recommendation
Add an explicit recursion/nesting-depth counter to `TrieNode::decode` (and the corresponding `OrderedListWalker::fetch_leaves` traversal), rejecting inputs that exceed a bound consistent with the maximum possible real Ethereum trie depth (e.g., 64), instead of relying on stack-guard/signal handling as the only backstop.

### Proof of Concept
Construct an RLP-encoded chain of `Extension` nodes, each wrapping the next as its 2-element list payload (`[encoded_path, child_node]`), nested to a depth of tens of thousands of levels, then feed the outermost bytes as a preimage returned in response to a `TrieProvider`/preimage-oracle lookup during `TrieNode::decode` (or `OrderedListWalker::fetch_leaves`); each level consumes new stack frames until the process overflows its stack.

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

**File:** crates/proof/mpt/src/node.rs (L573-588)
```rust
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
