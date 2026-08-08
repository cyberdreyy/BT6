### Title
`getProgramAccounts`/`getTokenAccountsByOwner`/`getTokenAccountsByDelegate` abort the entire response when a single scanned account's data exceeds the Base58 size limit - (File: rpc/src/rpc.rs)

### Summary
The bug class described in the report is "one malicious/oversized item in a batch causes the whole atomic operation to fail, blocking access to otherwise-legitimate items." The same shape exists in the account-scanning RPC handlers: when encoding a *list* of accounts, a single account whose data (after any `dataSlice`) exceeds `MAX_BASE58_BYTES` (128 bytes) causes `encode_account` to return an `Err`, which is propagated with `?` through `collect::<Result<Vec<_>>>()`, discarding the entire result set instead of only the offending account.

### Finding Description
`encode_account` explicitly rejects Base58/Binary-encoded data slices larger than 128 bytes with a hard `Err`: [1](#0-0) 

This function is used in list-producing handlers that eagerly `collect` a `Result<Vec<_>>` and bail on the first error via `?`:
- `get_program_accounts`: [2](#0-1) 
- `get_token_accounts_by_delegate` (and analogously `get_token_accounts_by_owner`): [3](#0-2) 
- The `accounts` post-simulation config path of `simulateTransaction`: [4](#0-3) 

Because these use `.map(...).collect::<Result<Vec<_>>>()?`, encoding is attempted for every keyed account returned by the scan, but as soon as any single account fails to encode (data > 128 bytes with Base58/Binary encoding), the `?` propagates that single account's error and the entire RPC call fails - even though every other account in the result set would have encoded successfully. This mirrors the "malicious reward token" bug class: a single bad element in an otherwise-fine collection blocks legitimate elements from being delivered at all.

A confirming unit test shows the panic/error behavior for a single-account call, and the same `encode_account` function backs the list encoders above: [5](#0-4) 

`getProgramAccounts` scans over accounts owned by an arbitrary program, and any account owner (the program) can legitimately have accounts with data far exceeding 128 bytes (Solana accounts can hold up to 10 MiB). Any unprivileged party who can create/write an account under a given program (e.g., their own program, or any permissionless token/mint account under `spl-token`) can therefore poison every `getProgramAccounts`/`getTokenAccountsBy*` call scoped to that program that uses `encoding: "base58"` or `"binary"`, denying all callers a legitimate response for that program even though only one account is oversized.

### Impact Explanation
This is a functional-correctness/availability bug reachable via unprivileged JSON-RPC calls: `getProgramAccounts`, `getTokenAccountsByOwner`, `getTokenAccountsByDelegate` (and the `accounts` field of `simulateTransaction`). A single oversized account under the scanned program/owner/delegate causes the *entire* RPC response to fail with an `InvalidRequest` error instead of returning the other, legitimately-encodable accounts. Any RPC consumer relying on Base58/Binary encoding for account scans against a program that has (or gains) even one large account is denied all data for that call. This does not crash the validator process, but it silently blocks legitimate data delivery for an entire class of RPC requests, analogous to how the malicious reward token blocked legitimate reward claims in the external report.

### Likelihood Explanation
Likelihood is high for callers using Base58/Binary encoding (the default historic encoding for `getProgramAccounts`), and the triggering condition (one account >128 bytes under the target program/owner/delegate) is trivial and already common in practice for most non-trivial programs (e.g., stake accounts, most SPL token mint extensions, custom program state). No special privileges are needed to create such an account.

### Recommendation
Do not fail the whole scan when a single account fails to encode. Instead, either:
- Skip/omit or null-out only the offending account (mirroring how `getMultipleAccounts`/`get_encoded_account` already returns `None` per-pubkey rather than failing the batch), or
- Fall back to Base64 encoding for that individual account (as is already done for `Base64Zstd` failures and `JsonParsed` parse failures in `encode_ui_account`): [6](#0-5) 

### Proof of Concept
1. Create/observe a program `P` that has at least one account with data length > 128 bytes (trivial for most real programs).
2. Call `getProgramAccounts(P, {encoding: "base58"})` (or `"binary"`).
3. Observe the RPC handler iterate all matching accounts via `get_program_accounts` → `encode_account` → `.collect::<Result<Vec<_>>>()?` at [7](#0-6) .
4. The single oversized account triggers the `Err` branch in `encode_account` at [8](#0-7) , and the whole request returns an `InvalidRequest` JSON-RPC error instead of the list of accounts, denying the caller all legitimate results for program `P`.

### Citations

**File:** rpc/src/rpc.rs (L656-666)
```rust
        } else {
            keyed_accounts
                .into_iter()
                .map(|(pubkey, account)| {
                    Ok(RpcKeyedAccount {
                        pubkey: pubkey.to_string(),
                        account: encode_account(&account, &pubkey, encoding, data_slice_config)?,
                    })
                })
                .collect::<Result<Vec<_>>>()?
        };
```

**File:** rpc/src/rpc.rs (L2236-2248)
```rust
        let accounts = if encoding == UiAccountEncoding::JsonParsed {
            get_parsed_token_accounts(bank.clone(), keyed_accounts.into_iter()).collect()
        } else {
            keyed_accounts
                .into_iter()
                .map(|(pubkey, account)| {
                    Ok(RpcKeyedAccount {
                        pubkey: pubkey.to_string(),
                        account: encode_account(&account, &pubkey, encoding, data_slice_config)?,
                    })
                })
                .collect::<Result<Vec<_>>>()?
        };
```

**File:** rpc/src/rpc.rs (L2575-2601)
```rust
fn encode_account<T: ReadableAccount>(
    account: &T,
    pubkey: &Pubkey,
    encoding: UiAccountEncoding,
    data_slice: Option<UiDataSliceConfig>,
) -> Result<UiAccount> {
    if (encoding == UiAccountEncoding::Binary || encoding == UiAccountEncoding::Base58)
        && data_slice
            .map(|s| min(s.length, account.data().len().saturating_sub(s.offset)))
            .unwrap_or(account.data().len())
            > MAX_BASE58_BYTES
    {
        let message = format!(
            "Encoded binary (base 58) data should be less than {MAX_BASE58_BYTES} bytes, please \
             use Base64 encoding."
        );
        Err(error::Error {
            code: error::ErrorCode::InvalidRequest,
            message,
            data: None,
        })
    } else {
        Ok(encode_ui_account(
            pubkey, account, encoding, None, data_slice,
        ))
    }
}
```

**File:** rpc/src/rpc.rs (L4117-4132)
```rust
                    Some(
                        config_accounts
                            .addresses
                            .iter()
                            .map(|address_str| {
                                let pubkey = verify_pubkey(address_str)?;
                                get_encoded_account(
                                    bank,
                                    &pubkey,
                                    accounts_encoding,
                                    None,
                                    Some(&post_simulation_accounts_map),
                                )
                            })
                            .collect::<Result<Vec<_>>>()?,
                    )
```

**File:** rpc/src/rpc.rs (L5728-5741)
```rust
    #[test]
    #[should_panic(expected = "should be less than 128 bytes")] // If ever `MAX_BASE58_BYTES` changes, the expected error message will need to be updated.
    fn test_encode_account_throws_when_data_too_large_to_base58_encode() {
        let data = vec![42; MAX_BASE58_BYTES + 1];
        let pubkey = Pubkey::new_unique();
        let account = AccountSharedData::create_from_existing_shared_data(
            42,
            Arc::new(data),
            pubkey,
            false,
            0,
        );
        let _ = encode_account(&account, &pubkey, UiAccountEncoding::Base58, None).unwrap();
    }
```

**File:** account-decoder/src/lib.rs (L67-91)
```rust
        UiAccountEncoding::Base64Zstd => {
            let mut encoder = zstd::stream::write::Encoder::new(Vec::new(), 0).unwrap();
            match encoder
                .write_all(slice_data(account.data(), data_slice_config))
                .and_then(|()| encoder.finish())
            {
                Ok(zstd_data) => UiAccountData::Binary(BASE64_STANDARD.encode(zstd_data), encoding),
                Err(_) => UiAccountData::Binary(
                    BASE64_STANDARD.encode(slice_data(account.data(), data_slice_config)),
                    UiAccountEncoding::Base64,
                ),
            }
        }
        UiAccountEncoding::JsonParsed => {
            if let Ok(parsed_data) =
                parse_account_data_v3(pubkey, account.owner(), account.data(), additional_data)
            {
                UiAccountData::Json(parsed_data)
            } else {
                UiAccountData::Binary(
                    BASE64_STANDARD.encode(slice_data(account.data(), data_slice_config)),
                    UiAccountEncoding::Base64,
                )
            }
        }
```
