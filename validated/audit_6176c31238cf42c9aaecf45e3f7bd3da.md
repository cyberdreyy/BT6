### Title
Unbounded response materialization in `getProgramAccounts`/`getFilteredProgramAccounts` with no response-size cap in Agave JSON-RPC - ([File: rpc/src/rpc.rs], [File: rpc/src/rpc_service.rs])

### Summary
Agave's JSON-RPC server (`rpc/src/rpc_service.rs`) only caps the size of the *incoming* request body via `max_request_body_size` (default 50 KB) [1](#0-0) [2](#0-1) . Unlike the reported Monad issue where a post-serialization size check exists but runs too late, Agave has **no response-size check at all** for RPC responses. A single small request that triggers an unbounded accounts scan (`getProgramAccounts` without a secondary index) can materialize an arbitrarily large in-memory result set and serialize it into the HTTP response, with no cap anywhere in the pipeline, unlike other memory-conscious paths in the same file which do enforce a byte budget.

### Finding Description
`get_filtered_program_accounts()` in `rpc/src/rpc.rs` has two code paths:
- When the `AccountIndex::ProgramId` secondary index is enabled, it calls `get_filtered_indexed_accounts()`, which is bounded by `scan_results_limit_bytes` and aborts the scan via `accumulate_and_check_scan_result_size`/`maybe_abort_scan` in `accounts-db/src/accounts.rs`.
- When the secondary index is **not** enabled (the common/default configuration, since `--account-index program-id` is opt-in), the code explicitly skips any byte limit: [3](#0-2) 

The comment states "this path does not need to provide a mb limit because we only want to support secondary indexes" — meaning any validator running with default settings and `--full-rpc-api` enabled, but without the `program-id` secondary index, exposes `getProgramAccounts` (and the SPL-token variants that fall back to the same function) with a completely unbounded scan of all accounts matching an owner, materialized fully into `Vec<(Pubkey, AccountSharedData)>` in memory [4](#0-3) .

After the scan, every matched account is base64/base58/JSON-encoded via `encode_account()`/`encode_ui_account()` [5](#0-4) , producing a full `Vec<RpcKeyedAccount>` that is then serialized by the underlying `jsonrpc_http_server`/`MetaIoHandler` into the HTTP response body. There is no `max_response_body_size` (or equivalent) configured anywhere in `rpc_service.rs`'s `ServerBuilder` chain — only `.max_request_body_size(max_request_body_size)` is set [6](#0-5) . This is strictly worse than the reported analog: the Monad report criticizes a size check that runs "too late" (post-serialization); here, for the unindexed `getProgramAccounts` path, no size check of the response exists at any point.

The request itself can be tiny (a single JSON-RPC call with just a program ID string), so the small-request-body cap of 50 KB provides no mitigation — the amplification happens entirely server-side during the account scan and encoding, not from request size.

### Impact Explanation
An unauthenticated RPC client can call `getProgramAccounts` for a program ID (e.g., a widely-used system/token program or any program with a large number of accounts) with a tiny request payload. The validator will scan and hold the entire matching account set (including full account data, potentially base64/JSON-encoded) in memory simultaneously, with no abort mechanism, no streaming, and no cap. On a validator with a large accounts-db and the `getProgramAccounts` unindexed path, this can exhaust process memory, causing the RPC service (and potentially the whole validator process, depending on OS memory pressure/OOM killer behavior) to crash or become unresponsive — a denial of service against RPC availability.

### Likelihood Explanation
Likelihood is high in realistic deployments: `getProgramAccounts` is a standard, widely used, unauthenticated RPC method; many RPC operators run `--full-rpc-api` without enabling the `program-id` secondary index (which itself has operational costs), making the unbounded path the default behavior for that endpoint. No special privileges, precondition races, or malicious snapshots are required — a single ordinary RPC call suffices.

### Recommendation
- Apply the same `scan_results_limit_bytes`/abort-on-exceed pattern used in `get_filtered_indexed_accounts()` and `accumulate_and_check_scan_result_size()` to the non-indexed path in `get_filtered_program_accounts()` (currently explicitly skipped), so that unindexed program-account scans also respect a configurable byte budget and abort early.
- Additionally, add a genuine response-size enforcement mechanism (streaming serialization with a byte budget, or a hard cap check before finalizing the HTTP response) at the RPC service layer in `rpc_service.rs`, so that large per-request or per-account-scan responses are rejected before or during serialization rather than never being bounded at all.
- Consider making the accounts-index byte limit mandatory by default rather than optional (`Option<usize>`), so operators cannot unknowingly run without any cap.

### Proof of Concept
1. Start a validator with `--full-rpc-api` and without `--account-index program-id`.
2. Deploy/target a program ID that owns a very large number of accounts (or use an existing high-account-count program on a live cluster, e.g., a token program with millions of associated accounts).
3. Send a single small JSON-RPC request: `{"jsonrpc":"2.0","id":1,"method":"getProgramAccounts","params":["<program_id>"]}`.
4. Observe that `get_filtered_program_accounts()` takes the `else` branch (no secondary index), calls `bank.get_filtered_program_accounts()` with no byte limit [7](#0-6) , loads and encodes every matching account, and attempts to serialize the full `Vec<RpcKeyedAccount>` into the HTTP response — with memory usage scaling linearly with the number/size of matching accounts and no cap applied anywhere in `rpc_service.rs`'s server configuration [8](#0-7) .

### Citations

**File:** rpc/src/rpc.rs (L142-143)
```rust
pub const MAX_REQUEST_BODY_SIZE: usize = 50 * (1 << 10); // 50kB
pub const PERFORMANCE_SAMPLES_LIMIT: usize = 720;
```

**File:** rpc/src/rpc.rs (L603-670)
```rust
    pub async fn get_program_accounts(
        &self,
        program_id: Pubkey,
        config: Option<RpcAccountInfoConfig>,
        mut filters: Vec<RpcFilterType>,
        with_context: bool,
        sort_results: bool,
    ) -> Result<OptionalContext<Vec<RpcKeyedAccount>>> {
        let RpcAccountInfoConfig {
            encoding,
            data_slice: data_slice_config,
            commitment,
            min_context_slot,
        } = config.unwrap_or_default();
        let bank = self.get_bank_with_config(RpcContextConfig {
            commitment,
            min_context_slot,
        })?;
        let encoding = encoding.unwrap_or(UiAccountEncoding::Binary);
        optimize_filters(&mut filters);
        let keyed_accounts = {
            if let Some(owner) = get_spl_token_owner_filter(&program_id, &filters)? {
                self.get_filtered_spl_token_accounts_by_owner(
                    Arc::clone(&bank),
                    program_id,
                    owner,
                    filters,
                    sort_results,
                )
                .await?
            } else if let Some(mint) = get_spl_token_mint_filter(&program_id, &filters)? {
                self.get_filtered_spl_token_accounts_by_mint(
                    Arc::clone(&bank),
                    program_id,
                    mint,
                    filters,
                    sort_results,
                )
                .await?
            } else {
                self.get_filtered_program_accounts(
                    Arc::clone(&bank),
                    program_id,
                    filters,
                    sort_results,
                )
                .await?
            }
        };
        let accounts = if is_known_spl_token_id(&program_id)
            && encoding == UiAccountEncoding::JsonParsed
        {
            get_parsed_token_accounts(Arc::clone(&bank), keyed_accounts.into_iter()).collect()
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
        Ok(match with_context {
            true => OptionalContext::Context(new_response(&bank, accounts)),
            false => OptionalContext::NoContext(accounts),
        })
```

**File:** rpc/src/rpc.rs (L2283-2307)
```rust
        } else {
            // this path does not need to provide a mb limit because we only want to support secondary indexes
            let mut accounts = self
                .runtime
                .spawn_blocking(move || {
                    bank.get_filtered_program_accounts(
                        &program_id,
                        |account: &AccountSharedData| {
                            filters
                                .iter()
                                .all(|filter_type| filter_allows(filter_type, account))
                        },
                    )
                    .map_err(|e| RpcCustomError::ScanError {
                        message: e.to_string(),
                    })
                })
                .await
                .expect("Failed to spawn blocking task")?;
            if sort_results {
                // Avoid copying pubkeys (using Ord::cmp(a, b) silences clippy::unnecessary_sort_by).
                accounts.sort_unstable_by(|(addr_a, _), (addr_b, _)| Ord::cmp(addr_a, addr_b));
            }
            Ok(accounts)
        }
```

**File:** rpc/src/rpc_service.rs (L665-668)
```rust
        let full_api = config.full_api;
        let max_request_body_size = config
            .max_request_body_size
            .unwrap_or(MAX_REQUEST_BODY_SIZE);
```

**File:** rpc/src/rpc_service.rs (L718-743)
```rust
                let request_middleware = RpcRequestMiddleware::new(
                    ledger_path,
                    snapshot_config,
                    bank_forks,
                    health.clone(),
                );
                let server = ServerBuilder::with_meta_extractor(
                    io,
                    move |req: &hyper::Request<hyper::Body>| {
                        let xbigtable = req.headers().get("x-bigtable");
                        if xbigtable.is_some_and(|v| v == "disabled") {
                            request_processor.clone_without_bigtable()
                        } else {
                            request_processor.clone()
                        }
                    },
                )
                .event_loop_executor(runtime.handle().clone())
                .threads(1)
                .cors(DomainsValidation::AllowOnly(vec![
                    AccessControlAllowOrigin::Any,
                ]))
                .cors_max_age(86400)
                .request_middleware(request_middleware)
                .max_request_body_size(max_request_body_size)
                .start_http(&rpc_addr);
```

**File:** account-decoder/src/lib.rs (L46-101)
```rust
pub fn encode_ui_account<T: ReadableAccount>(
    pubkey: &Pubkey,
    account: &T,
    encoding: UiAccountEncoding,
    additional_data: Option<AccountAdditionalDataV3>,
    data_slice_config: Option<UiDataSliceConfig>,
) -> UiAccount {
    let space = account.data().len();
    let data = match encoding {
        UiAccountEncoding::Binary => {
            let data = encode_bs58(account, data_slice_config);
            UiAccountData::LegacyBinary(data)
        }
        UiAccountEncoding::Base58 => {
            let data = encode_bs58(account, data_slice_config);
            UiAccountData::Binary(data, encoding)
        }
        UiAccountEncoding::Base64 => UiAccountData::Binary(
            BASE64_STANDARD.encode(slice_data(account.data(), data_slice_config)),
            encoding,
        ),
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
    };
    UiAccount {
        lamports: account.lamports(),
        data,
        owner: account.owner().to_string(),
        executable: account.executable(),
        rent_epoch: account.rent_epoch(),
        space: Some(space as u64),
    }
}
```
