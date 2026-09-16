[1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6)

### Citations

**File:** crates/consensus/protocol/src/info/ecotone_base.rs (L112-130)
```rust
    /// This assumes the slice starts at byte offset 4 (after the 4-byte selector)
    /// and contains at least 164 bytes of data.
    ///
    /// # Safety
    /// This method assumes the slice is at least 164 bytes long and starts at
    /// the correct offset. Callers must validate the length before calling.
    pub fn decode_calldata_body(r: &[u8]) -> Self {
        // SAFETY: All slice operations below assume r is at least 164 bytes.
        // The caller must validate this before calling this method.

        // SAFETY: 4 bytes are copied directly into the array
        let mut base_fee_scalar = [0u8; 4];
        base_fee_scalar.copy_from_slice(&r[4..8]);
        let base_fee_scalar = u32::from_be_bytes(base_fee_scalar);

        // SAFETY: 4 bytes are copied directly into the array
        let mut blob_base_fee_scalar = [0u8; 4];
        blob_base_fee_scalar.copy_from_slice(&r[8..12]);
        let blob_base_fee_scalar = u32::from_be_bytes(blob_base_fee_scalar);
```

**File:** crates/batcher/blobs/src/decoder.rs (L55-68)
```rust
    pub fn decode(blob: &Blob) -> Result<Bytes, BlobDecodeError> {
        let data: &[u8; BYTES_PER_BLOB] = blob.as_ref();

        // Validate encoding version.
        let version = data[VERSIONED_HASH_VERSION_KZG as usize];
        if version != 0 {
            return Err(BlobDecodeError::InvalidEncodingVersion { version });
        }

        // Decode 3-byte big-endian length.
        let length = u32::from_be_bytes([0, data[2], data[3], data[4]]) as usize;
        if length > BlobEncoder::BLOB_MAX_DATA_SIZE {
            return Err(BlobDecodeError::InvalidLength { length });
        }
```

**File:** crates/consensus/protocol/src/batch/bits.rs (L30-49)
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
        let sb_bits = Self(bits);

        if sb_bits.bit_len() > bit_length {
            return Err(SpanBatchError::BitfieldTooLong);
        }

        Ok(sb_bits)
    }
```

**File:** crates/consensus/protocol/src/batch/tx_data/eip8130.rs (L49-115)
```rust
impl SpanBatchEip8130TransactionData {
    /// Fail-fast upper bound, in bytes, on a single EIP-8130 auth proof.
    ///
    /// A single proof cannot exceed the byte budget of the channel that carries
    /// the whole span batch, so it is derived from [`Channel::MAX_RLP_BYTES`].
    /// The genuine allocation bound is the subsequent `r.len() < n` check in the
    /// decoder; this constant only rejects an obviously corrupt length prefix
    /// before any copy is attempted.
    pub const MAX_AUTH_PROOF_BYTES: u64 = Channel::MAX_RLP_BYTES;

    /// Encodes an `Option<Address>` as a zero-length byte string when `None` and
    /// a 20-byte string when `Some`.
    fn encode_address_opt(addr: &Option<Address>, out: &mut dyn BufMut) {
        match addr {
            None => Bytes::new().encode(out),
            Some(a) => Bytes::copy_from_slice(a.as_slice()).encode(out),
        }
    }

    /// Length contribution of an `Option<Address>` under [`Self::encode_address_opt`].
    const fn address_opt_encoded_length(addr: &Option<Address>) -> usize {
        match addr {
            None => 1,
            Some(_) => 21,
        }
    }

    /// Decodes the [`Self::encode_address_opt`] wire format.
    fn decode_address_opt(buf: &mut &[u8]) -> alloy_rlp::Result<Option<Address>> {
        let raw = Bytes::decode(buf)?;
        match raw.len() {
            0 => Ok(None),
            20 => Ok(Some(Address::from_slice(&raw))),
            _ => Err(alloy_rlp::Error::Custom("invalid Option<Address> length")),
        }
    }

    fn rlp_encoded_fields_length(&self) -> usize {
        Self::address_opt_encoded_length(&self.sender)
            + self.nonce_key.length()
            + self.valid_after.length()
            + self.valid_before.length()
            + self.max_priority_fee_per_gas.length()
            + self.max_fee_per_gas.length()
            + Self::address_opt_encoded_length(&self.payer)
            + self.account_changes.length()
            + self.calls.length()
            + self.metadata.length()
            + self.sender_authenticator.length()
            + self.payer_authenticator.length()
    }

