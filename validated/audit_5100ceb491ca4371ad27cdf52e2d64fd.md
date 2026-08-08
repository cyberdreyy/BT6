### Title
`getAccountInfo`/`getProgramAccounts` with `jsonParsed` encoding can be forced into unbounded-cost JSON serialization via attacker-controlled SPL Token‑2022 `TokenMetadata` extension data - (File: `account-decoder/src/parse_token_extension.rs`, `account-decoder/src/lib.rs`, `rpc/src/rpc.rs`)

### Summary
Any unprivileged user can create an SPL Token‑2022 mint with the `TokenMetadata` extension and grow its `name`, `symbol`, `uri`, and especially the `additional_metadata: Vec<(String, String)>` map up to the maximum permitted account size (10 MiB, `MAX_PERMITTED_DATA_LENGTH` in `program-runtime/src/serialization.rs`). Because these fields are variable-length and unbounded within that ceiling, every subsequent unprivileged RPC caller who requests this account with `encoding: "jsonParsed"` (via `getAccountInfo`, `getMultipleAccounts`, or `getProgramAccounts`) forces the validator's JSON-RPC node to decode and re-serialize the full multi-megabyte string data on every single call. This is the direct analog of the Hats Protocol `uri()` DOS: an attacker pays a one-time (rent) cost to inflate a string field, then every future unprivileged reader/caller pays a disproportionately large decode+serialize cost.

