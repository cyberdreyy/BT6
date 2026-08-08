### Title
Attacker-controlled `equality_proof_instruction_offset`/`range_proof_instruction_offset` and account list cause a proof-context-state account to be mislabeled as `owner` in parsed `withdrawConfidentialTransfer` JSON - (File: transaction-status/src/parse_token/extension/confidential_transfer.rs)

### Summary
`parse_confidential_transfer_instruction`'s `Withdraw` branch derives which account index holds `instructionsSysvar`, `equalityProofContextStateAccount`, `rangeProofContextStateAccount`, and `owner` purely from the attacker-controlled `equality_proof_instruction_offset`/`range_proof_instruction_offset` bytes and the raw account list length, with only a minimum-length check (`check_num_token_accounts(account_indexes, 4)`), not an exact/consistent length check tied to the offsets used. This lets a client submit a transaction whose confidential-transfer instruction data/account list intentionally mismatches what the real spl-token-2022 instruction builder would produce, so the `offset < account_indexes.len().saturating_sub(1)` guard mis-skips the `rangeProofContextStateAccount` slot and instead assigns that account key to `owner` in the JSON returned by `getTransaction`/`getBlock` (jsonParsed encoding).

### Finding Description
In the `Withdraw` arm (transaction-status/src/parse_token/extension/confidential_transfer.rs:177-236) the code walks `account_indexes` starting at `offset = 2`:

