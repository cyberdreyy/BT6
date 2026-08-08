### Title
Unbounded zstd decompression allows memory-exhaustion via `simulateTransaction` account overwrites - ([File: account-decoder-client-types/src/lib.rs])

### Summary
`UiAccountData::decode` for the `Base64Zstd` encoding decompresses attacker-supplied base64+zstd data with `zstd::stream::read::Decoder::new(...).read_to_end(&mut data)` and no cap on the decompressed output size. Since `to_account_shared_data`/`to_account` (which call `decode`) are used to materialize accounts supplied via `simulateTransaction`'s `accounts.overwrite` parameter, a single JSON-RPC call containing a small, highly compressible zstd blob can force the validator to allocate an arbitrarily large buffer.

### Finding Description
`UiAccountData::decode` handles the `Base64Zstd` branch by base64-decoding the client-supplied string and streaming it through `zstd::stream::read::Decoder`, then calling `read_to_end` on an unbounded `Vec<u8>`: [1](#0-0) 

There is no maximum decompressed-size check, no `Read::take`, and no length comparison against a bound (e.g., `MAX_PERMITTED_DATA_LENGTH`) before or during decompression. `read_to_end` will keep growing the `Vec` and decompressing until the zstd stream ends, so a compressed input of a few KB expanding to gigabytes (a classic "zstd bomb", e.g., all-zero data) will cause the process to attempt a correspondingly large allocation and CPU-bound decompression loop.

This method is used by `UiAccount::to_account_shared_data` and `UiAccount::to_account`: [2](#0-1) 

These conversions are invoked from the RPC layer (`rpc/src/rpc.rs`) when processing `simulateTransaction`'s `accounts.overwrite` map, which is then merged with real bank state via `get_account_from_overwrites_or_bank`: [3](#0-2) 

Since `overwrite_accounts` originates directly from client-supplied JSON-RPC parameters (a `UiAccount` map, each entry potentially using `Base64Zstd` encoding), an attacker fully controls the encoded bytes reaching `decode()`. No existing parameter-limit, commitment, or length-check guard intervenes between the base64 decode and the unbounded `read_to_end` call.

### Impact Explanation
A single `simulateTransaction` RPC call with a crafted `Base64Zstd` account blob in `accounts.overwrite` can force the validator's JSON-RPC handling thread to allocate and populate a very large `Vec<u8>` (bounded only by the zstd window/frame content size, which can be enormous relative to compressed size), consuming memory and CPU disproportionate to the request cost. This matches the "unbounded cost for a single low-rate call" category — a resource-exhaustion / DoS class RPC issue reachable with exactly one request.

### Likelihood Explanation
The precondition is simply calling `simulateTransaction` with an `accounts.overwrite` entry whose `data` field uses `base64+zstd` encoding, which is a standard, publicly documented client-facing encoding option. Constructing a highly compressible zstd payload (e.g., zeros) that expands enormously from a few KB of compressed data is straightforward with the `zstd` crate available to any client. This requires only one unprivileged JSON-RPC call, satisfying the attacker model (no more than one call per `CLUSTER_SLOT_TIME_TARGET / 2`).

### Recommendation
Bound decompression output size in `UiAccountData::decode`'s `Base64Zstd` branch — e.g., wrap the zstd `Decoder` output with `Read::take(MAX_PERMITTED_DATA_LENGTH)` (or another appropriate hard cap matching Solana's max account data size) before calling `read_to_end`, and reject/error if the decompressed stream exceeds that bound rather than reading to completion unconditionally. Additionally consider capping the size of the base64-encoded input itself before attempting decompression.

### Proof of Concept
```rust
// account-decoder-client-types/src/lib.rs (or a new integration test)
#[test]
fn test_base64_zstd_decompression_bomb_is_bounded() {
    use base64::{engine::general_purpose::STANDARD as BASE64_STANDARD, Engine};

    // Construct a highly compressible payload: e.g. 1 GiB of zeros.
    let huge_zeros = vec![0u8; 1024 * 1024 * 1024];
    let compressed = zstd::stream::encode_all(huge_zeros.as_slice(), 19).unwrap();
    // compressed will be only a few KB due to high compressibility.
    assert!(compressed.len() < 100 * 1024);

    let blob = BASE64_STANDARD.encode(&compressed);
    let ui_data = UiAccountData::Binary(blob, UiAccountEncoding::Base64Zstd);

    // Expected (after fix): decode() should return None or an error once the
    // decompressed size exceeds a defined MAX_PERMITTED_DATA_LENGTH bound,
    // instead of allocating/populating a 1 GiB Vec<u8>.
    let decoded = ui_data.decode();
    assert!(decoded.is_none(), "decode() must reject oversized decompressed data instead of materializing it");
}
```
This test currently fails against the unmodified code (it will allocate and return the full 1 GiB buffer), demonstrating the missing output-size cap. The same blob supplied via `simulateTransaction`'s `accounts.overwrite` parameter would reach this code path through `UiAccount::to_account_shared_data`.

### Citations

**File:** account-decoder-client-types/src/lib.rs (L46-55)
```rust
                #[cfg(feature = "zstd")]
                UiAccountEncoding::Base64Zstd => {
                    BASE64_STANDARD.decode(blob).ok().and_then(|zstd_data| {
                        let mut data = vec![];
                        zstd::stream::read::Decoder::new(zstd_data.as_slice())
                            .and_then(|mut reader| reader.read_to_end(&mut data))
                            .map(|_| data)
                            .ok()
                    })
                }
```

**File:** account-decoder-client-types/src/lib.rs (L75-96)
```rust
impl UiAccount {
    pub fn to_account_shared_data(&self) -> Option<AccountSharedData> {
        let data = Arc::new(self.data.decode()?);
        Some(AccountSharedData::create_from_existing_shared_data(
            self.lamports,
            data,
            Pubkey::from_str(&self.owner).ok()?,
            self.executable,
            self.rent_epoch,
        ))
    }

    pub fn to_account(&self) -> Option<Account> {
        let data = self.data.decode()?;
        Some(Account {
            lamports: self.lamports,
            data,
            owner: Pubkey::from_str(&self.owner).ok()?,
            executable: self.executable,
            rent_epoch: self.rent_epoch,
        })
    }
```

**File:** rpc/src/rpc/account_resolver.rs (L6-14)
```rust
pub(crate) fn get_account_from_overwrites_or_bank(
    pubkey: &Pubkey,
    bank: &Bank,
    overwrite_accounts: Option<&HashMap<Pubkey, AccountSharedData>>,
) -> Option<AccountSharedData> {
    overwrite_accounts
        .and_then(|accounts| accounts.get(pubkey).cloned())
        .or_else(|| bank.get_account(pubkey))
}
```
