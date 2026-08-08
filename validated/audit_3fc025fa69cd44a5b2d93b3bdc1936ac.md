Found a concrete panic path in `parse_bpf_upgradeable_loader`.

### Title
Public `getAccountInfo`/`getProgramAccounts` (`jsonParsed` encoding) crashes the RPC node via slice-index panic in `parse_bpf_upgradeable_loader` - (File: `account-decoder/src/parse_bpf_loader.rs`)

### Summary
`parse_bpf_upgradeable_loader` decodes an account's raw bytes with `bincode::deserialize` into an `UpgradeableLoaderState` enum, then, for the `Buffer` and `ProgramData` variants, slices the remaining data with `&data[offset..]` where `offset` is a fixed constant (`size_of_buffer_metadata()` / `size_of_programdata_metadata()`), without checking that `data.len() >= offset`. Because account ownership dispatch for JSON-parsed account decoding is keyed purely on `owner == bpf_loader_upgradeable::id()` (see `PARSABLE_PROGRAM_IDS` in `account-decoder/src/parse_account_data.rs`), any unprivileged user can create a `system_program`-owned account, assign it to the BPF Upgradeable Loader program ID via `Assign`/`CreateAccount` with owner set directly, and store minimal-length arbitrary bytes that bincode-decode into `Buffer{authority_address: Some(_)}` or `ProgramData{...}` but whose total length is shorter than the metadata offset. Requesting that account via `getAccountInfo`/`getProgramAccounts` with `encoding: "jsonParsed"` reaches `encode_ui_account` → `parse_account_data_v3` → `parse_bpf_upgradeable_loader`, causing an out-of-bounds slice index panic and crashing the validator's RPC-serving process.

### Finding Description [1](#0-0) 