```
let has_sysvar = eq_offset != 0 || range_offset != 0;
if has_sysvar && offset < account_indexes.len().saturating_sub(1) { ...instructionsSysvar...; offset += 1 }
if eq_offset == 0 && offset < account_indexes.len().saturating_sub(1) { ...equalityProofContextStateAccount...; offset += 1 }
if range_offset == 0 && offset < account_indexes.len().saturating_sub(1) { ...rangeProofContextStateAccount...; offset += 1 }
parse_signers(map, offset, account_keys, account_indexes, "owner", "multisigOwner");
``` [1](#0-0) 

`check_num_token_accounts` only enforces `account_indexes.len() >= 4`, not that the length matches the specific combination of `eq_offset`/`range_offset` chosen [2](#0-1) . Both `equality_proof_instruction_offset` and `range_proof_instruction_offset` come straight from the raw, attacker-supplied instruction data via `decode_instruction_data` with no cross-validation against the account list [3](#0-2) .

Exploit flow: an attacker crafts a `Withdraw` instruction with `equality_proof_instruction_offset = 1` (non-zero → "instruction offset" proof, triggering the `instructionsSysvar` slot at index 2) and `range_proof_instruction_offset = 0` (zero → "context account" proof, which should occupy the next free slot), but supplies only the minimum 4 accounts `[account, mint, sysvar, X]`. With `len=4`, the guard `offset < len.saturating_sub(1)` evaluates to `3 < 3 = false` for the range-context insertion step, so the `rangeProofContextStateAccount` field is silently dropped even though `range_offset == 0` says a context account should exist. `offset` remains 3, and `parse_signers` then labels `account_indexes[3]` (attacker's `X`, intended/supplied as the range-proof context account) as `"owner"` in the parsed JSON.

This instruction never needs to succeed on-chain: the RPC's `getTransaction`/`getBlock` (jsonParsed) parses instruction data/accounts straight from the recorded `CompiledInstruction`, independent of execution success [4](#0-3) , and instruction parsing itself has no dependency on the transaction's execution outcome. A transaction that fails on-chain (e.g., due to real spl-token-2022 account-requirement checks) is still recorded with `meta.err` and its instructions are still parsed and returned, so the attacker doesn't need the withdraw to actually be honored by the program — they only need `sendTransaction` to place a validly signed, sanitizable transaction into a block, then query it back via `getTransaction`.

### Impact Explanation
This is a decoder misreporting bug in the `jsonParsed` transaction-status encoder: a single unprivileged client can make `getTransaction`/`getBlock` return an incorrect/misleading `owner` field for a `withdrawConfidentialTransfer` instruction, where the labeled account is actually a proof-context-state account (or vice versa depending on chosen offsets/account count). It does not affect consensus, validator crash/deadlock, or the sBPF/token program's actual authorization logic (the label is cosmetic in RPC JSON only) — but it does return wrong/misleading account-role data through a supported RPC API, matching the "wrong ... account data returned" / "decoder ... misreporting" category.

### Likelihood Explanation
Trivial to trigger and fully repeatable: the attacker needs only to construct and sign a transaction containing a spl-token-2022 confidential-transfer `Withdraw` instruction with a deliberately mismatched account-list length relative to the chosen proof offsets, submit it via one `sendTransaction` call, and then issue one `getTransaction` (jsonParsed) call — well within the single-call-per-slot constraint. No special privileges, staked node, or leader control are required.

### Recommendation
In the `Withdraw` (and similarly `Transfer`/`TransferWithFee`) arms, validate that `account_indexes.len()` exactly matches the number of accounts implied by the specific combination of proof-instruction offsets before doing the offset walk (i.e., compute the expected count from `has_sysvar` + number of zero-offset proofs + 1 owner/signer account, and reject/return a parse error if it doesn't match), rather than relying on a generic minimum-count check plus a `saturating_sub(1)`-based "leave room for owner" heuristic that can silently swallow a proof-context slot.

### Proof of Concept
Extend `test_withdraw` (transaction-status/src/parse_token/extension/confidential_transfer.rs:846-989) with an adversarial case that manually builds a `CompiledInstruction`/`Message` (bypassing `inner_withdraw`'s correct account construction) using:
- `equality_proof_instruction_offset = 1`, `range_proof_instruction_offset = 0`
- exactly 4 accounts: `[token_account, mint, sysvar_id, range_ctx_pubkey]`

Then call `parse_token` and assert:
```rust
assert!(parsed.info.get("rangeProofContextStateAccount").is_none()); // dropped, should exist
assert_eq!(parsed.info["owner"], json!(range_ctx_pubkey.to_string())); // mislabeled
```
This demonstrates that `range_ctx_pubkey`, intended as `rangeProofContextStateAccount`, is reported as `owner` instead, confirming the misalignment for a single crafted transaction/account list.

### Citations

**File:** transaction-status/src/parse_token/extension/confidential_transfer.rs (L177-178)
```rust
        ConfidentialTransferInstruction::Withdraw => {
            check_num_token_accounts(account_indexes, 4)?;
```

**File:** transaction-status/src/parse_token/extension/confidential_transfer.rs (L179-235)
```rust
            let withdrawal_data: WithdrawInstructionData =
                *decode_instruction_data(instruction_data).map_err(|_| {
                    ParseInstructionError::InstructionNotParsable(ParsableProgram::SplToken)
                })?;
            let amount: u64 = withdrawal_data.amount.into();
            let mut value = json!({
                "account": account_keys[account_indexes[0] as usize].to_string(),
                "mint": account_keys[account_indexes[1] as usize].to_string(),
                "amount": amount,
                "decimals": withdrawal_data.decimals,
                "newDecryptableAvailableBalance": format!("{}", withdrawal_data.new_decryptable_available_balance),
                "equalityProofInstructionOffset": withdrawal_data.equality_proof_instruction_offset,
                "rangeProofInstructionOffset": withdrawal_data.range_proof_instruction_offset,

            });

            let mut offset = 2;
            let map = value.as_object_mut().unwrap();
            let has_sysvar = withdrawal_data.equality_proof_instruction_offset != 0
                || withdrawal_data.range_proof_instruction_offset != 0;

            if has_sysvar && offset < account_indexes.len().saturating_sub(1) {
                map.insert(
                    "instructionsSysvar".to_string(),
                    json!(account_keys[account_indexes[offset] as usize].to_string()),
                );
                offset += 1;
            }

            if withdrawal_data.equality_proof_instruction_offset == 0
                && offset < account_indexes.len().saturating_sub(1)
            {
                map.insert(
                    "equalityProofContextStateAccount".to_string(),
                    json!(account_keys[account_indexes[offset] as usize].to_string()),
                );
                offset += 1;
            }

            if withdrawal_data.range_proof_instruction_offset == 0
                && offset < account_indexes.len().saturating_sub(1)
            {
                map.insert(
                    "rangeProofContextStateAccount".to_string(),
                    json!(account_keys[account_indexes[offset] as usize].to_string()),
                );
                offset += 1;
            }

            parse_signers(
                map,
                offset,
                account_keys,
                account_indexes,
                "owner",
                "multisigOwner",
            );
```

**File:** rpc/src/rpc.rs (L1768-1804)
```rust
    pub async fn get_transaction(
        &self,
        signature: Signature,
        config: Option<RpcEncodingConfigWrapper<RpcTransactionConfig>>,
    ) -> Result<Option<EncodedConfirmedTransactionWithStatusMeta>> {
        self.check_if_transaction_history_enabled()?;

        let config = config
            .map(|config| config.convert_to_current())
            .unwrap_or_default();
        let encoding = config.encoding.unwrap_or(UiTransactionEncoding::Json);
        let max_supported_transaction_version = config.max_supported_transaction_version;
        let commitment = config.commitment.unwrap_or_default();
        check_is_at_least_confirmed(commitment)?;

        let confirmed_bank = self.bank(Some(CommitmentConfig::confirmed()));
        let confirmed_transaction = self
            .runtime
            .spawn_blocking({
                let blockstore = Arc::clone(&self.blockstore);
                let confirmed_bank = Arc::clone(&confirmed_bank);
                move || {
                    if commitment.is_confirmed() {
                        let highest_confirmed_slot = confirmed_bank.slot();
                        blockstore.get_complete_transaction(signature, highest_confirmed_slot)
                    } else {
                        blockstore.get_rooted_transaction(signature)
                    }
                }
            })
            .await
            .expect("Failed to spawn blocking task");

        let encode_transaction =
                |confirmed_tx_with_meta: ConfirmedTransactionWithStatusMeta| -> Result<EncodedConfirmedTransactionWithStatusMeta> {
                    Ok(confirmed_tx_with_meta.encode(encoding, max_supported_transaction_version).map_err(RpcCustomError::from)?)
                };
```
