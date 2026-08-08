### Title
`getMultipleAccounts` aborts the entire batch when a single account fails Base58/Binary encoding, instead of returning a per-account error - (File: `rpc/src/rpc.rs`)

### Summary
`getMultipleAccounts` fetches and encodes accounts for a batch of pubkeys in a loop, propagating the first encoding error with `?` and discarding all already-successfully-encoded results for the rest of the batch, mirroring the OrderBook `claim` "clashing" bug class where one bad item in a batch aborts the whole operation instead of being skipped.

### Finding Description
`JsonRpcRequestProcessor::get_multiple_accounts` iterates over the caller-supplied pubkey list and, for each pubkey, spawns a blocking task that calls `get_encoded_account`, immediately propagating any error with `?`: [1](#0-0) 

`get_encoded_account` in turn calls `encode_account(&account, pubkey, encoding, data_slice)?`, again propagating the error upward rather than converting it into a per-item `null`/error marker: [2](#0-1) 

`encode_account`/`encode_ui_account` enforce `MAX_BASE58_BYTES` (128 bytes) for `Binary`/`Base58` encodings; accounts whose data (after any `dataSlice`) exceeds this size cannot be safely Base58-encoded: [3](#0-2) 

The existing regression test `test_encode_account_does_not_throw_despite_account_and_dataslice_being_too_large_to_base58_encode_because_their_intersection_fits` confirms that `encode_account` can and does return an `Err` in the corresponding "too large" case when the account/data-slice intersection does not fit, and that this behavior was only patched for one specific overlap scenario: [4](#0-3) 

Because `get_multiple_accounts` uses `?` for each element of the loop rather than catching the per-account error and substituting `null`/an error field for that entry, a single "unencodable" account anywhere in the batch (e.g., an account with a large data buffer requested with `encoding: "base58"`/`"binary"`, or a jsonParsed-parsing edge case that still hits an error path) causes the entire multi-account RPC response to fail — discarding the correctly encoded data for every other, valid account in the same request. This is directly analogous to the OrderBook `claim(orderKeys[])` bug: a batch call over a list of independent items reverts/aborts wholesale due to one problematic entry, defeating the purpose of batching and wasting the caller's round trip.

### Impact Explanation
An unprivileged RPC client cannot retrieve any of the accounts in a `getMultipleAccounts` batch if even one pubkey in the list resolves to an account whose data cannot be Base58-encoded (larger than 128 bytes, a very common condition for real program accounts). This degrades the reliability/usability of a core read-only JSON-RPC method for legitimate multi-account queries and forces callers to issue N individual `getAccountInfo` calls to work around the failure, but does not itself corrupt validator state, crash the process, or affect consensus.

### Likelihood Explanation
High likelihood of accidental triggering: any client batching several accounts together with `base58`/`binary` encoding, where at least one has non-trivial data (>128 bytes), will experience a wholesale request failure rather than a graceful partial response. No secondary index abuse or repeated calls are needed — a single request with an unlucky mix of account sizes is sufficient.

### Recommendation
In `get_multiple_accounts` (`rpc/src/rpc.rs`), catch per-pubkey encoding errors from `encode_account`/`get_encoded_account` and convert them into a `null` or an encoded error indicator for that specific slot in the returned vector, rather than propagating with `?` and failing the whole batch — analogous to how the OrderBook fix skips (rather than reverts on) orders that would otherwise cause `_claim` to fail.

### Proof of Concept
1. Store an account with data length greater than `MAX_BASE58_BYTES` (128 bytes) alongside a normal, small account.
2. Call `getMultipleAccounts` with `encoding: "base58"` (or default `"binary"`), passing both pubkeys.
3. Observe that the request returns a top-level RPC error (from the failed encode of the large account) instead of a response array containing the successfully encoded small account plus an error/`null` placeholder for the large one — confirmed by the existing test at `rpc/src/rpc.rs:5791-5813`, which only patches the narrow case where a `dataSlice` happens to reduce the effective size below the limit, leaving the general multi-account case unaddressed. [5](#0-4)

### Citations

**File:** rpc/src/rpc.rs (L562-592)
```rust
    pub async fn get_multiple_accounts(
        &self,
        pubkeys: Vec<Pubkey>,
        config: Option<RpcAccountInfoConfig>,
    ) -> Result<RpcResponse<Vec<Option<UiAccount>>>> {
        let RpcAccountInfoConfig {
            encoding,
            data_slice,
            commitment,
            min_context_slot,
        } = config.unwrap_or_default();
        let bank = self.get_bank_with_config(RpcContextConfig {
            commitment,
            min_context_slot,
        })?;
        let encoding = encoding.unwrap_or(UiAccountEncoding::Base64);

        let mut accounts = Vec::with_capacity(pubkeys.len());
        for pubkey in pubkeys {
            let bank = Arc::clone(&bank);
            accounts.push(
                self.runtime
                    .spawn_blocking(move || {
                        get_encoded_account(&bank, &pubkey, encoding, data_slice, None)
                    })
                    .await
                    .expect("rpc: get_encoded_account panicked")?,
            );
        }
        Ok(new_response(&bank, accounts))
    }
```

**File:** rpc/src/rpc.rs (L2559-2573)
```rust
) -> Result<Option<UiAccount>> {
    match account_resolver::get_account_from_overwrites_or_bank(pubkey, bank, overwrite_accounts) {
        Some(account) => {
            let response = if is_known_spl_token_id(account.owner())
                && encoding == UiAccountEncoding::JsonParsed
            {
                get_parsed_token_account(bank, pubkey, account, overwrite_accounts)
            } else {
                encode_account(&account, pubkey, encoding, data_slice)?
            };
            Ok(Some(response))
        }
        None => Ok(None),
    }
}
```

**File:** rpc/src/rpc.rs (L5791-5813)
```rust
    #[test]
    fn test_encode_account_does_not_throw_despite_account_and_dataslice_being_too_large_to_base58_encode_because_their_intersection_fits()
     {
        let data = vec![42; MAX_BASE58_BYTES + 1];
        let pubkey = Pubkey::new_unique();
        let account = AccountSharedData::create_from_existing_shared_data(
            42,
            Arc::new(data),
            pubkey,
            false,
            0,
        );
        let result = encode_account(
            &account,
            &pubkey,
            UiAccountEncoding::Base58,
            Some(UiDataSliceConfig {
                length: MAX_BASE58_BYTES + 1,
                offset: 1,
            }),
        );
        assert!(result.is_ok());
    }
```

**File:** account-decoder/src/lib.rs (L31-44)
```rust
pub type StringDecimals = String;
pub const MAX_BASE58_BYTES: usize = 128;

fn encode_bs58<T: ReadableAccount>(
    account: &T,
    data_slice_config: Option<UiDataSliceConfig>,
) -> String {
    let slice = slice_data(account.data(), data_slice_config);
    if slice.len() <= MAX_BASE58_BYTES {
        bs58::encode(slice).into_string()
    } else {
        "error: data too large for bs58 encoding".to_string()
    }
}
```
