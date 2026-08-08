### Title
`getMultipleAccounts` fails entirely for all requested accounts if a single account's data cannot fit `base58` encoding - (File: `rpc/src/rpc.rs`)

### Summary
`JsonRpcRequestProcessor::get_multiple_accounts` iterates over the caller-supplied list of pubkeys and calls `get_encoded_account` for each one, propagating any single per-account error with `?`. Because `encode_account` returns a hard `Err` when an account's data (or data-slice) exceeds `MAX_BASE58_BYTES` under `Binary`/`Base58` encoding, a single oversized account anywhere in the batch aborts the whole RPC call, so the client gets no data for any of the other (perfectly valid, small) accounts in the same request. This mirrors the reported bug class where a single item's missing/invalid configuration in a batched loop (`vault.claimReward()` iterating markets) causes the entire operation to fail for all other unaffected items.

### Finding Description
`get_multiple_accounts` loops over every requested pubkey and, for each one, spawns a blocking task that calls `get_encoded_account`, then immediately propagates any error out of the loop with `?`: [1](#0-0) 

`get_encoded_account` in turn calls `encode_account`, which explicitly returns `Err(...)` (an `InvalidRequest` JSON-RPC error) whenever the (possibly sliced) account data for `Binary`/`Base58` encoding exceeds `MAX_BASE58_BYTES` (128 bytes): [2](#0-1) 

Because the loop in `get_multiple_accounts` uses `?` on the per-account result rather than collecting a per-item `Option`/error, any single account in the requested list that is too large to base58-encode causes the entire `getMultipleAccounts` call to return an error instead of a response — even though the remaining pubkeys in the same request are ordinary small accounts that could be encoded without any problem. This is directly analogous to the M-13 pattern: a per-item condition (`reward == address(0)` in the audit finding; oversized account data here) that is not checked/skipped before the shared batch operation, causing the whole batch to fail because of one non-conforming item.

By contrast, note that `Binary`/`Base58` per-account failures are already gracefully handled inside `encode_ui_account`/`encode_bs58` for the *single*-account `getAccountInfo` path and for pubsub account-subscription notifications (both substitute an `"error: data too large for bs58 encoding"` string rather than failing the request): [3](#0-2) 

but this graceful degradation is bypassed specifically in the multi-account RPC path (`get_multiple_accounts`/`encode_account`), which instead hard-errors and takes down the whole batch.

### Impact Explanation
Any unprivileged JSON-RPC client calling `getMultipleAccounts` with `encoding: "base58"` (or the deprecated default `"binary"`) and a pubkey list that includes even one account whose data (after any `dataSlice`) exceeds 128 bytes will receive an `InvalidRequest` error for the *entire* call, losing access to the other, unaffected accounts' data in that same request. This is a "wrong data returned" outcome for a completely ordinary single RPC call: the client cannot retrieve valid, publicly-readable account data it is otherwise entitled to, purely because it was batched together with one large account. It also creates an availability gotcha for any tooling that batches `getMultipleAccounts` requests (a common client pattern to reduce RPC call counts), since a single misplaced large-data pubkey silently breaks the whole batch response.

### Likelihood Explanation
Trivial to trigger: any account with data > 128 bytes (very common — token mints with extensions, program data accounts, most PDAs beyond trivial fixed structs) combined with `encoding: base58` in a single `getMultipleAccounts` call. No special permissions, no multiple calls, and no reliance on other clients or maliciously crafted data are required — it is a one-call, deterministic condition based purely on the size of publicly-known account data.

### Recommendation
Mirror the graceful degradation already used by `encode_ui_account`/pubsub notifications: instead of propagating a hard `Err` from `encode_account` for oversized `Binary`/`Base58` accounts inside the `get_multiple_accounts` loop, either (a) substitute a per-account placeholder/error value (consistent with `encode_bs58`'s `"error: data too large for bs58 encoding"` behavior) so other accounts in the batch still succeed, or (b) collect per-account results as `Result` and return them individually in the response array rather than short-circuiting the whole batch with `?`.

### Proof of Concept
1. Store/find an account whose data length exceeds `MAX_BASE58_BYTES` (128 bytes) — e.g. `test_encode_account_throws_when_data_too_large_to_base58_encode` demonstrates `encode_account` erroring in exactly this scenario: [4](#0-3) 
2. Call `getMultipleAccounts` with `["<small_account_pubkey>", "<oversized_account_pubkey>"]` and `{"encoding": "base58"}`.
3. Observe that the RPC call fails with `InvalidRequest`/"Encoded binary (base 58) data should be less than 128 bytes..." for the whole request, even though `<small_account_pubkey>` alone (or via `getAccountInfo`/`getMultipleAccounts` without the oversized entry) would succeed, confirmed by the existing single-account success test paths in `test_rpc_get_multiple_accounts`: [5](#0-4)

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

**File:** rpc/src/rpc.rs (L2552-2601)
```rust
fn get_encoded_account(
    bank: &Bank,
    pubkey: &Pubkey,
    encoding: UiAccountEncoding,
    data_slice: Option<UiDataSliceConfig>,
    // only used for simulation results
    overwrite_accounts: Option<&HashMap<Pubkey, AccountSharedData>>,
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

**File:** rpc/src/rpc.rs (L5815-5862)
```rust
    #[test]
    fn test_rpc_get_multiple_accounts() {
        let rpc = RpcHandler::start();
        let bank = rpc.working_bank();

        let non_existent_pubkey = Pubkey::new_unique();
        let pubkey = Pubkey::new_unique();
        let address = pubkey.to_string();
        let data = vec![1, 2, 3, 4, 5];
        let account = AccountSharedData::create_from_existing_shared_data(
            42,
            Arc::new(data.clone()),
            Pubkey::default(),
            false,
            0,
        );
        bank.store_account(&pubkey, &account);

        // Test 3 accounts, one empty, one non-existent, and one with data
        let request = create_test_request(
            "getMultipleAccounts",
            Some(json!([[
                rpc.mint_keypair.pubkey().to_string(),
                non_existent_pubkey.to_string(),
                address,
            ]])),
        );
        let result: RpcResponse<Value> = parse_success_result(rpc.handle_request_sync(request));
        let expected = json!([
            {
                "owner": "11111111111111111111111111111111",
                "lamports": TEST_MINT_LAMPORTS,
                "data": ["", "base64"],
                "executable": false,
                "rentEpoch": 0,
                "space": 0,
            },
            null,
            {
                "owner": "11111111111111111111111111111111",
                "lamports": 42,
                "data": [BASE64_STANDARD.encode(&data), "base64"],
                "executable": false,
                "rentEpoch": 0,
                "space": 5,
            }
        ]);
        assert_eq!(result.value, expected);
```

**File:** account-decoder/src/lib.rs (L34-44)
```rust
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
