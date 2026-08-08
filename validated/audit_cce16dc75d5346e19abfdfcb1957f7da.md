### Title
`parse_token_v3` misclassifies a structurally-corrupted SPL-Token `Account`/`Mint` as a `Multisig` when its raw length collides with `Multisig::get_packed_len()` - ([File: account-decoder/src/parse_token.rs])

### Summary
`parse_token_v3` only distinguishes `Account`/`Mint` from `Multisig` by attempting `StateWithExtensions::unpack` first and, on any failure, falling back to a bare length check (`data.len() == Multisig::get_packed_len()`) before unconditionally calling `Multisig::unpack`. Because SPL Token-2022 accounts are variable-length (base struct + TLV extension bytes), an attacker who owns/writes a token-program account can shape its extension bytes so the total length exactly equals `Multisig::get_packed_len()` while making the TLV region itself malformed (e.g. an extension whose declared length overruns the remaining buffer), causing `StateWithExtensions::<Account>::unpack` (and the `Mint` attempt) to legitimately return `Err`. The fallback path then blindly reinterprets those same corrupted bytes as a `Multisig`, producing a `UiMultisig` with signer pubkeys/authority counts pulled from what is actually mangled Account/Mint/extension data.

### Finding Description
The relevant control flow is in `parse_token_v3`: [1](#0-0) [2](#0-1) [3](#0-2) 

The function tries, in order: (1) unpack as `Account` with extensions, (2) unpack as `Mint` with extensions, (3) if neither succeeds *and* the byte length happens to equal `Multisig::get_packed_len()`, unpack as `Multisig` — with no further structural cross-check that the bytes are actually multisig-shaped (e.g. no verification that the discriminating "account type"/extension markers used by (1)/(2) are absent, and no sanity bound on `m`/`n` against `Pubkey::default()` filtering beyond display formatting).

An SPL Token-2022 `Account` with extensions is laid out as `Account::LEN` (165 bytes) + 1 account-type marker byte + a TLV extension region. By choosing the extension payload size such that `165 + 1 + TLV_bytes == Multisig::get_packed_len()` (355 bytes), while corrupting one extension's declared length field so it points past the buffer (or otherwise violates the extension format), `StateWithExtensions::<Account>::unpack` fails with `Err` at step (1) purely due to the malformed extension TLV — not because the underlying account isn't a real, owner-permitted token account. The same length can be reached via a corrupted `Mint` with extensions, since only the fixed base length differs (82 vs 165), and the TLV padding amount is attacker-controlled.

Once both typed unpacks fail, the code only checks `data.len() == Multisig::get_packed_len()` and calls `Multisig::unpack(data)`. `Multisig::unpack` in the SPL interface simply copies fixed-offset fields (`is_initialized`, `m`, `n`, 11 signer `Pubkey`s) out of the byte buffer with no cross-field validation, so it will very likely succeed on essentially any 355-byte buffer, including this corrupted Account/Mint data. The result is a `TokenAccountType::Multisig` response containing "signers" that are actually mint/owner/delegate/extension bytes from the real (but corrupted) token account — a misreported authority/signer set returned to the caller via a single `getAccountInfo` (jsonParsed) call.

No existing guard in `parse_token.rs` distinguishes "genuinely not Account/Mint" from "corrupted-but-owner-permitted Account/Mint that happens to collide in length with Multisig." The length-equality fallback is the sole discriminator.

### Impact Explanation
This is a decoder misreporting issue reachable from a single unprivileged `getAccountInfo(jsonParsed)` RPC call on attacker-controlled on-chain data, matching the explicitly in-scope "decoder panic and misreporting" category. It results in wrong `program/authority` data being returned to RPC clients (fake "signers"/`num_required_signers`/`num_valid_signers` reported for what is actually a corrupted token Account/Mint), which can mislead wallets, explorers, or automated tooling that trust `jsonParsed` output to represent multisig authority structures, potentially causing them to authorize or display transactions based on fabricated signer sets.

### Likelihood Explanation
The precondition is fully attacker-controlled: any account owned by the SPL Token or Token-2022 program can have arbitrary data written into it by its owner/authority (e.g., an attacker deploying their own mint/token account with a crafted, corrupted extension TLV), and no validator- or leader-side privilege is required. Constructing a 355-byte data blob with a specific corrupted TLV is a straightforward, deterministic byte-layout exercise, making this reliably repeatable with a single RPC call.

### Recommendation
In `parse_token_v3`, do not fall back to `Multisig::unpack` solely based on length equality when the `Account`/`Mint` unpack attempts failed due to structurally malformed data that was clearly intended to be a token Account/Mint with extensions (e.g., check whether an account-type/extension marker byte is present at offset `Account::LEN`/`Mint::LEN` before treating length as decisive). At minimum, propagate the specific `StateWithExtensions` unpack error for lengths that could plausibly be an extended Account/Mint rather than silently attempting a Multisig reinterpretation, or add a discriminating byte/tag that unambiguously separates Multisig data from extended Account/Mint layouts in this decoding order.

### Proof of Concept
Rust unit test to add to `account-decoder/src/parse_token.rs` tests module:
1. Compute `LEN = Multisig::get_packed_len()` (355).
2. Build `data: Vec<u8>` of length `LEN` shaped as a Token-2022 `Account` with extensions: bytes `[0..165]` = valid packed `Account` base fields (valid mint/owner/state), byte `165` = valid `AccountType::Account` marker, followed by a TLV extension header at offset 166 whose declared `length` field is set larger than the remaining buffer (e.g., points 50 bytes past `LEN`), padded/filled to make total length exactly `LEN`.
3. Assert `StateWithExtensions::<Account>::unpack(&data)` returns `Err(_)` (confirming the corruption is real and not accidentally valid).
4. Call `parse_token_v3(&data, Some(&additional_data))` and assert that it does **not** return `Ok(TokenAccountType::Multisig(_))` with signer pubkeys extracted from the mint/owner bytes of step 2 — instead expect either a distinct error (e.g. `ParseAccountError::AccountNotParsable`/a new corrupted-extension-specific error) or, at minimum, assert the returned "signers" do not equal the known mint/owner pubkeys planted in the crafted `Account` bytes (proving the current code currently DOES misreport them, which the fix should change).

### Citations

**File:** account-decoder/src/parse_token.rs (L24-34)
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

**File:** account-decoder/src/parse_token.rs (L91-109)
```rust
    if data.len() == Multisig::get_packed_len() {
        let multisig = Multisig::unpack(data)
            .map_err(|_| ParseAccountError::AccountNotParsable(ParsableAccount::SplToken))?;
        Ok(TokenAccountType::Multisig(UiMultisig {
            num_required_signers: multisig.m,
            num_valid_signers: multisig.n,
            is_initialized: multisig.is_initialized,
            signers: multisig
                .signers
                .iter()
                .filter_map(|pubkey| {
                    if pubkey != &Pubkey::default() {
                        Some(pubkey.to_string())
                    } else {
                        None
                    }
                })
                .collect(),
        }))
```
