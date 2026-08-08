### Title
Extension data unconditionally parsed from mint account bytes without verifying owner is a token-2022 program - `get_additional_mint_data` misreports extension config for legacy `spl_token`-owned mints - ([File: rpc/src/parsed_token_accounts.rs])

### Summary
`get_mint_owner_and_additional_data` and `get_additional_mint_data` call `StateWithExtensions::<Mint>::unpack(data)` on any mint account's raw bytes without first checking that `mint_account.owner()` is actually a token-2022-compatible program id. Because `StateWithExtensions::unpack` only inspects byte layout (base `Mint` struct + optional TLV tail) and never checks the account's on-chain `owner` field, an attacker-controlled account that is owned by legacy `spl_token` but has crafted trailing TLV bytes will have `interest_bearing_config`/`scaled_ui_amount_config` populated in the returned `SplTokenAdditionalDataV2`, even though the legacy `spl_token` program neither writes nor honors any bytes beyond the 82-byte `Mint` struct.

### Finding Description
- `get_mint_owner_and_additional_data` (rpc/src/parsed_token_accounts.rs:92-108) fetches the mint account and calls `get_additional_mint_data(bank, mint_account.data())` unconditionally, then returns `(*mint_account.owner(), mint_data)`.
- `get_additional_mint_data` (rpc/src/parsed_token_accounts.rs:110-130) calls `StateWithExtensions::<Mint>::unpack(data)` and then `mint.get_extension::<InterestBearingConfig>()` / `get_extension::<ScaledUiAmountConfig>()`. Neither of these steps consults `mint_account.owner()`; they operate purely on the byte contents of the account data, matching on a TLV discriminator byte that could exist in the account regardless of which program actually owns/writes the account.
- `get_parsed_token_account` (same file, lines 30-41) similarly calls `get_additional_mint_data` on whatever mint account is referenced by a token account's mint field, without checking that the mint's owner is a token-2022 program consistent with the token account's own owning program.
- The returned `mint_data` (containing `decimals` and any parsed extension configs) is fed into `AccountAdditionalDataV3`/`encode_ui_account`, which is exposed via `jsonParsed` encoding in RPC calls such as `getAccountInfo`/`getTokenAccountsByOwner`.
- Root cause: the code assumes any account whose bytes unpack as a `Mint` (optionally with TLV tail) is a legitimate token-2022 mint, but never checks that the account's owner matches a recognized token-2022 program id (nor that it matches the referencing token account's own owner). A legacy `spl_token`-owned mint account is guaranteed by the real `spl_token` program to only interpret/write the first 82 bytes; anything after that is inert as far as the runtime program is concerned, but the RPC parsing code will still interpret it as valid extension TLV data if the byte pattern happens to match.

### Impact Explanation
This falls under a decoder/parsing misreporting bug (wrong account data returned to an RPC client) rather than a consensus or crash issue. A single `getAccountInfo`/`getTokenAccountsByOwner` call with `jsonParsed` encoding against an attacker-planted mint (owned by legacy `spl_token`, with crafted trailing bytes) can return `interest_bearing_config`/`scaled_ui_amount_config` extension fields and a UI amount computed as if the mint were a real interest-bearing/scaled-ui token-2022 mint, even though the mint's true owning program (`spl_token`) does not support or enforce any such interest/scaling logic. This is a "wrong data returned" misreporting bug in the RPC layer, matching the "decoder misreporting" category referenced in the question scope.

### Likelihood Explanation
Feasibility is limited by how an attacker can get owner-mismatched trailing bytes onto an account owned by `spl_token`. `SystemInstruction::CreateAccount` zero-initializes account data, and legacy `spl_token`'s `InitializeMint` only ever writes the first 82 bytes, never clearing or validating bytes beyond that. For an attacker to place non-zero, TLV-shaped bytes after byte 82 on an account ultimately owned by `spl_token`, they would need a way to write those bytes while they still control ownership (e.g., via a custom on-chain program they deploy that writes arbitrary data into an account it owns and then transfers ownership to `spl_token_interface::id()`), since Solana's runtime only permits the currently-owning program to change an account's owner. This is achievable by any unprivileged user deploying and invoking their own program — no validator/leader/staked-node privileges are required — making the precondition realistic, though it requires deploying a custom program rather than using stock SPL Token instructions alone.

### Recommendation
In `get_additional_mint_data`/`get_mint_owner_and_additional_data`, verify `mint_account.owner()` equals a recognized token-2022 program id (and, in `get_parsed_token_account`, that it matches the referencing token account's own owner) before invoking `StateWithExtensions::<Mint>::unpack` for extension parsing. If the mint is owned by legacy `spl_token`, skip extension parsing entirely and only report `decimals` from the base `Mint` struct with `interest_bearing_config`/`scaled_ui_amount_config` set to `None`.

### Proof of Concept
Rust unit test plan for `rpc/src/parsed_token_accounts.rs`:
```rust
#[test]
fn test_get_additional_mint_data_ignores_extensions_for_legacy_spl_token_owner() {
    // Construct a valid base Mint (82 bytes) as spl_token_interface::state::Mint
    // packed at offset 0, followed by a fabricated TLV tail:
    // [AccountType::Mint tag byte][ExtensionType::InterestBearingConfig][len][InterestBearingConfig bytes]
    let mut data = vec![0u8; spl_token_interface::state::Mint::LEN];
    // ... pack a valid base Mint into `data[0..82]` ...
    data.push(/* AccountType::Mint as u8 */ 1);
    // append a well-formed InterestBearingConfig TLV entry mimicking token-2022 layout
    data.extend_from_slice(&fabricated_interest_bearing_tlv_bytes());

    // Simulate: account is owned by legacy spl_token_interface::id(), NOT spl_token_2022.
    let mint_account = AccountSharedData::new_data(
        /* lamports */ 1_000_000,
        &(),
        &spl_token_interface::id(),
    ).unwrap(); // (adjust to directly set .data() = data, .set_owner(spl_token_interface::id()))

    let result = get_additional_mint_data(&bank, &data).unwrap();

    // Expected (fix): since owner is spl_token (legacy), interest_bearing_config must be None.
    assert!(result.interest_bearing_config.is_none());
    assert!(result.scaled_ui_amount_config.is_none());

    // Current (buggy) behavior: this assertion fails today because
    // get_additional_mint_data ignores mint_account.owner() and parses the
    // TLV tail unconditionally, populating interest_bearing_config from
    // attacker-controlled trailing bytes on a legacy spl_token mint.
}
```
This test should be run against `get_additional_mint_data` (and ideally an end-to-end variant calling `get_mint_owner_and_additional_data`/`get_parsed_token_account` with a full `Bank`) to confirm that today's implementation returns non-`None` extension configs for an account whose `owner()` is `spl_token_interface::id()`, demonstrating the misreporting described.