### Title
JSON-RPC `jsonParsed` token-account decoding trusts an attacker-controlled "mint" address embedded in account data without verifying its owner, causing misreported token balances - ([File: rpc/src/parsed_token_accounts.rs])

### Summary
The Sablier report's bug class is: a contract accepts an address supplied by an untrusted/semi-trusted party and treats it as authoritative (a legitimate Lockup contract) without validating that it actually is one, leading to funds being drained or wrong behavior. The Agave analog is in the `jsonParsed` token-account decoding path used by unprivileged JSON-RPC callers: the "mint" pubkey is read directly out of attacker-controlled account bytes and then used to fetch "additional data" (decimals, extension configs) from whatever account happens to sit at that address — without ever checking that this "mint" account is actually owned by an SPL Token/Token-2022 program. This lets an unprivileged actor make the RPC report incorrect (attacker-chosen) decimals/UI amounts for a real token account.

### Finding Description
`get_token_account_mint` extracts a pubkey from the first 32 bytes of an account's raw data, only checking that the data blob is at least structurally shaped like a token `Account` layout via `GenericTokenAccount::valid_account_data` — it performs no relationship check to the mint's owner program: [1](#0-0) 

That pubkey is then used by `get_parsed_token_account`/`get_parsed_token_accounts` to look up whatever account currently lives at that address in the bank, and to derive `SplTokenAdditionalDataV2` (decimals, interest-bearing/scaled-UI configs) from its data: [2](#0-1) [3](#0-2) 

`get_additional_mint_data` — the function that actually reads those decimals/extension values — never validates that the referenced account is owned by a legitimate SPL Token or Token-2022 program. It only attempts a structural `StateWithExtensions::<Mint>::unpack`: [4](#0-3) 

`get_mint_owner_and_additional_data` similarly returns `*mint_account.owner()` verbatim as the "token program id" for a caller-specified mint, with no check that it equals a known SPL token program: [5](#0-4) 

This mirrors the Sablier issue exactly: an address (there, the Lockup contract; here, the "mint" pubkey embedded in account data) is accepted and treated as authoritative for a security/format-relevant computation (decimals used to scale raw amounts into `uiAmount`) without validating that the referenced entity is actually of the expected/trusted type (a real SPL Token/Token-2022 mint). Anyone can create/own an arbitrary account (owned by any program, including their own) whose 82-byte-compatible layout satisfies `Mint::unpack`, set an arbitrary `decimals` field in it, and then create a real SPL-Token-owned token `Account` whose `mint` field points at that spoofed account.

### Impact Explanation
This is reachable purely through unprivileged JSON-RPC calls (`getAccountInfo`/`getTokenAccountsByOwner`/`getTokenAccountsByDelegate` with `encoding: jsonParsed`), which internally call `get_parsed_token_account(s)` in `rpc/src/parsed_token_accounts.rs`. It causes the RPC to return wrong-value account data: the parsed `tokenAmount.uiAmount`, `uiAmountString`, and `decimals` fields will reflect attacker-chosen values rather than the true mint's decimals, misleading any client, wallet, or exchange that relies on `jsonParsed` output rather than raw amounts. This falls into the accepted "wrong ... account data returned" / decoder misreporting category, since a single unprivileged query produces incorrect data without any consensus-state mutation or crash.

### Likelihood Explanation
High feasibility: an attacker needs only to create two ordinary accounts under the standard system/token program (or an unrelated program) with no special privileges — one crafted to structurally look like a Mint (satisfying `Mint::unpack`) with a chosen `decimals`, and a genuine SPL token Account whose `mint` field points at it. No governance/validator/operator role or special timing is required; the exploit is triggerable in a single RPC call.

### Recommendation
In `get_additional_mint_data` and `get_mint_owner_and_additional_data` (`rpc/src/parsed_token_accounts.rs`), verify that `mint_account.owner()` is one of the known SPL Token program ids (`spl_token_interface::id()`/Token-2022 id, as done via `spl_token_ids()`/`is_known_spl_token_id` elsewhere in the codebase — see `account-decoder/src/parse_token.rs`) before trusting its data as mint metadata; if the owner check fails, fall back to a safe default (e.g., omit additional data / treat decimals as unknown) rather than silently accepting attacker-chosen values.

### Proof of Concept
1. Create account `M` owned by an arbitrary program (or even the System Program), with data laid out to satisfy `StateWithExtensions::<Mint>::unpack` (82-byte SPL Mint layout), setting `decimals = 0` (or any attacker-chosen value) while the "genuine" ecosystem mint uses `decimals = 6`.
2. Create a real SPL Token `Account` `T`, correctly owned by `spl_token::id()`, with its `mint` field set to `M`'s pubkey and a nonzero `amount` (e.g., raw amount `1_000_000`).
3. Call `getAccountInfo` (or `getTokenAccountsByOwner`) for `T` with `{"encoding": "jsonParsed"}`.
4. Observe that `parsed.info.tokenAmount.decimals` and `uiAmount`/`uiAmountString` are computed from `M`'s attacker-controlled `decimals` field (e.g., reporting `1000000` tokens instead of the true `1.0`), even though `M` is not a legitimate token-program-owned mint — confirming the RPC misreports data based on an unvalidated, attacker-supplied "mint" address, mirroring the root cause described in the Sablier report (blind trust of a supplied contract/account address without validating it is of the expected trusted type).

### Citations

**File:** account-decoder/src/parse_token.rs (L166-170)
```rust
pub fn get_token_account_mint(data: &[u8]) -> Option<Pubkey> {
    Account::valid_account_data(data)
        .then(|| Pubkey::try_from(data.get(..32)?).ok())
        .flatten()
}
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

**File:** rpc/src/parsed_token_accounts.rs (L52-88)
```rust
pub fn get_parsed_token_accounts<I>(
    bank: Arc<Bank>,
    keyed_accounts: I,
) -> impl Iterator<Item = RpcKeyedAccount>
where
    I: Iterator<Item = (Pubkey, AccountSharedData)>,
{
    let mut mint_data: HashMap<Pubkey, AccountAdditionalDataV3> = HashMap::new();
    keyed_accounts.filter_map(move |(pubkey, account)| {
        let additional_data = get_token_account_mint(account.data()).and_then(|mint_pubkey| {
            mint_data.get(&mint_pubkey).cloned().or_else(|| {
                let (_, data) = get_mint_owner_and_additional_data(&bank, &mint_pubkey).ok()?;
                let data = AccountAdditionalDataV3 {
                    spl_token_additional_data: Some(data),
                };
                mint_data.insert(mint_pubkey, data);
                Some(data)
            })
        });

        let maybe_encoded_account = encode_ui_account(
            &pubkey,
            &account,
            UiAccountEncoding::JsonParsed,
            additional_data,
            None,
        );
        if let UiAccountData::Json(_) = &maybe_encoded_account.data {
            Some(RpcKeyedAccount {
                pubkey: pubkey.to_string(),
                account: maybe_encoded_account,
            })
        } else {
            None
        }
    })
}
```

**File:** rpc/src/parsed_token_accounts.rs (L92-108)
```rust
pub(crate) fn get_mint_owner_and_additional_data(
    bank: &Bank,
    mint: &Pubkey,
) -> Result<(Pubkey, SplTokenAdditionalDataV2)> {
    if mint == &spl_token_interface::native_mint::id() {
        Ok((
            spl_token_interface::id(),
            SplTokenAdditionalDataV2::with_decimals(spl_token_interface::native_mint::DECIMALS),
        ))
    } else {
        let mint_account = bank.get_account(mint).ok_or_else(|| {
            Error::invalid_params("Invalid param: could not find mint".to_string())
        })?;
        let mint_data = get_additional_mint_data(bank, mint_account.data())?;
        Ok((*mint_account.owner(), mint_data))
    }
}
```

**File:** rpc/src/parsed_token_accounts.rs (L110-130)
```rust
fn get_additional_mint_data(bank: &Bank, data: &[u8]) -> Result<SplTokenAdditionalDataV2> {
    StateWithExtensions::<Mint>::unpack(data)
        .map_err(|_| {
            Error::invalid_params("Invalid param: Token mint could not be unpacked".to_string())
        })
        .map(|mint| {
            let interest_bearing_config = mint
                .get_extension::<InterestBearingConfig>()
                .map(|x| (*x, bank.clock().unix_timestamp))
                .ok();
            let scaled_ui_amount_config = mint
                .get_extension::<ScaledUiAmountConfig>()
                .map(|x| (*x, bank.clock().unix_timestamp))
                .ok();
            SplTokenAdditionalDataV2 {
                decimals: mint.base.decimals,
                interest_bearing_config,
                scaled_ui_amount_config,
            }
        })
}
```
