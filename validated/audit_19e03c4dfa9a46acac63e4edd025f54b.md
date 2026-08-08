### Title
`UpdateField` parser conflates `Field::Key("name"/"symbol"/"uri")` with the canonical `Field::Name`/`Field::Symbol`/`Field::Uri` variants - ([File: transaction-status/src/parse_token/extension/token_metadata.rs])

### Summary
`token_metadata_field_to_string` in `transaction-status/src/parse_token/extension/token_metadata.rs` maps `Field::Name`, `Field::Symbol`, `Field::Uri` to the literal strings `"name"`, `"symbol"`, `"uri"`, and maps any `Field::Key(key)` to `key` verbatim. An attacker who submits an `UpdateField` (or `RemoveKey`) instruction using `Field::Key("name")` (or `"symbol"`/`"uri"`) produces exactly the same `"field"` JSON value as a legitimate update to the fixed `Field::Name` variant, so the parsed transaction output returned by RPC (`getTransaction`/`getConfirmedTransaction` with `jsonParsed` encoding) cannot distinguish the two.

### Finding Description
The `TokenMetadataInstruction::UpdateField` arm at [1](#0-0)  calls `token_metadata_field_to_string`, whose match arms are: [2](#0-1) 

Because `Field::Key(key)` accepts an arbitrary attacker-controlled `String` with no reservation/collision check against the literals `"name"`, `"symbol"`, `"uri"`, an attacker can craft an `UpdateField`/`RemoveKey` instruction with `Field::Key("name".to_string())`. When this transaction is later fetched via `getTransaction`/`getConfirmedTransaction` (jsonParsed encoding), the parser emits `"field": "name"` — identical output to a real update of the canonical `Field::Name`. Any client or indexer relying on this JSON field to distinguish a canonical metadata field update from an arbitrary additional-metadata key update will misinterpret the transaction. This is a pure attacker-controlled-transaction-data path reachable purely by writing (submitting) such a transaction and reading it back through the standard RPC parsed-transaction API, matching the allowed "writing on-chain data later returned through those APIs" attack model. There is no length/validation guard anywhere in this file or in `spl_token_metadata_interface` that prevents `Field::Key` from equaling the reserved strings.

### Impact Explanation
This is limited to a display/misreporting bug in the `jsonParsed` transaction decoder: it returns a `"field"` value that is ambiguous between the reserved metadata fields and an arbitrary additional-metadata key, which can mislead RPC consumers, indexers, or wallets that use this parsed output to reconcile a token's canonical `name`/`symbol`/`uri` versus its custom additional-metadata entries. It does not affect consensus, does not cause a crash, and does not expose any account data beyond what's already in the instruction. Impact is limited to "wrong data returned" via the RPC transaction-parsing API.

### Likelihood Explanation
Fully reproducible by any unprivileged client: constructing and submitting a normal `UpdateField`/`RemoveKey` instruction with `Field::Key("name")` requires no special privileges, and querying it back via `getTransaction`/`getConfirmedTransaction` is a single RPC call. No rate-limit or validation logic blocks this.

### Recommendation
In `token_metadata_field_to_string` (and/or in the `RemoveKey` arm), disambiguate `Field::Key(key)` from the fixed variants in the emitted JSON — e.g., emit a distinguishing wrapper (`{"type": "key", "value": key}` vs `{"type": "name"}`) or reject/flag `Field::Key` values equal to `"name"`, `"symbol"`, `"uri"` when parsing for display, so RPC consumers can reliably distinguish canonical metadata-field updates from additional-metadata key updates.

### Proof of Concept
```rust
// transaction-status/src/parse_token/extension/token_metadata.rs (test mod)
#[test]
fn test_update_field_key_collides_with_reserved_name() {
    let metadata = Pubkey::new_unique();
    let update_authority = Pubkey::new_unique();

    // Attacker sets an *additional metadata* key literally named "name".
    let ix = spl_token_metadata_interface::instruction::update_field(
        &spl_token_2022_interface::id(),
        &metadata,
        &update_authority,
        spl_token_metadata_interface::state::Field::Key("name".to_string()),
        "attacker-controlled-value".to_string(),
    );
    let mut message = Message::new(&[ix], None);
    let compiled_instruction = &mut message.instructions[0];
    let parsed = parse_token(
        compiled_instruction,
        &AccountKeys::new(&message.account_keys, None),
    )
    .unwrap();

    // BUG: identical "field": "name" output as a real Field::Name update,
    // even though this is actually a Field::Key("name") additional-metadata entry.
    assert_eq!(parsed.info["field"], "name");
}
```
Expected: the assertion currently passes, demonstrating the parser cannot distinguish `Field::Key("name")` from `Field::Name` in the JSON-parsed output returned by RPC.

### Citations

**File:** transaction-status/src/parse_token/extension/token_metadata.rs (L11-18)
```rust
fn token_metadata_field_to_string(field: &Field) -> String {
    match field {
        Field::Name => "name".to_string(),
        Field::Symbol => "symbol".to_string(),
        Field::Uri => "uri".to_string(),
        Field::Key(key) => key.clone(),
    }
}
```

**File:** transaction-status/src/parse_token/extension/token_metadata.rs (L43-56)
```rust
        TokenMetadataInstruction::UpdateField(update) => {
            check_num_token_accounts(account_indexes, 2)?;
            let UpdateField { field, value } = update;
            let value = json!({
                "metadata": account_keys[account_indexes[0] as usize].to_string(),
                "updateAuthority": account_keys[account_indexes[1] as usize].to_string(),
                "field": token_metadata_field_to_string(field),
                "value": value,
            });
            Ok(ParsedInstructionEnum {
                instruction_type: "updateTokenMetadataField".to_string(),
                info: value,
            })
        }
```