### Finding Description
`encode_ui_account` in `account-decoder/src/lib.rs` handles the `UiAccountEncoding::JsonParsed` case by calling `parse_account_data_v3`, which for SPL Token/Token-2022 accounts calls `parse_token_v3`. [1](#0-0) 

`parse_token_v3` unpacks all extensions present on the mint/account and converts each into its `Ui*` representation for JSON serialization: [2](#0-1) 

For the `TokenMetadata` extension specifically, the code calls `get_variable_len_extension::<TokenMetadata>()` with no length checks, and `convert_token_metadata` copies the raw `name`, `symbol`, `uri`, and `additional_metadata` `Vec<(String, String)>` directly into the `UiTokenMetadata` struct that gets JSON-serialized: [3](#0-2) [4](#0-3) 

`UiTokenMetadata` itself places no upper bound on any of these fields: [5](#0-4) 

Unlike the `Base58`/`Binary` encoding path, which explicitly caps size via `MAX_BASE58_BYTES` and returns an error string instead of encoding oversized data: [6](#0-5) [7](#0-6) 

...the `JsonParsed` path has no equivalent size guard. `encode_account` in `rpc/src/rpc.rs`, which is used by `getAccountInfo`, `getMultipleAccounts`, and `getProgramAccounts`, only enforces the `MAX_BASE58_BYTES` limit for `Binary`/`Base58` encodings and falls through to unconditionally call `encode_ui_account(..., JsonParsed, ...)` for the JSON-parsed path.

Because the underlying account data can be as large as the network-wide `MAX_PERMITTED_DATA_LENGTH` (10 MiB) enforced in `program-runtime/src/serialization.rs`, and the `TokenMetadata` extension's `additional_metadata` map can be repeatedly extended by the mint's own update authority (an ordinary, unprivileged user) via `UpdateField`/reallocation instructions on the Token-2022 program, an attacker can grow this metadata to multiple megabytes at a modest, one-time rent cost. From that point on, every unprivileged RPC caller who requests `jsonParsed` encoding for that account triggers full parsing and JSON string allocation/serialization of the bloated metadata on the validator's RPC node — a single, otherwise innocuous read request imposing disproportionate CPU/memory cost, directly mirroring the Hats `uri()` gas-DOS pattern where a cheaply-inflated string field imposes unbounded cost on every future reader.

### Impact Explanation
A single unprivileged JSON-RPC call (`getAccountInfo`, `getMultipleAccounts`, or `getProgramAccounts` with `encoding=jsonParsed`) against an attacker-crafted Token-2022 mint account can force the RPC node to parse and serialize up to ~10 MiB of string data, per call, with no size guard analogous to the one already present for `Base58`/`Binary` encoding. This is an unbounded-cost-for-a-single-low-rate-call condition against a validator's RPC surface, consistent with the accepted class "unbounded cost for a single low-rate call."

### Likelihood Explanation
The setup cost is a one-time rent payment to grow the mint account and extension data (paid once by the attacker, similar in spirit to the ~$92 setup cost described in the source report), after which the account remains attacker-controlled/inflated indefinitely unless closed. Any RPC operator that serves `jsonParsed`-encoded `getAccountInfo`/`getProgramAccounts` requests for token accounts (a very common feature used by wallets/indexers) is affected once such an account exists on-chain.

### Recommendation
Add a size guard to the `JsonParsed` path in `encode_ui_account` (`account-decoder/src/lib.rs`) and/or `parse_token_v3`/`parse_extension` (`account-decoder/src/parse_token.rs`, `account-decoder/src/parse_token_extension.rs`) analogous to the existing `MAX_BASE58_BYTES` check: if the account data (or the `TokenMetadata` extension payload specifically) exceeds a reasonable threshold, fall back to `Base64` binary encoding instead of parsing/serializing the full JSON representation, exactly as is already done when `parse_account_data_v3` fails to parse.

### Proof of Concept
1. Using the SPL Token-2022 program, create a mint with the `MetadataPointer` extension pointing at itself and initialize the `TokenMetadata` extension (`spl_token_metadata_interface::instruction::initialize`), as exercised in [8](#0-7) .
2. Repeatedly call `UpdateField` (`spl_token_metadata_interface::instruction::update_field`) to append/extend `additional_metadata` key-value pairs, reallocating the mint account up toward `MAX_PERMITTED_DATA_LENGTH` (10 MiB).
3. Call `getAccountInfo` (or `getProgramAccounts`) against this mint with `{"encoding": "jsonParsed"}`, which routes through `encode_account` (`rpc/src/rpc.rs`) → `encode_ui_account` → `parse_account_data_v3` → `parse_token_v3` → `parse_extension`/`convert_token_metadata` (`account-decoder/src/parse_token.rs`, `account-decoder/src/parse_token_extension.rs`), forcing full decode and JSON serialization of the multi-megabyte metadata on every call, with no length cap comparable to `MAX_BASE58_BYTES`.

### Citations

**File:** account-decoder/src/lib.rs (L32-44)
```rust
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

**File:** account-decoder/src/parse_token.rs (L70-76)
```rust
    if let Ok(mint) = StateWithExtensions::<Mint>::unpack(data) {
        let extension_types = mint.get_extension_types().unwrap_or_default();
        let ui_extensions = extension_types
            .iter()
            .map(|extension_type| parse_extension::<Mint>(extension_type, &mint))
            .collect();
        return Ok(TokenAccountType::Mint(UiMint {
```

**File:** account-decoder/src/parse_token_extension.rs (L108-111)
```rust
        ExtensionType::TokenMetadata => account
            .get_variable_len_extension::<TokenMetadata>()
            .map(|extension| UiExtension::TokenMetadata(convert_token_metadata(extension)))
            .unwrap_or(UiExtension::UnparseableExtension),
```

**File:** account-decoder/src/parse_token_extension.rs (L342-352)
```rust
fn convert_token_metadata(token_metadata: TokenMetadata) -> UiTokenMetadata {
    let update_authority: Option<Pubkey> = token_metadata.update_authority.into();
    UiTokenMetadata {
        update_authority: update_authority.map(|pubkey| pubkey.to_string()),
        mint: token_metadata.mint.to_string(),
        name: token_metadata.name,
        symbol: token_metadata.symbol,
        uri: token_metadata.uri,
        additional_metadata: token_metadata.additional_metadata,
    }
}
```

**File:** account-decoder-client-types/src/token.rs (L285-294)
```rust
#[derive(Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct UiTokenMetadata {
    pub update_authority: Option<String>,
    pub mint: String,
    pub name: String,
    pub symbol: String,
    pub uri: String,
    pub additional_metadata: Vec<(String, String)>,
}
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

**File:** transaction-status/src/parse_token/extension/token_metadata.rs (L20-42)
```rust
pub(in crate::parse_token) fn parse_token_metadata_instruction(
    instruction: &TokenMetadataInstruction,
    account_indexes: &[u8],
    account_keys: &AccountKeys,
) -> Result<ParsedInstructionEnum, ParseInstructionError> {
    match instruction {
        TokenMetadataInstruction::Initialize(metadata) => {
            check_num_token_accounts(account_indexes, 4)?;
            let Initialize { name, symbol, uri } = metadata;
            let value = json!({
                "metadata": account_keys[account_indexes[0] as usize].to_string(),
                "updateAuthority": account_keys[account_indexes[1] as usize].to_string(),
                "mint": account_keys[account_indexes[2] as usize].to_string(),
                "mintAuthority": account_keys[account_indexes[3] as usize].to_string(),
                "name": name,
                "symbol": symbol,
                "uri": uri,
            });
            Ok(ParsedInstructionEnum {
                instruction_type: "initializeTokenMetadata".to_string(),
                info: value,
            })
        }
```
