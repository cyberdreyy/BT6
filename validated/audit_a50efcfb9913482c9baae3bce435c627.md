This confirms the bug: `impl Encodable for v0::Message::encode` builds `let account_keys = AccountKeys::new(&self.account_keys, None)` — static keys only, with `loaded_addresses: LoadedAddresses::default()` — even for `UiTransactionEncoding::JsonParsed` [1](#0-0) . This path is used whenever a `VersionedTransaction` is encoded via the `Encodable` (not `EncodableWithMeta`) trait, i.e., when `TransactionWithStatusMeta::MissingMetadata` (no meta available) is encoded [2](#0-1) . Instructions in a V0 message can reference account indices belonging to address-lookup-table-loaded accounts, which lie beyond `static_account_keys.len()`; with `AccountKeys::new(&self.account_keys, None)`, indexing beyond the static length is out of bounds.

Most instruction parsers guard against this with `instruction.accounts.iter().max() < account_keys.len()` before indexing (`parse_system.rs`, `parse_vote.rs`, `parse_stake.rs`, `parse_bpf_loader.rs`, `parse_associated_token.rs`, `parse_address_lookup_table.rs`, all commented "Runtime should prevent this from ever happening") [3](#0-2) [4](#0-3) [5](#0-4) . However, `transaction-status/src/parse_token.rs` (the SPL Token / Token-2022 parser — the most heavily used program on Solana) contains no such `iter().max()` bounds check anywhere in the file; it relies solely on `check_num_token_accounts`, which only validates the count of `instruction.accounts`, not that each index is `< account_keys.len()` [6](#0-5) . So for any Token/Token-2022 instruction indexing `account_keys[instruction.accounts[i] as usize]`, e.g. lines like [7](#0-6) , an out-of-range index directly panics instead of returning `ParseInstructionError`.

I was not able to fully confirm within the given iterations the exact runtime condition under which `MissingMetadata` transactions containing V0 messages with lookup-table token instructions actually reach this specific `Encodable::encode` path in production RPC serving code (as opposed to always going through `EncodableWithMeta::encode_with_meta`, which correctly uses `meta.loaded_addresses`) — this would need to be traced further in `rpc/src/rpc.rs` and blockstore transaction-status storage to determine precisely which JSON-RPC method(s) construct a `MissingMetadata` variant for a V0 transaction and then call the legacy `Encodable::encode`.

### Title
Index-out-of-bounds panic parsing SPL Token instructions on transactions missing status metadata (V0/versioned transactions) — ([File: transaction-status/src/parse_token.rs])

### Summary
`v0::Message::encode` for `UiTransactionEncoding::JsonParsed` builds `AccountKeys` from only the static account keys (`LoadedAddresses::default()`), which is correct only when the transaction does not use address-lookup-table (ALT) accounts. When this path is taken for a versioned transaction that does use ALT accounts, instruction account indices referencing loaded (ALT) accounts exceed the length of the `AccountKeys` slice built. Most instruction parsers defensively bounds-check `instruction.accounts` against `account_keys.len()` before indexing, but `parse_token.rs` (SPL Token / Token-2022) does not, so it panics with an index-out-of-bounds error instead of gracefully returning `ParseInstructionError`.

### Finding Description
`transaction-status/src/lib.rs`'s `impl Encodable for v0::Message` builds:
```rust
let account_keys = AccountKeys::new(&self.account_keys, None);
let loaded_addresses = LoadedAddresses::default();
```
even when encoding is `JsonParsed` [1](#0-0) . This is used from `Encodable for VersionedTransaction::encode`/`json_encode`, which is invoked for `TransactionWithStatusMeta::MissingMetadata` (no `TransactionStatusMeta`, hence no `loaded_addresses`) [2](#0-1) [8](#0-7) . This differs from the metadata-aware path (`EncodableWithMeta`), which correctly passes `meta.loaded_addresses` [9](#0-8) .

Every instruction-level parser downstream indexes into the passed `AccountKeys` using raw `u8` indices from `CompiledInstruction.accounts`. Parsers for System, Vote, Stake, BPF-Loader, Associated-Token-Account, and Address-Lookup-Table all defensively check `instruction.accounts.iter().max() < account_keys.len()` before indexing, explicitly noting "Runtime should prevent this from ever happening" [3](#0-2) . `parse_token.rs`, which handles the SPL Token / Token-2022 program (by far the most common program on Solana), has no equivalent check; it goes straight from `check_num_token_accounts` (a length-only check) to `account_keys[instruction.accounts[i] as usize]` [6](#0-5) [7](#0-6) .

### Impact Explanation
If reached, this causes a decoder panic when the RPC server serves a `getTransaction`/`getBlock` request with `encoding=jsonParsed` for a versioned (V0) transaction that both (a) uses address-lookup-table accounts and (b) invokes an SPL Token instruction referencing an ALT-loaded account, under conditions where the transaction's status metadata is unavailable (the `MissingMetadata` code path). A panic in the RPC-serving thread can crash or degrade the RPC service for a query made by any unprivileged client, and no other program parser is similarly exposed because they all defensively bounds-check.

### Likelihood Explanation
The likelihood is uncertain because it depends on how frequently/whether `TransactionWithStatusMeta::MissingMetadata` is actually produced for versioned (V0) transactions in the serving paths reachable by `getTransaction`/`getBlock`, which I could not fully trace within the tool-call budget. If `MissingMetadata` for V0 transactions with ALT + token instructions is achievable by an ordinary user issuing a single RPC query (e.g., an unfiltered or partially-indexed historical query), this is a straightforward, no-privilege, single-request trigger.

### Recommendation
Add the same `instruction.accounts.iter().max() < account_keys.len()` defensive bounds check used by other program parsers (`parse_system`, `parse_vote`, `parse_stake`, `parse_bpf_loader`, `parse_associated_token`, `parse_address_lookup_table`) to `parse_token.rs`'s entry point before any indexing occurs, returning `ParseInstructionError::InstructionKeyMismatch` instead of panicking. Additionally, review whether `v0::Message::encode` (the `Encodable`, non-meta-aware path) should ever be reachable for `JsonParsed` encoding at all, since it can never correctly resolve ALT-loaded accounts.

### Proof of Concept
Not independently verified end-to-end (would require confirming that `TransactionWithStatusMeta::MissingMetadata` for a V0 transaction is reachable via a live RPC `getTransaction`/`getBlock` call in this codebase). Conceptually:
1. Construct a versioned (V0) transaction that uses an address-lookup-table account as one of the accounts passed to an SPL Token instruction (e.g., `Transfer`), such that the account index in `CompiledInstruction.accounts` is `>= static_account_keys.len()`.
2. Get this transaction into blockstore in a state where its `TransactionStatusMeta` is unavailable/missing (`MissingMetadata` variant).
3. Call `getTransaction` (or `getBlock`) with `encoding: "jsonParsed"`.
4. `VersionedTransaction::encode(JsonParsed)` → `v0::Message::encode` builds `AccountKeys` without loaded addresses → `parse_token` indexes `account_keys[idx]` with `idx >= account_keys.len()` → panic.

### Citations

**File:** transaction-status/src/lib.rs (L460-470)
```rust
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

**File:** transaction-status/src/lib.rs (L838-852)
```rust
impl EncodableWithMeta for v0::Message {
    type Encoded = UiMessage;
    fn encode_with_meta(
        &self,
        encoding: UiTransactionEncoding,
        meta: &TransactionStatusMeta,
    ) -> Self::Encoded {
        if encoding == UiTransactionEncoding::JsonParsed {
            let reserved_account_keys = ReservedAccountKeys::new_all_activated();
            let account_keys = AccountKeys::new(&self.account_keys, Some(&meta.loaded_addresses));
            let loaded_message = LoadedMessage::new_borrowed(
                self,
                &meta.loaded_addresses,
                &reserved_account_keys.active,
            );
```

**File:** transaction-status/src/parse_system.rs (L17-25)
```rust
    match instruction.accounts.iter().max() {
        Some(index) if (*index as usize) < account_keys.len() => {}
        _ => {
            // Runtime should prevent this from ever happening
            return Err(ParseInstructionError::InstructionKeyMismatch(
                ParsableProgram::System,
            ));
        }
    }
```

**File:** transaction-status/src/parse_vote.rs (L18-26)
```rust
    match instruction.accounts.iter().max() {
        Some(index) if (*index as usize) < account_keys.len() => {}
        _ => {
            // Runtime should prevent this from ever happening
            return Err(ParseInstructionError::InstructionKeyMismatch(
                ParsableProgram::Vote,
            ));
        }
    }
```

**File:** transaction-status/src/parse_associated_token.rs (L15-23)
```rust
    match instruction.accounts.iter().max() {
        Some(index) if (*index as usize) < account_keys.len() => {}
        _ => {
            // Runtime should prevent this from ever happening
            return Err(ParseInstructionError::InstructionKeyMismatch(
                ParsableProgram::SplAssociatedTokenAccount,
            ));
        }
    }
```

**File:** transaction-status/src/parse_token.rs (L159-163)
```rust
            TokenInstruction::Transfer { amount } => {
                check_num_token_accounts(&instruction.accounts, 3)?;
                let mut value = json!({
                    "source": account_keys[instruction.accounts[0] as usize].to_string(),
                    "destination": account_keys[instruction.accounts[1] as usize].to_string(),
```

**File:** transaction-status/src/parse_token.rs (L963-965)
```rust
fn check_num_token_accounts(accounts: &[u8], num: usize) -> Result<(), ParseInstructionError> {
    check_num_accounts(accounts, num, ParsableProgram::SplToken)
}
```