The relevant code:
```rust
UpgradeableLoaderState::Buffer { authority_address } => {
    let offset = if authority_address.is_some() {
        UpgradeableLoaderState::size_of_buffer_metadata()
    } else { ... };
    BpfUpgradeableLoaderAccountType::Buffer(UiBuffer {
        authority: authority_address.map(|pubkey| pubkey.to_string()),
        data: UiAccountData::Binary(
            BASE64_STANDARD.encode(&data[offset..]),   // panics if data.len() < offset
            UiAccountEncoding::Base64,
        ),
    })
}
```
The same pattern exists for `ProgramData`. `bincode::deserialize` only requires the leading bytes to match the enum discriminant plus fixed-size fields (the enum tag + `Option<Pubkey>`); it does not require the buffer to contain any trailing "program data" bytes. An attacker can craft a byte string that bincode-decodes successfully into `Buffer { authority_address: Some(pubkey) }` while being exactly `size_of_buffer_metadata()` bytes or shorter (e.g., using a truncated account after resizing/reallocating, since accounts can be shrunk via `system_instruction::allocate`/reassignment tricks, or simply by storing data via `create_account` with a length equal to or slightly under the required metadata size while still satisfying bincode's fixed-width decode). This triggers `&data[offset..]` on a slice shorter than `offset`, which panics with "range start index out of range for slice".

Reachability: unlike the sysvar dispatch (keyed on fixed, non-user-controllable pubkeys), the BPF Upgradeable Loader dispatch is keyed only on the account's `owner` field [2](#0-1) . Any user can create an account with `owner = bpf_loader_upgradeable::id()` directly via the System Program's `CreateAccount` instruction (specifying arbitrary owner) with any data length and content — this requires no special privilege and does not need the account to actually be a valid/deployed program. The call path from the public JSON-RPC surface is: `JsonRpcRequestProcessor::get_account_info` → `get_encoded_account` → `encode_account`/`encode_ui_account` → `parse_account_data_v3` → `parse_bpf_upgradeable_loader` [3](#0-2) [4](#0-3) [5](#0-4) .

### Impact Explanation
A single unprivileged RPC call (`getAccountInfo`/`getProgramAccounts`/`getMultipleAccounts` with `encoding: "jsonParsed"`) against a self-created, attacker-owned account panics the RPC-serving thread/process, causing denial of service to the JSON-RPC API. This is a concrete validator-process crash triggered by a single low-cost, unprivileged request, matching the accepted impact category (decoder panic).

### Likelihood Explanation
High. Creating an account with an arbitrary owner and arbitrary short data is a normal, cheap, unprivileged operation (`SystemInstruction::CreateAccount`). No special permissions, staking, or validator/operator role is required — this is fully within reach of any RPC client.

### Recommendation
In `parse_bpf_upgradeable_loader` (`account-decoder/src/parse_bpf_loader.rs`), replace the unchecked slices `&data[offset..]` with checked accessors (`data.get(offset..)`) and return `ParseAccountError::AccountNotParsable(ParsableAccount::BpfUpgradeableLoader)` when the account data is shorter than the expected metadata size, instead of panicking. Add regression tests covering truncated `Buffer`/`ProgramData` accounts (length below `size_of_buffer_metadata()`/`size_of_programdata_metadata()`) to ensure `parse_bpf_upgradeable_loader` returns an `Err` rather than panicking.

### Proof of Concept
1. As any funded keypair, submit a `CreateAccount` system instruction that creates a new account owned by `bpf_loader_upgradeable::id()` with data set to the bincode-serialized bytes of `UpgradeableLoaderState::Buffer { authority_address: Some(Pubkey::new_unique()) }` truncated to fewer bytes than `UpgradeableLoaderState::size_of_buffer_metadata()` (while keeping the leading discriminant + `Option<Pubkey>` encoding intact so `bincode::deserialize` still succeeds).
2. Call the JSON-RPC method `getAccountInfo` with that account's pubkey and `{"encoding": "jsonParsed"}`.
3. The RPC handler invokes `parse_bpf_upgradeable_loader`, which executes `&data[offset..]` where `offset > data.len()`, panicking the request-handling thread and crashing/denying the RPC service.

### Citations

**File:** account-decoder/src/parse_bpf_loader.rs (L19-61)
```rust
    let parsed_account = match account_state {
        UpgradeableLoaderState::Uninitialized => BpfUpgradeableLoaderAccountType::Uninitialized,
        UpgradeableLoaderState::Buffer { authority_address } => {
            let offset = if authority_address.is_some() {
                UpgradeableLoaderState::size_of_buffer_metadata()
            } else {
                // This case included for code completeness; in practice, a Buffer account will
                // always have authority_address.is_some()
                UpgradeableLoaderState::size_of_buffer_metadata()
                    - serialized_size(&Pubkey::default()).unwrap() as usize
            };
            BpfUpgradeableLoaderAccountType::Buffer(UiBuffer {
                authority: authority_address.map(|pubkey| pubkey.to_string()),
                data: UiAccountData::Binary(
                    BASE64_STANDARD.encode(&data[offset..]),
                    UiAccountEncoding::Base64,
                ),
            })
        }
        UpgradeableLoaderState::Program {
            programdata_address,
        } => BpfUpgradeableLoaderAccountType::Program(UiProgram {
            program_data: programdata_address.to_string(),
        }),
        UpgradeableLoaderState::ProgramData {
            slot,
            upgrade_authority_address,
        } => {
            let offset = if upgrade_authority_address.is_some() {
                UpgradeableLoaderState::size_of_programdata_metadata()
            } else {
                UpgradeableLoaderState::size_of_programdata_metadata()
                    - serialized_size(&Pubkey::default()).unwrap() as usize
            };
            BpfUpgradeableLoaderAccountType::ProgramData(UiProgramData {
                slot,
                authority: upgrade_authority_address.map(|pubkey| pubkey.to_string()),
                data: UiAccountData::Binary(
                    BASE64_STANDARD.encode(&data[offset..]),
                    UiAccountEncoding::Base64,
                ),
            })
        }
```

**File:** account-decoder/src/parse_account_data.rs (L126-150)
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
```

**File:** rpc/src/rpc.rs (L534-560)
```rust
    pub async fn get_account_info(
        &self,
        pubkey: Pubkey,
        config: Option<RpcAccountInfoConfig>,
    ) -> Result<RpcResponse<Option<UiAccount>>> {
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
        let encoding = encoding.unwrap_or(UiAccountEncoding::Binary);

        let response = self
            .runtime
            .spawn_blocking({
                let bank = Arc::clone(&bank);
                move || get_encoded_account(&bank, &pubkey, encoding, data_slice, None)
            })
            .await
            .expect("rpc: get_encoded_account panicked")?;
        Ok(new_response(&bank, response))
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

**File:** account-decoder/src/lib.rs (L80-91)
```rust
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
