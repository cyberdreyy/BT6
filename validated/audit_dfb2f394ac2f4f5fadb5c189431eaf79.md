[1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** crates/consensus/protocol/src/batch/transactions.rs (L274-289)
```rust
    /// Decode a single uvarint-prefixed EIP-8130 auth proof from a reader.
    fn decode_eip8130_proof(r: &mut &[u8]) -> Result<Bytes, SpanBatchError> {
        let (n, remaining) = unsigned_varint::decode::u64(r)
            .map_err(|_| SpanBatchError::Decoding(SpanDecodingError::InvalidAuthData))?;
        *r = remaining;
        if n > SpanBatchEip8130TransactionData::MAX_AUTH_PROOF_BYTES {
            return Err(SpanBatchError::TooBigSpanBatchSize);
        }
        let n = n as usize;
        if r.len() < n {
            return Err(SpanBatchError::Decoding(SpanDecodingError::InvalidTransactionData));
        }
        let proof = Bytes::copy_from_slice(&r[..n]);
        r.advance(n);
        Ok(proof)
    }
```

**File:** crates/proof/tee/registrar/src/cbor.rs (L88-128)
```rust
impl CborItem {
    /// Decodes the complete CBOR item at `start`.
    pub fn read(bytes: &[u8], start: usize) -> PlannerResult<Self> {
        Self::read_at(bytes, start, 0)
    }

    /// Decodes an item with `depth` enclosing containers already entered.
    pub fn read_at(bytes: &[u8], start: usize, depth: usize) -> PlannerResult<Self> {
        if depth > MAX_CBOR_NESTING_DEPTH {
            return Err(PlannerError::Cose(format!(
                "CBOR nesting exceeds maximum depth {MAX_CBOR_NESTING_DEPTH}"
            )));
        }
        if start >= bytes.len() {
            return Err(PlannerError::Cose("CBOR read out of bounds".into()));
        }

        let initial = bytes[start];
        let major = initial >> 5;
        let ai = initial & 0x1f;
        let (value, header_len, indefinite) = Self::read_additional_info(bytes, start + 1, ai)?;
        let content_start = start + header_len;
        let mut end = content_start;

        match major {
            CBOR_MAJOR_BYTE_STRING | CBOR_MAJOR_TEXT_STRING => {
                if indefinite {
                    return Err(PlannerError::Cose(
                        "unsupported indefinite CBOR byte/text string".into(),
                    ));
                }
                let len = usize::try_from(value).map_err(|_| {
                    PlannerError::Cose("CBOR item length exceeds platform address space".into())
                })?;
                let Some(content_end) = content_start.checked_add(len) else {
                    return Err(PlannerError::Cose("CBOR item length out of bounds".into()));
                };
                if content_end > bytes.len() {
                    return Err(PlannerError::Cose("CBOR item length out of bounds".into()));
                }
                end = content_end;
```

**File:** crates/consensus/protocol/src/info/ecotone_base.rs (L112-124)
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
```