    /// Splits an authentication blob into its authenticator and proof parts.
    ///
    /// Inverse of `join_auth`. On the EOA path (`configured` is false)
    /// the authenticator is empty and the whole blob is the proof. On the
    /// configured-actor path the blob is `authenticator(20) || proof`; a
    /// configured blob shorter than 20 bytes is rejected.
    pub fn split_auth(blob: &Bytes, configured: bool) -> Result<(Bytes, Bytes), SpanBatchError> {
        if !configured {
            return Ok((Bytes::new(), blob.clone()));
        }
        if blob.len() < 20 {
            return Err(SpanBatchError::Decoding(SpanDecodingError::InvalidAuthData));
        }
        Ok((blob.slice(..20), blob.slice(20..)))
    }
```

**File:** crates/common/precompile-storage/src/types/bytes_like.rs (L212-244)
```rust
#[inline]
fn store_bytes_like<S: StorageOps>(bytes: &[u8], storage: &mut S, base_slot: U256) -> Result<()> {
    let new_length = bytes.len();
    let new_chunks = if new_length <= 31 { 0 } else { calc_chunks(new_length) };

    if storage.storage_features().dynamic_storage_tail_cleanup_enabled() {
        // Cobalt intentionally charges an old-metadata SLOAD on every bytes-like write so
        // shrinking values can be detected, including when reusing storage written pre-fork.
        storage.ensure_writable()?;
        clear_stale_long_tail(storage, base_slot, new_chunks)?;
    }

    if new_length <= 31 {
        storage.store(base_slot, encode_short_string(bytes))
    } else {
        storage.store(base_slot, encode_long_string_length(new_length))?;
        let slot_start = calc_data_slot(base_slot);

        for i in 0..new_chunks {
            let slot =
                slot_start.checked_add(U256::from(i)).ok_or(BasePrecompileError::SlotOverflow)?;
            let chunk_start = i * 32;
            let chunk_end = (chunk_start + 32).min(new_length);
            let chunk = &bytes[chunk_start..chunk_end];
            let mut chunk_bytes = [0u8; 32];
            chunk_bytes[..chunk.len()].copy_from_slice(chunk);
            storage.store(slot, U256::from_be_bytes(chunk_bytes))?;
        }

        Ok(())
    }
}

```

**File:** crates/proof/tee/registrar/src/cbor.rs (L220-268)
```rust
    /// Decodes the additional-information portion of a CBOR header.
    ///
    /// Returns `(value, header_length_including_initial_byte, indefinite)`.
    pub fn read_additional_info(
        bytes: &[u8],
        offset: usize,
        ai: u8,
    ) -> PlannerResult<(u64, usize, bool)> {
        match ai {
            0..=23 => Ok((u64::from(ai), 1, false)),
            24 => {
                if offset >= bytes.len() {
                    return Err(PlannerError::Cose("CBOR uint8 out of bounds".into()));
                }
                Ok((u64::from(bytes[offset]), 2, false))
            }
            25 => {
                if offset + 2 > bytes.len() {
                    return Err(PlannerError::Cose("CBOR uint16 out of bounds".into()));
                }
                Ok((u64::from(u16::from_be_bytes([bytes[offset], bytes[offset + 1]])), 3, false))
            }
            26 => {
                if offset + 4 > bytes.len() {
                    return Err(PlannerError::Cose("CBOR uint32 out of bounds".into()));
                }
                Ok((
                    u64::from(u32::from_be_bytes([
                        bytes[offset],
                        bytes[offset + 1],
                        bytes[offset + 2],
                        bytes[offset + 3],
                    ])),
                    5,
                    false,
                ))
            }
            27 => {
                if offset + 8 > bytes.len() {
                    return Err(PlannerError::Cose("CBOR uint64 out of bounds".into()));
                }
                let mut buf = [0u8; 8];
                buf.copy_from_slice(&bytes[offset..offset + 8]);
                Ok((u64::from_be_bytes(buf), 9, false))
            }
            31 => Ok((0, 1, true)),
            _ => Err(PlannerError::Cose(format!("unsupported CBOR additional information {ai}"))),
        }
    }
```

**File:** crates/consensus/protocol/src/batch/transactions.rs (L484-497)
```rust
    /// Regression: truncated input to `decode_tx_sigs` must return an error, not panic.
    /// A dishonest batcher can craft a span batch with fewer bytes than the declared tx count
    /// requires, which previously caused an out-of-bounds slice panic.
    #[test]
    fn test_decode_tx_sigs_truncated_input() {
        let mut txs = SpanBatchTransactions { total_block_tx_count: 1, ..Default::default() };
        // y_parity bitfield for 1 tx = 1 byte (all zeros = false parity), then we need 64 bytes
        // for r+s. Provide only 32 bytes to trigger the bounds check.
        let truncated = [0u8; 33]; // 1 byte bitfield + 32 bytes (not enough for 64-byte sig)
        assert_eq!(
            txs.decode_tx_sigs(&mut truncated.as_ref()),
            Err(SpanBatchError::Decoding(SpanDecodingError::InvalidTransactionData))
        );
    }
```
