### Title
`simulateTransaction`'s `accounts.addresses` length check allows duplicate-pubkey amplification of `get_encoded_account`/`parse_account_data_v3` cost within a single RPC call - ([File: rpc/src/rpc.rs])

### Summary
The `simulate_transaction` RPC handler bounds `config_accounts.addresses.len()` only by `number_of_accounts` (the count of the transaction's account keys), but performs no deduplication of the supplied addresses. An attacker can repeat the same pubkey up to `number_of_accounts` times in `accounts.addresses`, causing `get_encoded_account` → `get_account_from_overwrites_or_bank` → `encode_ui_account`/`parse_account_data_v3` to be invoked once per duplicate on the same large account, multiplying the per-account decode/encode cost by the list length in a single RPC call.

### Finding Description
In `simulate_transaction`, after building `post_simulation_accounts_map`, the code maps every entry of `config_accounts.addresses` independently to `get_encoded_account`, with no uniqueness constraint on the addresses: [1](#0-0) 

The only guard present is a length check against `number_of_accounts`, which is derived from the sanitized transaction's `account_keys()` length: [2](#0-1) 

`get_encoded_account` resolves each address via `get_account_from_overwrites_or_bank`, which does a `HashMap` lookup/clone of the (possibly attacker-crafted, post-simulation) account and then either calls `get_parsed_token_account` (for SPL-token owners) or `encode_account`/`encode_ui_account`: [3](#0-2) [4](#0-3) 

`encode_ui_account` performs O(account.data().len()) work per call regardless of encoding (base64 encode, zstd compression, or `parse_account_data_v3` for jsonParsed): [5](#0-4) [6](#0-5) 

Since `overwrite_accounts` is populated from `post_simulation_accounts`, an attacker's own transaction can leave a large account (e.g., a BPF Loader Upgradeable program-data/buffer account, sized up to the max permitted account data length) in the post-simulation state, then reference that single pubkey N times in `accounts.addresses` (N bounded by `number_of_accounts`, i.e., by `MAX_TX_ACCOUNT_LOCKS`). The length check `config_accounts.addresses.len() > number_of_accounts` does not require the addresses to be distinct, so the same expensive account gets encoded/parsed N times per call.

### Impact Explanation
This is a single-call CPU/memory amplification: cost of one `simulateTransaction` call scales with `(number of duplicate entries) × (size of the referenced account)`, rather than being bounded purely by explicit per-call limits. Given `number_of_accounts` can reach `MAX_TX_ACCOUNT_LOCKS` and account size can reach the maximum permitted account data length, a single call can force many multiples of a large-account encode/decode, well beyond the cost a caller would expect from a request with a small number of `addresses`. This falls into the "RPC DoS via single low-rate call, unbounded cost" bounty category.

### Likelihood Explanation
Feasible with a single unprivileged client and a single JSON-RPC call: the attacker crafts a transaction that leaves a large account in `post_simulation_accounts` (e.g., via a builtin/system instruction that writes to/creates a large account, or references an existing large program-data account) and submits `simulateTransaction` with `accounts.addresses` = `[same_pubkey; N]`, `encoding: "jsonParsed"` (or `base64`). No sig-verify, staking, or multi-call requirement is needed; the check `addresses.len() > number_of_accounts` is satisfied by construction. Repeatable at up to one call per `CLUSTER_SLOT_TIME_TARGET / 2`.

### Recommendation
Deduplicate `config_accounts.addresses` before iterating (e.g., collect into a `HashSet`/preserve-order-dedup and only decode each distinct pubkey once, then fan back out to duplicated positions in the response), or bound total decode/encode work by summing account sizes across the (deduplicated) address list against a fixed limit rather than only checking list length against `number_of_accounts`.

### Proof of Concept
Integration test plan (in `rpc/src/rpc.rs` test module, alongside `test_rpc_simulate_transaction`):
1. Build a transaction whose post-simulation state leaves one large account (close to `MAX_PERMITTED_DATA_LENGTH`) at a known pubkey (e.g., via a builtin instruction such as `create_account` with maximal space, or an existing large program-data account).
2. Ensure the transaction's account key count (`number_of_accounts`) is as large as `MAX_TX_ACCOUNT_LOCKS` allows (e.g., by including many distinct read-only accounts).
3. Send one `simulateTransaction` call with `accounts.addresses` = `[large_account_pubkey; number_of_accounts]`, `encoding: "jsonParsed"`.
4. Measure wall-clock time / CPU of the handler call as a function of `N` (number of duplicates) versus a control call with `addresses.len() == 1`.
5. Assert: current code shows time scaling ~linearly with `N` (fails to be bounded); a fixed version should show O(1) cost after deduplication (assert total handler time stays within a small constant factor of the single-address case regardless of `N`).

### Citations

**File:** rpc/src/rpc.rs (L2552-2573)
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
```

**File:** rpc/src/rpc.rs (L4089-4133)
```rust
            let account_keys = transaction.message().account_keys();
            let number_of_accounts = account_keys.len();

            let accounts = if let Some(config_accounts) = config_accounts {
                let accounts_encoding = config_accounts
                    .encoding
                    .unwrap_or(UiAccountEncoding::Base64);

                if accounts_encoding == UiAccountEncoding::Binary
                    || accounts_encoding == UiAccountEncoding::Base58
                {
                    return Err(Error::invalid_params("base58 encoding not supported"));
                }

                if config_accounts.addresses.len() > number_of_accounts {
                    return Err(Error::invalid_params(format!(
                        "Too many accounts provided; max {number_of_accounts}"
                    )));
                }

                if result.is_err() {
                    Some(vec![None; config_accounts.addresses.len()])
                } else {
                    let mut post_simulation_accounts_map = HashMap::new();
                    for (pubkey, data) in post_simulation_accounts {
                        post_simulation_accounts_map.insert(pubkey, data);
                    }

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

**File:** account-decoder/src/parse_account_data.rs (L126-157)
```rust
pub fn parse_account_data_v3(
    pubkey: &Pubkey,
    program_id: &Pubkey,
    data: &[u8],
    additional_data: Option<AccountAdditionalDataV3>,
) -> Result<ParsedAccount, ParseAccountError> {
    let program_name = PARSABLE_PROGRAM_IDS
        .get(program_id)
        .ok_or(ParseAccountError::ProgramNotParsable)?;
    let additional_data = additional_data.unwrap_or_default();
    let parsed_json = match program_name {
        ParsableAccount::AddressLookupTable => {
            serde_json::to_value(parse_address_lookup_table(data)?)?
        }
        ParsableAccount::BpfUpgradeableLoader => {
            serde_json::to_value(parse_bpf_upgradeable_loader(data)?)?
        }
        ParsableAccount::Config => serde_json::to_value(parse_config(data, pubkey)?)?,
        ParsableAccount::Nonce => serde_json::to_value(parse_nonce(data)?)?,
        ParsableAccount::SplToken | ParsableAccount::SplToken2022 => serde_json::to_value(
            parse_token_v3(data, additional_data.spl_token_additional_data.as_ref())?,
        )?,
        ParsableAccount::Stake => serde_json::to_value(parse_stake(data)?)?,
        ParsableAccount::Sysvar => serde_json::to_value(parse_sysvar(data, pubkey)?)?,
        ParsableAccount::Vote => serde_json::to_value(parse_vote(data, pubkey)?)?,
    };
    Ok(ParsedAccount {
        program: format!("{program_name:?}").to_kebab_case(),
        parsed: parsed_json,
        space: data.len() as u64,
    })
}
```
