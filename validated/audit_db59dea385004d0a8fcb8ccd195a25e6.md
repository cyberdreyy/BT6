### Title
Out-of-bounds panic in `v0::Message::encode`/`Encodable for VersionedTransaction` when parsing ALT-resolved instruction indices without loaded addresses - ([File: transaction-status/src/lib.rs])

### Finding Description
`parse_ui_instruction` and `make_ui_partially_decoded_instruction` index directly into the `AccountKeys` slice with no bounds check: [1](#0-0) 

For a V0 (versioned) message using address-table lookups (ALT), `sanitize` validates `program_id_index` / instruction account indices against the *total resolved* key space (static keys + ALT-loaded addresses), so a legitimate, sanitize-valid transaction can legally reference an index `>= static_account_keys.len()`.

The bug is that `Encodable for v0::Message::encode` (the *meta-less* encode path, distinct from `EncodableWithMeta`) builds `account_keys` from **only the static keys**, discarding the ALT-loaded addresses entirely: [2](#0-1) 

It then calls `parse_ui_instruction(instruction, &account_keys, ...)`, which immediately does `account_keys[instruction.program_id_index as usize]` with no length check. If `program_id_index` (or any index inside `instruction.accounts`) refers to an ALT-resolved account (i.e., index `>= static_account_keys.len()`), this indexing panics.

This meta-less `Encodable` path is reachable through `VersionedTransaction::encode` (the plain `Encodable` impl, not `EncodableWithMeta`): [3](#0-2) 

...which is exactly what `TransactionWithStatusMeta::encode` invokes for the `MissingMetadata` variant: [4](#0-3) 

Contrast this with the correctly-guarded meta-aware path, `VersionedTransactionWithStatusMeta::encode`, which builds `AccountKeys` from `static_account_keys()` **plus** `self.meta.loaded_addresses`, matching the sanitize-time resolved space: [5](#0-4) [6](#0-5) 

So the vulnerability only manifests on the `MissingMetadata` branch, i.e., when a confirmed V0 transaction using ALT-resolved account indices is fetched via `getTransaction`/`jsonParsed` but its `TransactionStatusMeta` (holding `loaded_addresses`) cannot be located, even though the raw transaction bytes are still present in the block/Entry data. This is a state the blockstore layer can legitimately produce (transaction body and status metadata are stored in separate column families with different lifetimes/pruning), so it is not something the attacker needs elevated privilege to trigger — the attacker only needs to submit a normal V0 transaction that intentionally puts a lookup-table-resolved account/program at the used instruction position, wait for it to land, and then issue a single `getTransaction(sig, {encoding: "jsonParsed"})` call once that meta-missing condition applies.

### Impact Explanation
A single unprivileged RPC read (`getTransaction` with `jsonParsed` encoding) can panic the RPC-serving thread/process by indexing out of bounds in `transaction-status/src/lib.rs::parse_ui_instruction` / `make_ui_partially_decoded_instruction`, once the transaction's `program_id_index`/instruction account indices reference the ALT-loaded portion of the account-key space and the transaction is returned through the `MissingMetadata` code path. This matches the "decoder panic per read" scoped impact called out in the question and falls under the RPC decode-panic / misreporting bounty category — not a consensus break, but a per-request denial-of-service triggerable with one query.

### Likelihood Explanation
Preconditions: (1) attacker submits a valid, sanitize-passing V0 transaction whose instruction(s) reference account indices resolved via an address lookup table (entirely within attacker control — no special permissions needed to construct such a transaction); (2) the transaction is committed; (3) at query time its `TransactionStatusMeta` (and thus `loaded_addresses`) is unavailable to the RPC node even though the raw transaction data is retrievable (`TransactionWithStatusMeta::MissingMetadata`). Condition (3) is the main source of uncertainty in this write-up: I could not conclusively confirm from the available code all the exact circumstances under which `blockstore`/`storage-bigtable` construct `MissingMetadata` for a rooted/confirmed slot during a normal unprivileged `getTransaction` call within my remaining exploration budget. `MissingMetadata` handling exists explicitly in `transaction-status/src/lib.rs` and `storage-bigtable/src/lib.rs`/`storage-proto/src/convert.rs`, indicating it is a real, non-hypothetical outcome path, but the precise triggering scenario (e.g., partial ledger pruning, bigtable meta write gaps) needs further confirmation with direct blockstore-read tracing.

### Recommendation
In `Encodable for v0::Message::encode` (transaction-status/src/lib.rs), stop constructing `AccountKeys` from static keys alone when encoding as `JsonParsed`. Either (a) return an error/`UiInstruction::Compiled` fallback when `loaded_addresses` are unavailable rather than attempting to resolve ALT-referenced indices, or (b) make `parse_ui_instruction`/`make_ui_partially_decoded_instruction` bounds-check `program_id_index` and every `instruction.accounts` entry against `account_keys.len()` and return a `Result`/safe fallback (e.g., `UiInstruction::Compiled` with raw indices) instead of panicking on out-of-range indices.

### Proof of Concept
Rust unit test (add to `transaction-status/src/lib.rs` tests) demonstrating the panic:
```rust
#[test]
#[should_panic]
fn test_v0_message_encode_without_meta_oob_alt_index() {
    use solana_message::v0::{Message, MessageAddressTableLookup};
    use solana_message::compiled_instruction::CompiledInstruction;

    let static_keys = vec![Pubkey::new_unique()]; // len = 1
    let message = Message {
        header: MessageHeader {
            num_required_signatures: 1,
            num_readonly_signed_accounts: 0,
            num_readonly_unsigned_accounts: 0,
        },
        account_keys: static_keys, // only 1 static key
        recent_blockhash: Hash::default(),
        instructions: vec![CompiledInstruction {
            // program_id_index points past static keys into ALT-resolved space,
            // valid post-sanitize because total resolved keys = static + ALT loaded
            program_id_index: 1,
            accounts: vec![1],
            data: vec![],
        }],
        address_table_lookups: vec![MessageAddressTableLookup {
            account_key: Pubkey::new_unique(),
            writable_indexes: vec![0],
            readonly_indexes: vec![],
        }],
    };

    // This calls the meta-less Encodable::encode path used for
    // TransactionWithStatusMeta::MissingMetadata, which builds AccountKeys
    // from static keys only and panics indexing account_keys[1].
    let _ = message.encode(UiTransactionEncoding::JsonParsed);
}
```
Expected assertion: without the fix, this panics with an out-of-bounds index inside `parse_ui_instruction`/`account_keys[instruction.program_id_index as usize]`; after the fix, it should return a `Result`/safe fallback UI representation instead of panicking. A fuzz target should additionally permute `program_id_index` and `instruction.accounts` entries across `[0, static_len + max_alt_len)` and assert `Encodable::encode`/`EncodableWithMeta::encode_with_meta` never panic for any sanitize-valid combination.

### Citations

**File:** transaction-status/src/lib.rs (L96-126)
```rust
fn make_ui_partially_decoded_instruction(
    instruction: &CompiledInstruction,
    account_keys: &AccountKeys,
    stack_height: Option<u32>,
) -> UiPartiallyDecodedInstruction {
    UiPartiallyDecodedInstruction {
        program_id: account_keys[instruction.program_id_index as usize].to_string(),
        accounts: instruction
            .accounts
            .iter()
            .map(|&i| account_keys[i as usize].to_string())
            .collect(),
        data: bs58::encode(instruction.data.clone()).into_string(),
        stack_height,
    }
}

pub fn parse_ui_instruction(
    instruction: &CompiledInstruction,
    account_keys: &AccountKeys,
    stack_height: Option<u32>,
) -> UiInstruction {
    let program_id = &account_keys[instruction.program_id_index as usize];
    if let Ok(parsed_instruction) = parse(program_id, instruction, account_keys, stack_height) {
        UiInstruction::Parsed(UiParsedInstruction::Parsed(parsed_instruction))
    } else {
        UiInstruction::Parsed(UiParsedInstruction::PartiallyDecoded(
            make_ui_partially_decoded_instruction(instruction, account_keys, stack_height),
        ))
    }
}
```

**File:** transaction-status/src/lib.rs (L454-470)
```rust
    pub fn encode(
        self,
        encoding: UiTransactionEncoding,
        max_supported_transaction_version: Option<u8>,
        show_rewards: bool,
    ) -> Result<EncodedTransactionWithStatusMeta, EncodeError> {
        match self {
            Self::MissingMetadata(ref transaction) => Ok(EncodedTransactionWithStatusMeta {
                version: None,
                transaction: transaction.encode(encoding),
                meta: None,
            }),
            Self::Complete(tx_with_meta) => {
                tx_with_meta.encode(encoding, max_supported_transaction_version, show_rewards)
            }
        }
    }
```

**File:** transaction-status/src/lib.rs (L522-548)
```rust
    pub fn encode(
        self,
        encoding: UiTransactionEncoding,
        max_supported_transaction_version: Option<u8>,
        show_rewards: bool,
    ) -> Result<EncodedTransactionWithStatusMeta, EncodeError> {
        let version = self.validate_version(max_supported_transaction_version)?;

        Ok(EncodedTransactionWithStatusMeta {
            transaction: self.transaction.encode_with_meta(encoding, &self.meta),
            meta: Some(match encoding {
                UiTransactionEncoding::JsonParsed => parse_ui_transaction_status_meta(
                    self.meta,
                    self.transaction.message.static_account_keys(),
                    show_rewards,
                ),
                _ => {
                    let mut meta = UiTransactionStatusMeta::from(self.meta);
                    if !show_rewards {
                        meta.rewards = OptionSerializer::None;
                    }
                    meta
                }
            }),
            version,
        })
    }
```

**File:** transaction-status/src/lib.rs (L550-555)
```rust
    pub fn account_keys(&self) -> AccountKeys<'_> {
        AccountKeys::new(
            self.transaction.message.static_account_keys(),
            Some(&self.meta.loaded_addresses),
        )
    }
```

**File:** transaction-status/src/lib.rs (L683-716)
```rust
impl Encodable for VersionedTransaction {
    type Encoded = EncodedTransaction;
    fn encode(&self, encoding: UiTransactionEncoding) -> Self::Encoded {
        match encoding {
            UiTransactionEncoding::Binary => EncodedTransaction::LegacyBinary(
                bs58::encode(serialize_versioned_transaction(self)).into_string(),
            ),
            UiTransactionEncoding::Base58 => EncodedTransaction::Binary(
                bs58::encode(serialize_versioned_transaction(self)).into_string(),
                TransactionBinaryEncoding::Base58,
            ),
            UiTransactionEncoding::Base64 => EncodedTransaction::Binary(
                BASE64_STANDARD.encode(serialize_versioned_transaction(self)),
                TransactionBinaryEncoding::Base64,
            ),
            UiTransactionEncoding::Json | UiTransactionEncoding::JsonParsed => {
                EncodedTransaction::Json(UiTransaction {
                    signatures: self.signatures.iter().map(ToString::to_string).collect(),
                    message: match &self.message {
                        VersionedMessage::Legacy(message) => {
                            message.encode(UiTransactionEncoding::JsonParsed)
                        }
                        VersionedMessage::V0(message) => {
                            message.encode(UiTransactionEncoding::JsonParsed)
                        }
                        VersionedMessage::V1(message) => {
                            message.encode(UiTransactionEncoding::JsonParsed)
                        }
                    },
                })
            }
        }
    }
}
```

**File:** transaction-status/src/lib.rs (L794-818)
```rust
impl Encodable for v0::Message {
    type Encoded = UiMessage;
    fn encode(&self, encoding: UiTransactionEncoding) -> Self::Encoded {
        if encoding == UiTransactionEncoding::JsonParsed {
            let account_keys = AccountKeys::new(&self.account_keys, None);
            let loaded_addresses = LoadedAddresses::default();
            let loaded_message =
                LoadedMessage::new_borrowed(self, &loaded_addresses, &HashSet::new());
            UiMessage::Parsed(UiParsedMessage {
                account_keys: parse_v0_message_accounts(&loaded_message),
                recent_blockhash: self.recent_blockhash.to_string(),
                instructions: self
                    .instructions
                    .iter()
                    .map(|instruction| {
                        parse_ui_instruction(
                            instruction,
                            &account_keys,
                            Some(TRANSACTION_LEVEL_STACK_HEIGHT as u32),
                        )
                    })
                    .collect(),
                address_table_lookups: None,
                transaction_config: None,
            })
```
