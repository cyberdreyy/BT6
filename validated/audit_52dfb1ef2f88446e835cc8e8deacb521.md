### Title
Fixed-size stack buffer serialization of attacker-controlled `CrdsData` in `compute_crds_value_hash` may overflow if size invariant is violated - (File: gossip/src/crds_value.rs)

### Summary
`gossip/src/crds_value.rs` computes the identity hash of every deserialized gossip `CrdsValue` by serializing the (attacker-supplied) `CrdsData` into a **fixed-size, stack-allocated buffer** of exactly `PACKET_DATA_SIZE` bytes, relying solely on a code comment ("PACKET_DATA_SIZE is always enough since the value originated in a packet") rather than an enforced, checked bound at the point of use.

### Finding Description
`compute_crds_value_hash` is invoked from both the manual `SchemaRead` (`wincode`) implementation and the `serde::Deserialize` implementation of `CrdsValue` — i.e., it runs unconditionally on every `CrdsValue` decoded from the wire, *before* `Sanitize::sanitize()` (which enforces field-level bounds such as `MAX_WALLCLOCK`, `MAX_SLOT`, vote index bounds, etc.) is ever called: [1](#0-0) 

The function allocates a stack buffer sized exactly to `PACKET_DATA_SIZE` and writes the serialized `CrdsData` into it via `wincode::serialize_into`, with no explicit length check against the caller-visible `CrdsData` size before writing — the safety of the operation depends entirely on the assumption that the value "originated in a packet" (i.e., that whatever produced the byte stream being deserialized already enforced a `PACKET_DATA_SIZE` ceiling upstream): [2](#0-1) 

This mirrors the libmodbus root cause exactly: a response/serialization routine writes into a **fixed-capacity buffer sized for the "expected" (packet-bounded) case**, and correctness depends on an out-of-band invariant ("this always came from a packet") rather than a length check enforced at the write site itself. If that invariant is ever violated — e.g., a future refactor, an alternate deserialization entry point (bincode `Deserialize` is a public general-purpose trait impl, not gated to the socket-receive path), or a change in how `wincode`/`bincode` readers are constructed for `CrdsValue` — the write into the `[MaybeUninit<u8>; PACKET_DATA_SIZE]` stack array would not be validated by this function itself.

Whether this is *currently* exploitable depends entirely on `wincode`'s `Writer for &mut [MaybeUninit<u8>]` implementation (external to this repo, not present in the indexed code): if that writer performs unchecked/unsafe pointer writes trusting the destination length rather than returning an error/panicking safely on overrun, then any code path that can construct oversized `CrdsData` bytes ahead of this call (bypassing the "always from a packet" assumption) would corrupt the stack.

### Impact Explanation
If the size invariant can be violated by an attacker-reachable input, this becomes a stack-based buffer overflow while processing a single gossip message signature computation, which underlies every CRDS value verification path (`verify_with_cache`, `set_signature`) used for gossip messages that a validator receives from the network — a transaction-triggered cluster halt or arbitrary memory corruption class of bug. However, this could not be confirmed to be *currently* reachable: normal socket-receive gossip paths explicitly bound raw packet input to `PACKET_DATA_SIZE` before ever handing bytes to a `CrdsValue` deserializer, and the `Deserialize`/`SchemaRead` implementations here are otherwise generic trait impls without their own length bound.

### Likelihood Explanation
Low-to-uncertain. All identified concrete call sites (`protocol.rs`'s `deserialize_protocol`, gossip socket ingestion) already cap the input at `PACKET_DATA_SIZE` before deserialization reaches `CrdsValue`, so the documented invariant appears to hold for the paths actually exercised in this codebase. No path was found in the indexed code where a `CrdsValue`/`CrdsData` larger than `PACKET_DATA_SIZE` reaches `compute_crds_value_hash`. The `wincode` crate's writer implementation for `&mut [MaybeUninit<u8>]`, which would determine whether an oversized write is actually memory-unsafe or merely returns an error, could not be located/verified in this index.

### Recommendation
Because the exploitability hinges on (a) whether any current or future caller can present `CrdsData` larger than `PACKET_DATA_SIZE` to `CrdsValue`'s deserializers, and (b) the internal behavior of `wincode`'s bounded-slice `Writer`, this should be treated as a defense-in-depth gap rather than a confirmed exploitable bug from the indexed code alone. Recommend replacing the comment-only invariant in `compute_crds_value_hash` with an explicit length check (or use of a writer/API that always fails safely rather than performing unchecked pointer writes) before shipping any conclusion of unexploitability, and auditing all `wincode`/`bincode` deserialization entry points for `CrdsValue` to confirm none bypass the packet-size ceiling.

### Proof of Concept
Could not be constructed with confidence: reproducing a concrete overflow requires either (1) evidence that `wincode`'s fixed-slice `Writer` performs unchecked pointer writes on overrun (source not present in this index), or (2) a demonstrated deserialization entry point for `CrdsValue`/`CrdsData` that is not bounded to `PACKET_DATA_SIZE` before reaching `compute_crds_value_hash`. Neither could be confirmed from the available codebase context, so no working PoC is provided.

### Citations

**File:** gossip/src/crds_value.rs (L259-292)
```rust
// Computes sha256(signature || serialize(data)) using a stack buffer.
// PACKET_DATA_SIZE is always enough since the value originated in a packet.
fn compute_crds_value_hash(signature: &Signature, data: &CrdsData) -> wincode::WriteResult<Hash> {
    let mut buffer = [MaybeUninit::<u8>::uninit(); PACKET_DATA_SIZE];
    let mut writer: &mut [MaybeUninit<u8>] = &mut buffer;
    wincode::serialize_into(&mut writer, data)?;
    let written = PACKET_DATA_SIZE - writer.len();
    // SAFETY: wincode's "Writer for &mut [MaybeUninit<u8>]" initializes every
    // consumed slot before advancing the cursor, so the first "written" bytes are init.
    let bytes = unsafe { std::slice::from_raw_parts(buffer.as_ptr().cast::<u8>(), written) };
    Ok(hash_signed_data(signature, bytes))
}

// Manual implementation of SchemaRead for CrdsValue in order to populate
// CrdsValue.hash which is skipped in serialization.
unsafe impl<'de, C: Config> SchemaRead<'de, C> for CrdsValue {
    type Dst = Self;
    fn read(reader: impl Reader<'de>, dst: &mut MaybeUninit<Self>) -> ReadResult<()> {
        #[derive(SchemaRead)]
        struct CrdsValueLite {
            signature: Signature,
            data: CrdsData,
        }
        let CrdsValueLite { signature, data } = <CrdsValueLite as SchemaRead<'de, C>>::get(reader)?;
        let hash = compute_crds_value_hash(&signature, &data)
            .map_err(|_| ReadError::Custom("failed to serialize CrdsData"))?;
        dst.write(Self {
            signature,
            data,
            hash,
        });
        Ok(())
    }
}
```
