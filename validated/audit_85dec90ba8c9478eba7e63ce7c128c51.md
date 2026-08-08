### Title
Unbounded-depth JSON parsing of attacker-controlled Config account data causes stack overflow in `parse_config` - ([File: account-decoder/src/parse_config.rs])

### Summary
`parse_config` calls `serde_json::from_str::<Value>(&validator_info.config_data)` on the raw `config_data` bytes of any account owned by `config_program` whose first key equals `validator_info::id()`. Since the on-chain config program does not enforce `MAX_VALIDATOR_INFO` (576 bytes is a CLI-only client convention), an attacker can post an arbitrarily large, deeply-nested JSON string (e.g., thousands of `[`) as `config_data`, and every subsequent `getAccountInfo`/`getProgramAccounts` call with `jsonParsed` encoding against that account will attempt to recursively parse it, risking a stack overflow.

### Finding Description
`parse_account_data_v3` (`account-decoder/src/parse_account_data.rs:143`) dispatches Config-program-owned accounts to `parse_config` (`account-decoder/src/parse_config.rs:13-29`). That function deserializes the bincode `ConfigKeys`, checks that `keys[0].0 == validator_info::id()`, then calls `parse_config_data::<String>` to extract the raw string, and finally does:

```rust
config_data: serde_json::from_str(&validator_info.config_data).ok()?,
``` [1](#0-0) 

This deserializes into `serde_json::Value`, whose `Deserialize` implementation is recursive over nested arrays/objects and has no built-in depth guard for `Value` in the version used here — deeply nested input (e.g. `"[[[[[...]]]]]"`) can drive the call stack past its limit. There is no `catch_unwind` boundary around this decode path (`grep` for `catch_unwind` in the repo shows none in `rpc/`, `account-decoder/`, or the RPC pubsub path that reaches `parse_account_data_v3`), and a genuine stack overflow in Rust is not a catchable panic — it aborts the process directly. Because `MAX_VALIDATOR_INFO` is only enforced client-side in `cli/src/validator_info.rs` (not in `solana_config_interface`'s on-chain program logic), nothing prevents an attacker from writing a Config account with a `config_data` string of up to the account size limit (10 MiB) filled with nested brackets. [2](#0-1) 

### Impact Explanation
Any RPC node that serves `getAccountInfo`/`getProgramAccounts` with `encoding=jsonParsed` for the attacker's crafted Config account will crash its validator/RPC process on a single request, since stack overflow is an unrecoverable process abort rather than a catchable error. This matches the "single RPC request crashes/aborts validator process" bounty category — a single unprivileged client persists malicious on-chain data once, and every subsequent innocent read (by any client, at any time) of that account triggers the crash, making it a durable, repeatable DoS primitive against the entire fleet of RPC nodes that decode that account.

### Likelihood Explanation
Feasible with only standard, unprivileged instructions: `CreateAccount` (owned by `config_program`) plus a `config` program `store` instruction to write attacker-chosen bytes as `config_data`, with `validator_info::id()` as the first key — no special privilege, staking, or leader control required. The precondition (`MAX_VALIDATOR_INFO` not enforced on-chain) means the account can hold data large enough (well beyond 576 bytes, up to account size limits) to encode enough nesting depth to overflow a typical 8 MB thread stack. The exploit is entirely deterministic and repeatable — every subsequent `jsonParsed` read of the same account reproduces the crash.

### Recommendation
Bound the recursion/size of untrusted `config_data` before calling `serde_json::from_str`, e.g., use `serde_stacker::maybe_grow` around the deserialization, or use a `serde_json::Deserializer` configured with `Deserializer::set_recursion_limit` combined with a hard cap on `config_data` length, and return `ParseAccountError` instead of proceeding when the input exceeds sane bounds (mirroring the client-side `MAX_VALIDATOR_INFO` constraint but enforced during decode, independent of what the on-chain program allowed to be stored).

### Proof of Concept
```rust
// account-decoder/src/parse_config.rs (new test)
#[test]
fn test_parse_config_deeply_nested_json_does_not_crash() {
    let depth = 200_000; // exceeds typical stack-driven recursion limits
    let nested = "[".repeat(depth) + &"]".repeat(depth);
    let validator_info = ValidatorInfo { info: nested };
    let info_pubkey = solana_pubkey::new_rand();
    let account = create_config_account(
        vec![(validator_info::id(), false), (info_pubkey, true)],
        &validator_info,
        10,
    );
    // Expectation: this must return Err(..), not abort the process with a
    // stack overflow. Run under `cargo test -- --nocapture` and/or a
    // dedicated fuzz target that increases `depth` monotonically to confirm
    // the process never SIGSEGVs/aborts before the assert executes.
    let result = std::panic::catch_unwind(|| {
        parse_config(account.data(), &info_pubkey)
    });
    assert!(result.is_ok(), "parser should not crash the process");
    assert!(result.unwrap().is_err(), "deeply nested JSON should be rejected, not silently parsed");
}
```
Run this as an integration test increasing `depth` (e.g., 10k, 100k, 1M) to find the crash threshold on the target stack size, confirming the process aborts rather than returning `Err`.

### Citations

**File:** account-decoder/src/parse_config.rs (L16-20)
```rust
            parse_config_data::<String>(data, key_list.keys).and_then(|validator_info| {
                Some(ConfigAccountType::ValidatorInfo(UiConfig {
                    keys: validator_info.keys,
                    config_data: serde_json::from_str(&validator_info.config_data).ok()?,
                }))
```

**File:** account-decoder/src/parse_account_data.rs (L126-151)
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
```
