## Analysis

The strongest analog to CVE‑2018‑20502 (excessive, unbounded memory allocation while parsing an untrusted length/format before validation) is in the Base derivation-channel Brotli decompressor.

`Brotli::decompress` in [1](#0-0)  unconditionally allocates three large backing buffers — a 32 MiB `u8_buffer`, a 4 MiB `u32_buffer`, and a ~16 MiB `hc_buffer` (`4 * 1024 * 1024` `HuffmanCode` entries) — **before** examining the size, validity, or plausibility of the input data. Only the `output` buffer respects the caller-supplied `max_rlp_bytes_per_channel` cap [2](#0-1) ; the ~52 MiB of allocator arenas are sized as fixed constants regardless of how small or malformed the input channel is.

This function is reached from `BatchReader::decompress_brotli`, which is called for every channel whose first byte after the L1 batch-inbox transaction data is tagged `CHANNEL_VERSION_BROTLI` [3](#0-2) , itself driven from `ChannelReader::next_batch` in the derivation pipeline [4](#0-3) . Derivation processes every transaction addressed to the batch-inbox address on L1 — data that is written by an ordinary, unprivileged L1 transaction sender, not a trusted role — and this same code path runs identically in full/verifier nodes, the sequencer, and the fault-proof program used for dispute resolution.

By contrast, the codebase elsewhere goes out of its way to bound pre-authentication allocations against exactly this bug class (CWE‑770), e.g. `MAX_DECOMPRESSED_ENVELOPE_BYTES` in the gossip payload envelope [5](#0-4) , the bounded SSZ transaction list [6](#0-5) , the flashblock decompression cap [7](#0-6) , and the zlib channel decompressor which bounds the *entire* output via `decompress_to_vec_zlib_with_limit` [8](#0-7) . The Brotli allocator-arena sizing was missed in this hardening pass.

### Title
Unbounded ~52 MiB eager heap allocation in Brotli channel decompression regardless of input size or validity - (File: crates/consensus/protocol/src/brotli.rs)

### Summary
`Brotli::decompress` allocates fixed 32 MiB + 4 MiB + ~16 MiB internal decoder-state buffers on every invocation before it inspects or validates the compressed input, mirroring the CVE-2018-20502 pattern of attempting excessive memory allocation from an atom/container before validating its content.

### Finding Description
`Brotli::decompress` is the sole decompressor used for Fjord-and-later derivation channels tagged with `BatchReader::CHANNEL_VERSION_BROTLI`. Its implementation immediately does:
```
let mut u8_buffer = vec![0; 32 * 1024 * 1024].into_boxed_slice();
let mut u32_buffer = vec![0; 1024 * 1024].into_boxed_slice();
let mut hc_buffer = vec![HuffmanCode::default(); 4 * 1024 * 1024].into_boxed_slice();
``` [9](#0-8) 
These allocations total roughly 52 MiB and are sized as constants — they do not scale with, or get bounded by, the size of `data` or the `max_rlp_bytes_per_channel` argument that is otherwise used to cap the `output` buffer [2](#0-1) . Nothing about the input is checked before this allocation runs, so even a single-byte or garbage-filled brotli-tagged channel forces the full ~52 MiB allocation.

This function is invoked once per channel whenever the leading byte of the channel data is the Brotli version tag: `BatchReader::decompress_brotli` strips that tag and calls straight into `Brotli.decompress` [3](#0-2) . Channels are constructed purely from bytes any L1 sender submits as calldata/blob data to the configured batch-inbox address; the reader has no signature or origination check before decompression. The reader is driven from `ChannelReader::next_batch`, which runs as part of ordinary block derivation on every full/verifier node, the sequencer, and — critically — the fault-proof program that recomputes output roots for dispute-game resolution.

### Impact Explanation
Any unprivileged L1 account can post a minimal, cheap transaction (or blob) to the batch-inbox address whose data begins with the Brotli channel-version byte. Each such channel forces every node running derivation — including the fault-proof program executed inside a constrained VM (Cannon/Asterisc/zkVM) during dispute-game resolution — to eagerly allocate ~52 MiB of heap, an allocation-amplification factor of orders of magnitude relative to the attacker's on-chain data cost. Repeated cheap submissions can be used to induce memory pressure/OOM behavior across the network's derivation-consuming components, and inside the fault-proof program specifically this oversized, input-independent allocation risks exhausting the constrained memory model used for provable execution, potentially causing dispute-game execution to fail or diverge — a node-halt / wrong-provable-output-root class of impact.

### Likelihood Explanation
Reaching this code requires only sending an ordinary L1 transaction with calldata (or a blob) addressed to the batch-inbox address, with the first byte set to `BatchReader::CHANNEL_VERSION_BROTLI` (`1`) once Fjord is active (`brotli_supported`). No special privileges, contract deployment, or protocol role is required, and the cost is a single cheap L1 transaction/blob per triggered allocation.

### Recommendation
Size the Brotli decoder's internal `u8_buffer`, `u32_buffer`, and `hc_buffer` arenas relative to `max_rlp_bytes_per_channel` (and/or the actual compressed input length) instead of using fixed 32 MiB/4 MiB/16 MiB constants, mirroring the bound already applied to the `output` buffer, so a small or invalid input cannot force a large, input-independent allocation.

### Proof of Concept
1. Configure a rollup with Fjord active (`brotli_supported = true`).
2. From any L1 account, submit a transaction whose `data` to the batch-inbox address is `[BatchReader::CHANNEL_VERSION_BROTLI, <arbitrary/garbage bytes>]` (even a single trailing byte).
3. Any node running `ChannelReader::next_batch` (full node, sequencer, or fault-proof program replaying that L1 block) calls `BatchReader::decompress()` → `decompress_brotli()` → `Brotli::decompress()`, which immediately allocates ~52 MiB of heap via `u8_buffer`/`u32_buffer`/`hc_buffer` before any validation of the (possibly single-byte or malformed) payload succeeds or fails [9](#0-8) .
4. Repeating step 2 with many small/cheap channels amplifies attacker-controlled L1 data into large, repeated heap allocations across every derivation consumer.

### Citations

**File:** crates/consensus/protocol/src/brotli.rs (L27-41)
```rust
    pub fn decompress(
        &self,
        data: &[u8],
        max_rlp_bytes_per_channel: usize,
    ) -> Result<Vec<u8>, BrotliDecompressionError> {
        declare_stack_allocator_struct!(MemPool, 4096, stack);

        let mut u8_buffer = vec![0; 32 * 1024 * 1024].into_boxed_slice();
        let mut u32_buffer = vec![0; 1024 * 1024].into_boxed_slice();
        let mut hc_buffer = vec![HuffmanCode::default(); 4 * 1024 * 1024].into_boxed_slice();
        let u8_allocator = MemPool::<u8>::new_allocator(&mut u8_buffer, bzero);
        let u32_allocator = MemPool::<u32>::new_allocator(&mut u32_buffer, bzero);
        let hc_allocator = MemPool::<HuffmanCode>::new_allocator(&mut hc_buffer, bzero);
        let mut brotli_state = BrotliState::new(u8_allocator, u32_allocator, hc_allocator);

```

**File:** crates/consensus/protocol/src/brotli.rs (L42-44)
```rust
        // Setup the decompressor inputs and outputs.
        // Cap initial buffer at the limit to prevent over-allocation.
        let mut output = vec![0; core::cmp::min(data.len(), max_rlp_bytes_per_channel)];
```

**File:** crates/consensus/protocol/src/batch/reader.rs (L116-135)
```rust
    fn decompress_zlib(&mut self, data: Vec<u8>) -> Result<(), DecompressionError> {
        // Decompress with a limit to prevent zip-bomb attacks.
        // Per spec, if decompressed data exceeds the limit, the output is
        // truncated to max_rlp_bytes_per_channel bytes (not rejected).
        match decompress_to_vec_zlib_with_limit(&data, self.max_rlp_bytes_per_channel) {
            Ok(decompressed) => {
                self.decompressed = decompressed;
            }
            Err(e) if e.status == TINFLStatus::HasMoreOutput || !e.output.is_empty() => {
                // Either: limit reached — truncate per spec and keep partial output.
                // Or: decompression error with partial output — keep it so
                // batches decoded before the error point are accepted.
                self.decompressed = e.output;
            }
            Err(_) => {
                return Err(DecompressionError::ZlibError);
            }
        }
        Ok(())
    }
```

**File:** crates/consensus/protocol/src/batch/reader.rs (L137-143)
```rust
    fn decompress_brotli(&mut self, data: Vec<u8>) -> Result<(), DecompressionError> {
        self.brotli_used = true;
        // Note: the first byte of the channel data is the Brotli channel version but not part of
        // the compressed data, so it's skipped here but not for zlib.
        self.decompressed = Brotli.decompress(&data[1..], self.max_rlp_bytes_per_channel)?;
        Ok(())
    }
```

**File:** crates/consensus/derive/src/stages/channel/channel_reader.rs (L117-135)
```rust
    async fn next_batch(&mut self) -> PipelineResult<Batch> {
        if let Err(e) = self.set_batch_reader().await {
            debug!(target: "channel_reader", error = ?e, "Failed to set batch reader");
            self.next_channel();
            return Err(e);
        }

        // SAFETY: The batch reader must be set above.
        let next_batch = self.next_batch.as_mut().expect("Batch reader must be set");
        let decompress_result =
            base_metrics::time!(Metrics::pipeline_batch_decompress_duration_seconds(), {
                next_batch.decompress()
            });
        match decompress_result {
            Ok(()) => {
                // Record the decompressed size and type.
                let size = next_batch.decompressed.len() as f64;
                let ty = if next_batch.brotli_used {
                    BatchReader::CHANNEL_VERSION_BROTLI
```

**File:** crates/common/rpc-types-engine/src/envelope.rs (L17-24)
```rust
/// Maximum allowed decoded size for a snappy-compressed [`NetworkPayloadEnvelope`].
///
/// Mirrors `MAX_GOSSIP_SIZE` in `base-consensus-gossip` and bounds the heap
/// allocation performed by [`NetworkPayloadEnvelope::decode_v1`] and friends.
/// Without this cap, a wire-valid 9 `MiB` snappy frame can declare a 200 `MiB`
/// decoded length and force the decoder to allocate that buffer before any
/// downstream length, SSZ, or signature check runs.
pub const MAX_DECOMPRESSED_ENVELOPE_BYTES: usize = 10 * (1 << 20);
```

**File:** crates/common/rpc-types-engine/src/payload/v4.rs (L92-113)
```rust
/// SSZ `transactions` list bounded to [`MAX_TRANSACTIONS_PER_PAYLOAD`].
///
/// Transactions are variable-length SSZ items, so the decoder derives their
/// count from the list's leading offset. Bounding the count rejects a frame that
/// declares more transactions than the protocol allows *before* one entry per
/// declared element is allocated, closing the pre-authentication
/// allocation-amplification vector.
#[cfg(feature = "std")]
#[derive(Debug)]
pub struct BoundedTransactions(pub Vec<Bytes>);

#[cfg(feature = "std")]
impl ssz::Decode for BoundedTransactions {
    fn is_ssz_fixed_len() -> bool {
        false
    }

    fn from_ssz_bytes(bytes: &[u8]) -> Result<Self, ssz::DecodeError> {
        ssz::decode_list_of_variable_length_items(bytes, Some(MAX_TRANSACTIONS_PER_PAYLOAD))
            .map(Self)
    }
}
```

**File:** crates/common/flashblocks/src/block.rs (L52-78)
```rust
    fn try_parse_message(bytes: Bytes) -> Result<String, FlashblockDecodeError> {
        if let Ok(text) = std::str::from_utf8(&bytes)
            && text.trim_start().starts_with('{')
        {
            if bytes.len() > MAX_DECOMPRESSED_FLASHBLOCK_BYTES {
                return Err(FlashblockDecodeError::PayloadTooLarge {
                    given: bytes.len(),
                    max: MAX_DECOMPRESSED_FLASHBLOCK_BYTES,
                });
            }
            return Ok(text.to_owned());
        }

        let decompressor = brotli::Decompressor::new(bytes.as_ref(), 4096);
        let mut bounded = decompressor.take((MAX_DECOMPRESSED_FLASHBLOCK_BYTES + 1) as u64);
        let mut decompressed = Vec::new();
        bounded.read_to_end(&mut decompressed).map_err(FlashblockDecodeError::Decompress)?;
        if decompressed.len() > MAX_DECOMPRESSED_FLASHBLOCK_BYTES {
            return Err(FlashblockDecodeError::PayloadTooLarge {
                given: decompressed.len(),
                max: MAX_DECOMPRESSED_FLASHBLOCK_BYTES,
            });
        }

        let text = String::from_utf8(decompressed).map_err(FlashblockDecodeError::Utf8)?;
        Ok(text)
    }
```
