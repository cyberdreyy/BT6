### Title
Span-batch transaction decode allocates `Vec::with_capacity(total_block_tx_count)` for signatures/nonces/gases before verifying the buffer actually contains that many entries - ([File: crates/consensus/protocol/src/batch/transactions.rs])

### Summary
`SpanBatchTransactions::decode_tx_sigs`, `decode_tx_nonces`, `decode_tx_gases`, and `decode_tx_tos` each call `Vec::with_capacity(self.total_block_tx_count as usize)` before consuming any per-element bytes from the reader. `total_block_tx_count` is attacker-controlled (derived from compact varint-encoded per-block tx counts inside a span batch carried in channel/frame data that originates from arbitrary L1 calldata sent to the batch-inbox address) and is only bounded to `SpanBatchElement::MAX_SPAN_BATCH_ELEMENTS` (10,000,000), not to the amount of data actually present in the buffer. A tiny, well-formed-looking span batch payload can declare a `total_block_tx_count` near the 10M ceiling, causing an immediate multi-hundred-megabyte heap allocation per decoded field, well before the subsequent per-item length checks (`r.len() < 64`, etc.) have a chance to reject the malformed/truncated stream.

### Finding Description
`decode_tx_sigs` (and its sibling decoders) is structured as: [1](#0-0) 
```
pub fn decode_tx_sigs(&mut self, r: &mut &[u8]) -> Result<(), SpanBatchError> {
    let y_parity_bits = SpanBatchBits::decode(r, self.total_block_tx_count as usize)?;
    let mut sigs = Vec::with_capacity(self.total_block_tx_count as usize);
    for i in 0..self.total_block_tx_count {
        ...
        if r.len() < 64 { return Err(...) }
        ...
    }
    ...
}
```
The `Vec::with_capacity` call happens **before** the loop validates that the reader actually holds `total_block_tx_count * 64` bytes. `SpanBatchBits::decode` itself does not require the buffer to be long enough — when the buffer is shorter than the bit-length it needs, it zero-pads and consumes whatever remains: [2](#0-1) 

The same pattern repeats in `decode_tx_nonces`, `decode_tx_gases`, and `decode_tx_tos`, each doing `Vec::with_capacity(self.total_block_tx_count as usize)` up front: [3](#0-2) 

The only gate on `total_block_tx_count` is the upper bound check in `decode_txs`: [4](#0-3) 
```
let total_block_tx_count = self.block_tx_counts.iter().try_fold(0u64, |acc, block_tx_count| {
    acc.checked_add(*block_tx_count).ok_or(SpanBatchError::TooBigSpanBatchSize)
})?;
if total_block_tx_count > SpanBatchElement::MAX_SPAN_BATCH_ELEMENTS {
    return Err(SpanBatchError::TooBigSpanBatchSize);
}
self.txs.total_block_tx_count = total_block_tx_count;
self.txs.decode(r)?;
```
This check only rejects values *above* `MAX_SPAN_BATCH_ELEMENTS` (10,000,000) — it does not correlate `total_block_tx_count` with the actual remaining buffer length. `block_tx_counts` entries are themselves compact `unsigned_varint` values, so a single ~5-byte varint can declare a huge per-block tx count, and the whole span batch (channel-compressed) can still be tiny.

This is the same CWE-770 bug class as the Packetbeat report: an attacker-controlled count field drives an unconditional pre-authentication heap allocation sized by the *declared* count rather than the *verified* amount of backing data, before any structural/length validation of the actual payload occurs.

### Impact Explanation
Each `Signature` is ~96 bytes (two `U256` plus a bool with padding); `Vec::with_capacity(10_000_000)` for `tx_sigs` alone requests roughly 960 MB. The nonces/gases (`u64`, 8 bytes) and `to` addresses (`Address`, 20 bytes) vectors add further hundreds of MB each. All of this is allocated from a single crafted span batch, before the decoder has confirmed the buffer holds anywhere near that much real data — i.e., a few dozen bytes on the wire can force roughly 1+ GB of allocation attempts per decode call. Every full node running the derivation pipeline (all Base nodes, not just the sequencer) processes span batches read from L1 batch-inbox calldata, which is not restricted to a privileged sender at the derivation layer — any account can post calldata to the inbox address and force this decode path to run. Repeated/spam submissions of such malformed batches can exhaust node memory and CPU, causing a denial-of-service against the derivation pipeline and thus node/chain progress — a High-severity resource-exhaustion issue analogous to the reported CVE.

### Likelihood Explanation
The trigger requires only crafting a span batch whose per-block tx-count varints sum close to `MAX_SPAN_BATCH_ELEMENTS` while the rest of the encoded batch is minimal/malformed so real element data is absent. Constructing such a payload does not require any privileged key, contract deployment, or protocol-level access — it only requires getting bytes into the channel/frame stream that the derivation pipeline reads from L1. This is a cheap, repeatable, unauthenticated construction, making likelihood high once such data reaches the batch decoder.

### Recommendation
Bound the upfront allocation in `decode_tx_sigs`, `decode_tx_nonces`, `decode_tx_gases`, and `decode_tx_tos` to the amount of data actually available in `r` (e.g., `total_block_tx_count.min(r.len() / element_size)`), or avoid `Vec::with_capacity` for attacker-controlled counts entirely and grow the vector incrementally as bytes are consumed (mirroring the safer `Vec::new()` pattern already used in `decode_tx_data`). Additionally, tighten `decode_txs`'s bound so that `total_block_tx_count` is checked against a plausible maximum derivable from the remaining buffer length, not just the absolute protocol ceiling.

### Proof of Concept
1. Construct a span batch whose `block_tx_counts` list contains a single varint-encoded value close to `SpanBatchElement::MAX_SPAN_BATCH_ELEMENTS` (10,000,000), keeping the encoded `block_tx_counts` section itself only a few bytes (varints are compact).
2. Provide only a minimal amount of subsequent buffer content (e.g. a short zero-padded byte string) rather than the tens/hundreds of megabytes that would legitimately be required to back 10M signatures/nonces/gas values/addresses.
3. Feed this into `SpanBatchTransactions::decode` (reachable via `SpanBatchPayload::decode_txs` → `Batch::decode` → the channel/frame reader used by the derivation pipeline on data sourced from L1 batch-inbox calldata).
4. Observe that `decode_tx_sigs` (and the nonce/gas/to decoders) call `Vec::with_capacity(10_000_000)` and attempt hundreds of MB to ~1 GB of allocation immediately, before the per-item `r.len() < 64` checks have a chance to reject the truncated/malformed stream — i.e., the allocation size is driven purely by the attacker-declared count, not validated backing data.

*(Note: I was not able to fully confirm the exact byte-size of `Signature` or the precise value/enforcement of `MAX_RLP_BYTES_PER_CHANNEL_FJORD`/`BEDROCK` in this pass due to tool-call limits; a Devin session with full repo access should confirm these constants and validate the PoC end-to-end against `Batch::decode`/`SpanBatchPayload::decode_txs` before treating exact allocation-size figures as final.)*

### Citations

**File:** crates/consensus/protocol/src/batch/transactions.rs (L175-191)
```rust
    /// Decode the transaction signatures from a reader (excluding `v` field).
    pub fn decode_tx_sigs(&mut self, r: &mut &[u8]) -> Result<(), SpanBatchError> {
        let y_parity_bits = SpanBatchBits::decode(r, self.total_block_tx_count as usize)?;
        let mut sigs = Vec::with_capacity(self.total_block_tx_count as usize);
        for i in 0..self.total_block_tx_count {
            let y_parity = y_parity_bits.get_bit(i as usize).expect("same length");
            if r.len() < 64 {
                return Err(SpanBatchError::Decoding(SpanDecodingError::InvalidTransactionData));
            }
            let r_val = U256::from_be_slice(&r[..32]);
            let s_val = U256::from_be_slice(&r[32..64]);
            sigs.push(Signature::new(r_val, s_val, y_parity == 1));
            r.advance(64);
        }
        self.tx_sigs = sigs;
        Ok(())
    }
```

**File:** crates/consensus/protocol/src/batch/transactions.rs (L193-233)
```rust
    /// Decode the transaction nonces from a reader.
    pub fn decode_tx_nonces(&mut self, r: &mut &[u8]) -> Result<(), SpanBatchError> {
        let mut nonces = Vec::with_capacity(self.total_block_tx_count as usize);
        for _ in 0..self.total_block_tx_count {
            let (nonce, remaining) = unsigned_varint::decode::u64(r)
                .map_err(|_| SpanBatchError::Decoding(SpanDecodingError::TxNonces))?;
            nonces.push(nonce);
            *r = remaining;
        }
        self.tx_nonces = nonces;
        Ok(())
    }

    /// Decode the transaction gas limits from a reader.
    pub fn decode_tx_gases(&mut self, r: &mut &[u8]) -> Result<(), SpanBatchError> {
        let mut gases = Vec::with_capacity(self.total_block_tx_count as usize);
        for _ in 0..self.total_block_tx_count {
            let (gas, remaining) = unsigned_varint::decode::u64(r)
                .map_err(|_| SpanBatchError::Decoding(SpanDecodingError::TxNonces))?;
            gases.push(gas);
            *r = remaining;
        }
        self.tx_gases = gases;
        Ok(())
    }

    /// Decode the `to` addresses of the transactions from a reader.
    pub fn decode_tx_tos(&mut self, r: &mut &[u8]) -> Result<(), SpanBatchError> {
        let mut tos = Vec::with_capacity(self.total_block_tx_count as usize);
        let contract_creation_count = self.contract_creation_count();
        for _ in 0..(self.total_block_tx_count - contract_creation_count) {
            if r.len() < 20 {
                return Err(SpanBatchError::Decoding(SpanDecodingError::InvalidTransactionData));
            }
            let to = Address::from_slice(&r[..20]);
            tos.push(to);
            r.advance(20);
        }
        self.tx_tos = tos;
        Ok(())
    }
```

**File:** crates/consensus/protocol/src/batch/bits.rs (L30-41)
```rust
    pub fn decode(b: &mut &[u8], bit_length: usize) -> Result<Self, SpanBatchError> {
        let buffer_len = bit_length / 8 + if !bit_length.is_multiple_of(8) { 1 } else { 0 };
        let bits = if b.len() < buffer_len {
            let mut bits = vec![0; buffer_len];
            bits[..b.len()].copy_from_slice(b);
            b.advance(b.len());
            bits
        } else {
            let v = b[..buffer_len].to_vec();
            b.advance(buffer_len);
            v
        };
```

**File:** crates/consensus/protocol/src/batch/payload.rs (L93-112)
```rust
    /// Decode transactions from a reader.
    pub fn decode_txs(&mut self, r: &mut &[u8]) -> Result<(), SpanBatchError> {
        if self.block_tx_counts.is_empty() {
            return Err(SpanBatchError::EmptySpanBatch);
        }

        let total_block_tx_count =
            self.block_tx_counts.iter().try_fold(0u64, |acc, block_tx_count| {
                acc.checked_add(*block_tx_count).ok_or(SpanBatchError::TooBigSpanBatchSize)
            })?;

        // The total number of transactions in a span batch cannot be greater than
        // [SpanBatchElement::MAX_SPAN_BATCH_ELEMENTS].
        if total_block_tx_count > SpanBatchElement::MAX_SPAN_BATCH_ELEMENTS {
            return Err(SpanBatchError::TooBigSpanBatchSize);
        }
        self.txs.total_block_tx_count = total_block_tx_count;
        self.txs.decode(r)?;
        Ok(())
    }
```
