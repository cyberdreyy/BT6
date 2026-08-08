### Title
`convert_default_account_state` silently substitutes `AccountState::Uninitialized` for an out-of-range `DefaultAccountState` discriminant, misreporting `getAccountInfo` output - ([File: account-decoder/src/parse_token_extension.rs])

### Summary
`convert_default_account_state` converts the raw `state` byte of a Mint's `DefaultAccountState` extension into `spl_token_2022_interface::state::AccountState` via `AccountState::try_from(default_account_state.state).unwrap_or_default()`. Any byte value other than the valid discriminants silently falls back to `AccountState::Uninitialized` instead of surfacing an error, so `getAccountInfo(jsonParsed)` on a mint written with an invalid discriminant returns a `default_account_state` value that does not match the raw on-chain byte.

### Finding Description
The relevant code is: [1](#0-0) 

`parse_extension` dispatches on `ExtensionType::DefaultAccountState`, fetches the extension via `get_extension::<DefaultAccountState>()`, and — if the extension is present and correctly sized — calls `convert_default_account_state(extension)`: [2](#0-1) 

Note that `get_extension` only validates that the extension's TLV bytes exist and are the correct length for the `DefaultAccountState` struct (a single `u8` field, `state`); it performs no validation of the value of that byte. Because `state` is a raw `u8` and `AccountState` is a 3-variant enum (`Uninitialized = 0`, `Initialized = 1`, `Frozen = 2`), any byte in `3..=255` fails `TryFrom`, and `.unwrap_or_default()` converts that failure into `AccountState::Uninitialized` (its `Default` impl) rather than propagating an error or marking the extension unparseable.

An attacker who owns/creates a mint account (e.g., in a test validator, or any context where a client can supply/construct raw account bytes that will later be queried) can write a `DefaultAccountState` extension TLV with `state = 0xFF`. When any client calls `getAccountInfo` with `jsonParsed` encoding on that mint, the RPC decodes it through this path and returns `account_state: "uninitialized"` even though the raw byte on chain is `0xFF`, an internally inconsistent/impossible SPL Token-2022 mint state. This is a case of the parser masking invalid on-chain data as valid, distinct output rather than reporting a decode failure.

### Impact Explanation
This is a wrong-value RPC response: `getAccountInfo(jsonParsed)` misreports the `default_account_state` field of a Mint extension, silently coercing an invalid/out-of-range discriminant to a valid-looking `"uninitialized"` state instead of indicating unparseable/invalid data. It does not cause a crash, panic, or consensus-state mutation — it is scoped to decoder misreporting of account data returned via a single JSON-RPC query, matching the "wrong data returned" / decoder misreporting category referenced in the audit scope.

### Likelihood Explanation
Fully reproducible and deterministic: any account (owned by anyone, since RPC decoding does not check the account's actual owner program before jsonParsed dispatch is program-specific but token-program parsing happens once identified as a token/token-2022 mint) can be constructed with a `DefaultAccountState` TLV entry whose `state` byte is outside `0..=2`. A single `getAccountInfo` call with `jsonParsed` encoding triggers the bug every time — no race conditions, no special timing, no elevated privileges required beyond being able to write/create the account data (e.g., via a local test validator or already-existing on-chain mint with unexpected raw bytes, e.g. through account reallocation/upgrade bugs in other programs). The `unwrap_or_default()` is unconditional and undocumented as a fallback for invalid data.

### Recommendation
In `convert_default_account_state`, do not silently default on `try_from` failure. Either:
- Return a `Result`/`Option` from `convert_default_account_state` and have `parse_extension` map a decode failure of the byte to `UiExtension::UnparseableExtension`, consistent with how other malformed extensions in this file are already handled via `.unwrap_or(UiExtension::UnparseableExtension)`; or
- Add an explicit "invalid"/"unparseable" variant to `UiAccountState`/`UiDefaultAccountState` for out-of-range values so the raw byte is not conflated with a legitimate `Uninitialized` state.

### Proof of Concept
```rust
// account-decoder/src/parse_token_extension.rs (test module)
#[test]
fn test_convert_default_account_state_rejects_invalid_discriminant() {
    use spl_token_2022_interface::extension::default_account_state::DefaultAccountState;

    for byte in 0u8..=255u8 {
        let ext = DefaultAccountState { state: byte.into() };
        let ui = convert_default_account_state(ext);

        match byte {
            0 | 1 | 2 => {
                // valid discriminants map to their real state; fine.
            }
            _ => {
                // Current (buggy) behavior: silently reports "uninitialized"
                // even though `byte` is not a valid AccountState discriminant.
                // Expected/fixed behavior: caller should be able to detect
                // this was not a genuine on-chain Uninitialized state, e.g.
                // via an explicit error/Unparseable marker instead of a
                // valid-looking UiDefaultAccountState.
                assert_ne!(
                    ui.account_state,
                    UiAccountState::Uninitialized,
                    "byte {byte:#x} was silently coerced to Uninitialized, \
                     masking invalid on-chain data"
                );
            }
        }
    }
}
```
Running this test against the current implementation fails for every `byte` in `3..=255`, demonstrating that `convert_default_account_state` masks invalid on-chain discriminant bytes as a valid `AccountState::Uninitialized`, which would then be surfaced verbatim by `getAccountInfo(jsonParsed)`.

### Citations

**File:** account-decoder/src/parse_token_extension.rs (L77-82)
```rust
        ExtensionType::DefaultAccountState => account
            .get_extension::<extension::default_account_state::DefaultAccountState>()
            .map(|&extension| {
                UiExtension::DefaultAccountState(convert_default_account_state(extension))
            })
            .unwrap_or(UiExtension::UnparseableExtension),
```

**File:** account-decoder/src/parse_token_extension.rs (L209-218)
```rust
fn convert_default_account_state(
    default_account_state: extension::default_account_state::DefaultAccountState,
) -> UiDefaultAccountState {
    let account_state =
        spl_token_2022_interface::state::AccountState::try_from(default_account_state.state)
            .unwrap_or_default();
    UiDefaultAccountState {
        account_state: convert_account_state(account_state),
    }
}
```
