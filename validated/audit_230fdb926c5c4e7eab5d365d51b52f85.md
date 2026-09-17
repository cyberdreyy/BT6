### Title
Off-by-one `HISTORY_SERVE_WINDOW` constant makes the fault-proof program derive the wrong EIP-2935 storage slot, corrupting the output root - (File: `crates/proof/proof/src/eip2935.rs`)

### Summary
The fault-proof program's EIP-2935 block-hash lookup uses a locally redefined window constant, `HISTORY_SERVE_WINDOW = 2^13 - 1 = 8191`, both as the "is this block still in the ring buffer" boundary check and as the modulus used to derive the on-chain storage slot. The real, on-chain EIP-2935 history contract executed by the block executor uses the canonical window of `8192`. This mismatch is structurally the same bug class as the PoolTogether `getVaultPortion()` report: a range/window size constant used to recompute an accumulator's boundary does not match the actual retention capacity of the underlying circular buffer, so the derived index can point at the wrong (stale, evicted, or never-written) slot.

### Finding Description
`eip_2935_history_lookup` computes the storage slot to prove/read as: [1](#0-0) 

`HISTORY_SERVE_WINDOW` is set to `2^13 - 1 = 8191` and is used both to decide whether `block_number` is still inside the on-chain ring ("is `header.number - block_number <= HISTORY_SERVE_WINDOW`") and, critically, as the modulus (`% HISTORY_SERVE_WINDOW`) that turns a block number into the ring-buffer storage slot.

The actual on-chain EIP-2935 contract, however, is not a Base-specific stub here — it is invoked as a real system call against `HISTORY_STORAGE_ADDRESS` using the canonical EIP-2935 implementation pulled in from the `evm2` crate: [2](#0-1) 

The canonical EIP-2935 specification writes `blockhash(header.number - 1)` into slot `(header.number - 1) % HISTORY_SERVE_WINDOW` with `HISTORY_SERVE_WINDOW = 8192` (a ring of exactly 8192 slots, serving lookback distances `1..=8192`). The proof-side reimplementation in `eip2935.rs` instead uses `8191` for both the boundary comparison and the modulus divisor. This is precisely analogous to the reported PoolTogether bug: `PrizePool::computeRangeStartDrawIdInclusive()` derived a `startDrawIdInclusive` using a `rangeSize` that could exceed the circular buffer's real retention capacity, causing it to read (or, symmetrically here, fail to read) the slot the on-chain writer actually used. Here the effect is a modulus mismatch: for the vast majority of `block_number` values, `block_number % 8191` differs from the slot the real on-chain contract wrote to (`block_number % 8192`), so `slot_key` in the storage trie lookup: [3](#0-2) 
resolves to a different, generally empty or stale, storage slot rather than the one holding the historical block hash written on-chain.

Note that unit tests in this file pin the *same wrong* modulus/boundary logic (they recompute `ring_index` using `HISTORY_SERVE_WINDOW`), so they self-validate the (incorrect) window rather than catching the mismatch against the on-chain contract's real window: [4](#0-3) 

### Impact Explanation
`eip_2935_history_lookup` is used by the fault-proof program (`crates/proof/proof`) to resolve historical block hashes needed to validate execution during dispute-game / fault-proof verification. If the derived storage slot does not match the slot the real on-chain EIP-2935 contract actually wrote (because of the modulus/window mismatch), the program will either:
- open a `KeyNotFound` trie path and fail with `TrieNodeError::KeyNotFound`, halting proof generation/verification, or
- silently resolve to a *different* (possibly stale/zero) block hash than the correct historical one, causing the fault-proof program to compute a wrong state transition and thus a **wrong provable output root**.

Either outcome maps to one of the accepted concrete impacts (node/program halt or wrong provable output root) for a component explicitly in scope ("the fault-proof program and MPT").

### Likelihood Explanation
This is deterministic, not probabilistic: any block number that requires an EIP-2935 blockhash lookup (any `BLOCKHASH`/EIP-2935 access whose target is within the real 8192-block window but outside the incorrect 8191-block window, or any lookup where `block_number % 8191 != block_number % 8192`) triggers the mismatch. Since Prague/Isthmus activation, every block with `header.number >= 8192` and any historical `BLOCKHASH` opcode usage inside the disputed range is affected, making this reachable on essentially every dispute involving blockhash-dependent execution.

### Recommendation
Replace the locally redefined `HISTORY_SERVE_WINDOW = 2u64.pow(13) - 1` in `crates/proof/proof/src/eip2935.rs` with the canonical EIP-2935 window of `8192` (ideally imported from the same shared constant the `evm2`/`alloy_eips` EIP-2935 implementation uses, rather than being redefined locally), so the modulus and the "in-window" boundary check both match the on-chain contract's actual ring-buffer capacity and write cadence — mirroring the recommended fix of capping/aligning the range computation to the true accumulator size in the referenced report.

### Proof of Concept
1. Deploy/execute a chain past Isthmus activation so the EIP-2935 system call at `HISTORY_STORAGE_ADDRESS` runs each block (writing `blockhash(number-1)` at slot `(number-1) % 8192`), as wired in `BaseBlockExecutor::apply_pre_execution` (`crates/common/evm2/src/executor.rs:184-189`).
2. Run the block past block 8192 so the ring has wrapped once.
3. Call `eip_2935_history_lookup(header, block_number, ...)` for a `block_number` whose real on-chain slot is `block_number % 8192` but whose `HISTORY_SERVE_WINDOW`-derived slot (`block_number % 8191`) differs.
4. Observe the storage-trie lookup opens the wrong slot: it either returns `TrieNodeError::KeyNotFound` (if the slot key was never written) or returns an unrelated block hash previously written by a different block number that happens to collide under `% 8191`, both of which corrupt the fault-proof program's block-hash accounting and its resulting output root.

### Citations

**File:** crates/proof/proof/src/eip2935.rs (L19-43)
```rust
/// The number of blocks that the EIP-2935 contract serves historical block hashes for. (8192 - 1)
const HISTORY_SERVE_WINDOW: u64 = 2u64.pow(13) - 1;

/// Performs a historical block hash lookup using the EIP-2935 contract. If the block number is out
/// of bounds of the history lookup window size, the oldest block hash within the window is
/// returned.
pub async fn eip_2935_history_lookup<P, H>(
    header: &Header,
    block_number: u64,
    provider: &P,
    hinter: &H,
) -> Result<B256, OracleProviderError>
where
    P: TrieProvider,
    H: TrieHinter,
{
    // Compute the storage slot for the block hash. If the distance between the current header and
    // the desired block number is within the window size, we compute the slot based on the
    // target block number, as the result is present within the ring. Otherwise, we return the
    // oldest block in the window.
    let slot = if header.number.saturating_sub(block_number) <= HISTORY_SERVE_WINDOW {
        block_number
    } else {
        header.number
    } % HISTORY_SERVE_WINDOW;
```

**File:** crates/proof/proof/src/eip2935.rs (L57-60)
```rust
    // Fetch the storage slot value from the account.
    let mut storage_trie = TrieNode::new_blinded(account.storage_root);
    let slot_key = Nibbles::unpack(keccak256(U256::from(slot).to_be_bytes::<32>()));
    let slot_value = storage_trie.open(&slot_key, provider)?.ok_or(TrieNodeError::KeyNotFound)?;
```

**File:** crates/proof/proof/src/eip2935.rs (L140-169)
```rust
    #[rstest]
    #[case::block_number_in_window(1000, 999)]
    #[case::block_number_outside_window(9000, 100)]
    #[case::block_number_at_window_boundary(8192 * 2, 8192)]
    #[case::block_number_at_window_boundary_plus_one(8192 * 2, 8191)]
    #[tokio::test]
    async fn test_eip_2935_history_lookup(
        #[case] head_block_number: u64,
        #[case] target_block_number: u64,
    ) {
        let expected_hash = B256::from([0xFF; 32]);
        let ring_index = if head_block_number - target_block_number <= HISTORY_SERVE_WINDOW {
            target_block_number
        } else {
            head_block_number
        } % HISTORY_SERVE_WINDOW;

        let provider = MockTrieProvider::new(ring_index, expected_hash);
        let header = Header {
            number: head_block_number,
            state_root: provider.state_root,
            ..Default::default()
        };

        let result =
            eip_2935_history_lookup(&header, target_block_number, &provider, &NoopTrieHinter)
                .await
                .unwrap();

        assert_eq!(result, expected_hash);
```

**File:** crates/common/evm2/src/executor.rs (L184-189)
```rust
        // EIP-2935 block-hashes call (Prague onwards), skipped at genesis.
        if (spec as u8) >= (SpecId::PRAGUE as u8) && !is_genesis {
            let data = Bytes::copy_from_slice(self.ctx.parent_hash.as_slice());
            let executed = self.evm.system_call(SystemTx::new(HISTORY_STORAGE_ADDRESS, data))?;
            let _ = executed.commit_to(&mut self.block_state);
        }
```
