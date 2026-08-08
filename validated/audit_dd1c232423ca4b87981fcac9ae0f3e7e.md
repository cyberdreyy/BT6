### Title
`parse_account_data_v3` misreports Token-2022 extensions for accounts owned by the legacy `spl-token` program - (File: account-decoder/src/parse_account_data.rs)

### Summary
`parse_account_data_v3` maps both `ParsableAccount::SplToken` and `ParsableAccount::SplToken2022` to the exact same `parse_token_v3` call, without ever checking which program actually owns the account being decoded. Because `parse_token_v3` unconditionally tries `StateWithExtensions::<Account>::unpack`/`StateWithExtensions::<Mint>::unpack` (which understand Token-2022 TLV extensions), an account whose raw bytes are crafted to look like an extended Token-2022 account but whose `owner` field is the legacy `spl-token` program ID will still have fabricated extensions (e.g. `TransferHookProgramId`, `PermanentDelegate`) surfaced in `jsonParsed` output, even though the legacy program never implements or honors any extension mechanism.

### Finding Description
`parse_account_data_v3` selects behavior solely from `PARSABLE_PROGRAM_IDS`, which maps the legacy `spl_token_interface::id()` to `ParsableAccount::SplToken` and `spl_token_2022_interface::id()` to `ParsableAccount::SplToken2022` [1](#0-0) . Both variants are dispatched identically:

```
ParsableAccount::SplToken | ParsableAccount::SplToken2022 => serde_json::to_value(
    parse_token_v3(data, additional_data.spl_token_additional_data.as_ref())?,
)?,
``` [2](#0-1) 

`parse_token_v3` itself has no owner/program-id parameter at all — it only inspects raw `data` bytes via `StateWithExtensions::<Account>::unpack(data)` and, if that succeeds, unconditionally collects and serializes any extension TLV entries found (`account.get_extension_types()`), regardless of which program actually owns the account [3](#0-2) .

The Solana account-ownership model allows any program that currently owns an account to write arbitrary bytes to it and then reassign the `owner` field to a different program id in the same instruction (this is how `system_instruction::create_account` itself hands off freshly built accounts). An unprivileged attacker can therefore deploy their own BPF program that:
1. Creates/owns an account sized to hold `Account::LEN` (165) bytes plus a Token-2022-style TLV extension region.
2. Writes a byte-for-byte valid legacy `Account` header with `state = Initialized`, followed by a valid `AccountType::Account` discriminator byte and one or more valid extension TLV entries (e.g., `TransferHookProgramId`, `PermanentDelegate` — both are simple fixed-size Pod structs with no cryptographic binding to the owning program).
3. Reassigns the account's `owner` to the legacy `spl_token::id()`.

The resulting account is, from the legacy program's own perspective, permanently unusable (any legacy `spl-token` instruction unpacks with a strict `Account::LEN`-sized buffer and will reject the oversized data), but nothing prevents the account from existing on-chain with that owner and being queried. When queried via `getAccountInfo`/`getProgramAccounts` with `jsonParsed` encoding, the RPC path (`rpc/src/parsed_token_accounts.rs::get_parsed_token_account` → `encode_ui_account` → `parse_account_data_v3`) sees `owner == spl_token::id()`, selects `ParsableAccount::SplToken`, and calls the very same `parse_token_v3`, which happily reports the injected extensions as if they were legitimate Token-2022 extension state [4](#0-3) . No code path checks that the extensions found actually correspond to a program (`spl_token_2022_interface::id()`) that interprets them.

The `test_parse_account_data`/`test_token_parsing` tests only ever construct data whose program id matches the shape of the data intentionally, so this owner/data mismatch is untested [5](#0-4) .

### Impact Explanation
This is a decoder misreporting bug: RPC consumers (wallets, indexers, exchanges) that trust `jsonParsed` output for token accounts can be shown a `transferHook` program id, `permanentDelegate`, or other extension authority for an account that the actual owning (legacy) program neither recognizes nor enforces. Downstream integrators making trust decisions (e.g., "does this token have a transfer hook that can block/redirect transfers?") based on this field would be misled, matching the "misreporting program, authority, amount, decimals, or token owner to downstream integrators" bounty category referenced in the prompt.

### Likelihood Explanation
The attacker needs only standard, unprivileged capabilities: deploy a small BPF program (a normal, permissionless transaction), have it create/own an account, write crafted bytes, and reassign ownership to the legacy `spl_token::id()` — all in one transaction. No validator, leader, or staked-node control is required, and the malicious account persists on-chain to be queried repeatedly via a single low-rate `getAccountInfo` call. This is fully reproducible and deterministic.

### Recommendation
`parse_account_data_v3` (or `parse_token_v3`) should not treat `ParsableAccount::SplToken` and `ParsableAccount::SplToken2022` identically. Either:
- Pass the actual `program_id`/`ParsableAccount` variant into `parse_token_v3` and skip extension collection (`get_extension_types`/`parse_extension`) entirely when the owner is the legacy `spl_token_interface::id()`, only emitting the base `Account`/`Mint`/`Multisig` fields for that path; or
- Restrict the legacy path to `data.len() == Account::LEN`/`Mint::LEN`/`Multisig::LEN` and reject any data with a trailing extension region when parsed under `ParsableAccount::SplToken`.

### Proof of Concept
Rust unit test in `account-decoder/src/parse_account_data.rs` (or a new fuzz/invariant test) demonstrating the misreporting:

```rust
#[test]
fn test_legacy_owner_never_reports_extensions() {
    use spl_token_2022_interface::{
        extension::{
            transfer_hook::TransferHook, BaseStateWithExtensionsMut, ExtensionType,
            StateWithExtensionsMut,
        },
        state::Account as TokenAccount,
    };

    let account_size =
        ExtensionType::try_calculate_account_len::<TokenAccount>(&[ExtensionType::TransferHook])
            .unwrap();
    let mut account_data = vec![0; account_size];
    let mut account_state =
        StateWithExtensionsMut::<TokenAccount>::unpack_uninitialized(&mut account_data).unwrap();
    account_state.base = TokenAccount {
        state: spl_token_2022_interface::state::AccountState::Initialized,
        ..Default::default()
    };
    account_state.pack_base();
    account_state.init_account_type().unwrap();
    let hook = account_state.init_extension::<TransferHook>(true).unwrap();
    hook.program_id = Some(Pubkey::new_from_array([9; 32])).try_into().unwrap();

    // Simulate this data existing under the LEGACY spl-token program id.
    let parsed = parse_account_data_v3(
        &solana_pubkey::new_rand(),
        &spl_token_interface::id(), // legacy owner
        &account_data,
        Some(AccountAdditionalDataV3 {
            spl_token_additional_data: Some(SplTokenAdditionalDataV2::with_decimals(0)),
        }),
    )
    .unwrap();

    // INVARIANT: legacy-owned accounts must never surface extensions the
    // legacy program does not interpret.
    let extensions = parsed.parsed["info"]["extensions"].as_array().unwrap();
    assert!(
        extensions.is_empty(),
        "legacy spl-token account incorrectly reported extensions: {:?}",
        extensions
    );
}
```

Expected current behavior: the assertion fails — `parsed.parsed["info"]["extensions"]` contains a fabricated `transferHook` entry despite `program_id == spl_token_interface::id()`, confirming the misreporting.

### Citations

**File:** account-decoder/src/parse_account_data.rs (L37-41)
```rust
        m.insert(
            spl_token_2022_interface::id(),
            ParsableAccount::SplToken2022,
        );
        m.insert(spl_token_interface::id(), ParsableAccount::SplToken);
```

**File:** account-decoder/src/parse_account_data.rs (L145-147)
```rust
        ParsableAccount::SplToken | ParsableAccount::SplToken2022 => serde_json::to_value(
            parse_token_v3(data, additional_data.spl_token_additional_data.as_ref())?,
        )?,
```

**File:** account-decoder/src/parse_token.rs (L24-38)
```rust
pub fn parse_token_v3(
    data: &[u8],
    additional_data: Option<&SplTokenAdditionalDataV2>,
) -> Result<TokenAccountType, ParseAccountError> {
    if let Ok(account) = StateWithExtensions::<Account>::unpack(data) {
        let additional_data = additional_data.as_ref().ok_or_else(|| {
            ParseAccountError::AdditionalDataMissing(
                "no mint_decimals provided to parse spl-token account".to_string(),
            )
        })?;
        let extension_types = account.get_extension_types().unwrap_or_default();
        let ui_extensions = extension_types
            .iter()
            .map(|extension_type| parse_extension::<Account>(extension_type, &account))
            .collect();
```

**File:** rpc/src/parsed_token_accounts.rs (L23-50)
```rust
pub fn get_parsed_token_account(
    bank: &Bank,
    pubkey: &Pubkey,
    account: AccountSharedData,
    // only used for simulation results
    overwrite_accounts: Option<&HashMap<Pubkey, AccountSharedData>>,
) -> UiAccount {
    let additional_data = get_token_account_mint(account.data())
        .and_then(|mint_pubkey| {
            account_resolver::get_account_from_overwrites_or_bank(
                &mint_pubkey,
                bank,
                overwrite_accounts,
            )
        })
        .and_then(|mint_account| get_additional_mint_data(bank, mint_account.data()).ok())
        .map(|data| AccountAdditionalDataV3 {
            spl_token_additional_data: Some(data),
        });

    encode_ui_account(
        pubkey,
        &account,
        UiAccountEncoding::JsonParsed,
        additional_data,
        None,
    )
}
```

**File:** rpc/src/rpc.rs (L8503-8506)
```rust
    #[test_case(spl_token_interface::id(), None, None; "spl_token")]
    #[test_case(spl_token_2022_interface::id(), Some(InterestBearingConfig { pre_update_average_rate: 500.into(), current_rate: 500.into(),..Default::default() }), None; "spl_token_2022_with _interest")]
    #[test_case(spl_token_2022_interface::id(), None, Some(ScaledUiAmountConfig { new_multiplier: 2.0f64.into(), ..Default::default() }); "spl-token-2022 with multiplier")]
    fn test_token_parsing(
```
