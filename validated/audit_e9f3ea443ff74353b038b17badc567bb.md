### Title
Unbounded recursive `TrieNode` RLP decoding enables stack-overflow DoS in the fault-proof program - (File: `crates/proof/mpt/src/node.rs`)

### Summary
`TrieNode::decode` (the Merkle Patricia Trie node decoder used throughout `base-proof-mpt`, `base-proof-executor`, and the L1/L2 oracle chain providers of the fault-proof program) recurses without any depth bound when decoding `Branch` and `Extension` nodes. A malicious preimage/oracle host in a dispute game (or any other component feeding attacker-controlled bytes into `TrieNode::decode`) can supply a single RLP blob encoding a deeply nested chain of unblinded `Extension`/`Branch` nodes, causing the decoder to recurse thousands of times and overflow the stack — the same bug class as CVE-2026-22260 (Suricata's unbounded recursive decompression causing a stack-overflow crash).

### Finding Description
`TrieNode::decode` in [1](#0-0)  dispatches on the RLP list length: a `BRANCH_LIST_LENGTH` list decodes via `Vec::<Self>::decode(buf)`, which itself calls `TrieNode::decode` for each of up to 16 children; a `LEAF_OR_EXTENSION_LIST_LENGTH` list is passed to `try_decode_leaf_or_extension_payload`.

In `try_decode_leaf_or_extension_payload` ( [2](#0-1) ), when the path prefix indicates an `Extension` node, the pointer value is decoded via `Self::decode(buf)` — i.e., `TrieNode::decode` recurses into itself with no depth counter or recursion limit anywhere in the type.

Per the module's own documentation, `TrieNode` is described as "a recursive, in-memory implementation" of the MPT ( [3](#0-2) ). Because an `Extension`/`Branch` node's child is only blinded (replaced by a 32-byte hash) when its own RLP length is ≥ 32 bytes, an attacker can chain arbitrarily many short `Extension` nodes together inline in a single unblinded RLP blob (each nested level only costs a handful of bytes), producing thousands of recursive stack frames from one small, self-contained input.

This decoder is exercised directly on attacker/host-controlled bytes in the fault-proof program's oracle-backed chain providers:
- `OracleL1ChainProvider::trie_node_by_hash` decodes preimages fetched from the untrusted preimage oracle for L1 trie nodes: [4](#0-3) 
- `OracleL2ChainProvider::trie_node_by_hash` does the same for L2 trie nodes: [5](#0-4) 

Both are invoked via the generic `TrieProvider::trie_node_by_hash` interface ( [6](#0-5) ), which is called recursively from `TrieNode::unblind`/`TrieNode::open` during stateless execution and trie traversal ( [7](#0-6) ), and from the stateless `TrieDB` used by `StatelessL2Builder` in the fault-proof executor ( [8](#0-7) , [9](#0-8) ).

During dispute-game execution, the preimage oracle is served by the untrusted "host" side of the fault-proof program (the challenger/counterparty in an actual dispute), meaning a dispute-game participant can supply crafted preimage bytes that this recursive decoder will process.

### Impact Explanation
A stack overflow in the fault-proof program's client process causes an immediate, uncontrolled process crash (undefined behavior / abort, not a graceful error return, since Rust stack overflows abort the process rather than unwinding). This halts the client that is expected to compute or verify the fault-proof program's output for a dispute game, preventing that party from producing a valid response during the game — a denial-of-service against the correctness/liveness of the dispute-resolution path (fault-proof program halt), matching the "node halt" / "wrong provable output" impact classes in scope.

### Likelihood Explanation
Reachability requires only the ability to serve/influence preimage-oracle data consumed by `trie_node_by_hash` during fault-proof program execution — i.e., acting as (or compromising) the host side of a dispute game, or otherwise supplying a state/account/storage trie node preimage that gets decoded by the client program. No cryptographic material or privileged access is needed beyond normal dispute-game participation; only a specially crafted, deeply nested but otherwise small RLP blob is required.

### Recommendation
Add an explicit recursion/depth limit to `TrieNode::decode` (and to `try_decode_leaf_or_extension_payload`'s recursive `Self::decode` call), rejecting RLP inputs that would produce trie nodes deeper than the maximum possible MPT depth (bounded by the 64-nibble/256-bit key space, e.g. depth ≤ 64). Alternatively, convert the decode routine to an explicit iterative/worklist algorithm instead of recursive descent so that adversarial nesting cannot grow the native call stack.

### Proof of Concept
1. Construct RLP bytes representing a chain of N nested unblinded `Extension` nodes (each `rlp([encoded_path, child])` where `child` is itself another short `Extension` node), terminated by a `Leaf` node, keeping every intermediate node's encoded length under 32 bytes so it is never blinded (see `blinded_length`/`Encodable` logic at [10](#0-9) ).
2. Serve this blob as the preimage for a hash requested via `TrieProvider::trie_node_by_hash` in the fault-proof program (e.g., as the L1/L2 oracle response consumed by `OracleL1ChainProvider::trie_node_by_hash` / `OracleL2ChainProvider::trie_node_by_hash`).
3. When the client calls `TrieNode::decode` on this preimage, decoding recurses once per nested `Extension` level with no bound; for sufficiently large N (in the low thousands, well within any reasonable preimage size limit) this exhausts the stack and crashes the process.

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

**File:** crates/proof/mpt/src/node.rs (L439-461)
```rust
    fn try_decode_leaf_or_extension_payload(buf: &mut &[u8]) -> TrieNodeResult<Self> {
        // Decode the path and value of the leaf or extension node.
        let path = Bytes::decode(buf).map_err(TrieNodeError::RLPError)?;
        let Some(first_byte) = path.first() else {
            return Err(TrieNodeError::InvalidNodeType);
        };
        let first_nibble = first_byte >> NIBBLE_WIDTH;
        let first = match first_nibble {
            PREFIX_EXTENSION_ODD | PREFIX_LEAF_ODD => Some(first_byte & 0x0F),
            PREFIX_EXTENSION_EVEN | PREFIX_LEAF_EVEN => None,
            _ => return Err(TrieNodeError::InvalidNodeType),
        };

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

**File:** crates/proof/mpt/src/node.rs (L503-537)
```rust
    /// Returns the encoded length of the trie node, blinding it if it is longer than an encoded
    /// [B256] string in length.
    ///
    /// ## Returns
    /// - `usize` - The encoded length of the value, blinded if the raw encoded length is longer
    ///   than a [B256].
    fn blinded_length(&self) -> usize {
        let encoded_len = self.length();
        if encoded_len >= B256::ZERO.len() { B256::ZERO.length() } else { encoded_len }
    }
}

impl Encodable for TrieNode {
    fn encode(&self, out: &mut dyn alloy_rlp::BufMut) {
        let payload_length = self.payload_length();
        match self {
            Self::Empty => out.put_u8(EMPTY_STRING_CODE),
            Self::Blinded { commitment } => commitment.encode(out),
            Self::Leaf { prefix, value } => {
                // Encode the leaf node's header and key-value pair.
                Header { list: true, payload_length }.encode(out);
                alloy_trie::nodes::encode_path_leaf(prefix, true).as_slice().encode(out);
                value.encode(out);
            }
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

**File:** crates/proof/mpt/README.md (L1-10)
```markdown
# `base-proof-mpt`

A recursive, in-memory implementation of Ethereum's hexary Merkle Patricia Trie (MPT).

## Overview

Implements Ethereum's Merkle Patricia Trie with support for retrieval, insertion, deletion, and
root computation via RLP-encoded trie node encoding. Starting from a trie root hash, `TrieNode`
lazily fetches and caches node preimages via `TrieProvider`, enabling stateless block execution
without storing the full state. Designed as the trie backend for [`base-proof-executor`](../executor).
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

**File:** crates/proof/mpt/src/traits.rs (L11-25)
```rust
/// The [`TrieProvider`] trait defines the synchronous interface for fetching trie node preimages.
pub trait TrieProvider {
    /// The error type for fetching trie node preimages.
    type Error: Display;

    /// Fetches the preimage for the given trie node hash.
    ///
    /// ## Takes
    /// - `key`: The key of the trie node to fetch.
    ///
    /// ## Returns
    /// - Ok(TrieNode): The trie node preimage.
    /// - `Err(Self::Error)`: If the trie node preimage could not be fetched.
    fn trie_node_by_hash(&self, key: B256) -> Result<TrieNode, Self::Error>;
}
```

**File:** crates/proof/executor/src/db/mod.rs (L132-154)
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
    }
```

**File:** crates/proof/executor/src/builder/core.rs (L55-88)
```rust
impl<'a, P, H, Evm> StatelessL2Builder<'a, P, H, Evm>
where
    P: TrieDBProvider + Debug,
    H: TrieHinter + Debug,
    Evm: EvmFactory<Spec = BaseSpecId, BlockEnv = BlockEnv> + 'static,
    <Evm as EvmFactory>::Tx:
        FromTxWithEncoded<BaseTxEnvelope> + FromRecoveredTx<BaseTxEnvelope> + BaseTxEnv,
{
    /// Creates a new stateless L2 block builder instance.
    ///
    /// Initializes the builder with the necessary components for stateless block execution
    /// including the trie database, execution factory, and rollup configuration.
    ///
    /// # Arguments
    /// * `config` - Rollup configuration with chain parameters and activation heights
    /// * `evm_factory` - EVM factory for creating execution environments
    /// * `provider` - Trie database provider for state access
    /// * `hinter` - Trie hinter for optimizing state access patterns
    /// * `parent_header` - Sealed header of the parent block to build upon
    pub fn new(
        config: &'a RollupConfig,
        evm_factory: Evm,
        provider: P,
        hinter: H,
        parent_header: Sealed<Header>,
    ) -> Self {
        let trie_db = TrieDB::new(parent_header, provider, hinter);
        let factory = BaseBlockExecutorFactory::new(
            AlloyReceiptBuilder::default(),
            config.clone(),
            evm_factory,
        );
        Self { config, trie_db, factory }
    }
```
