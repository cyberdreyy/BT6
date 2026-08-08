### Title
`parse_transfer_fee_instruction` (via `parse_signers`) misreports the `authority`/`multisigAuthority` field for `TransferCheckedWithFee` without verifying the account is an actual signer - (File: transaction-status/src/parse_token/extension/transfer_fee.rs)

### Summary
`parse_transfer_fee_instruction` calls `parse_signers(map, 3, account_keys, account_indexes, "authority", "multisigAuthority")` for `TransferCheckedWithFee`, using purely positional indexing into `account_indexes` after only checking that there are at least 4 accounts via `check_num_token_accounts`. Neither `check_num_token_accounts` nor `parse_signers` consults the compiled instruction's/message's actual signer bitmap (`AccountKeys`/`message.is_signer`) to confirm that the account at index 3 is really a signer, so any account placed at that position is reported as the `authority` regardless of its true signer status.

### Finding Description
`parse_transfer_fee_instruction` in `transaction-status/src/parse_token/extension/transfer_fee.rs` handles `TransferFeeInstruction::TransferCheckedWithFee` as follows: [1](#0-0) 

`check_num_token_accounts` only validates `account_indexes.len() >= 4`; it does not know or check which of those accounts are signers. `parse_signers` is then invoked with a hard-coded position (`3`), and it decides whether to emit `authority`/`multisigAuthority` purely based on whether `account_indexes.len()` exceeds `last_nonsigner_index + 1` (i.e., whether there appear to be trailing multisig signer accounts) — it indexes `account_keys[account_indexes[3]]` and reports that pubkey as the authority without ever calling into the message's signer metadata (e.g. `AccountKeys`/`message.is_signer()`) to confirm the account is actually flagged as a required signer.

Because a transaction that fails at execution time (e.g., the SPL Token-2022 program rejecting the instruction with a missing-signature/incorrect-authority error) is still included on-chain with `meta.err` set, an attacker can construct and submit (or simulate) a `TransferCheckedWithFee` instruction where `account_indexes[3]` points to an arbitrary non-signer account (e.g., the `destination` account reused, or any other unrelated non-signer pubkey), while all message-level signers are legitimately signed by the attacker's own keys. The instruction still parses successfully and the parser blindly reports that non-signer account as `authority` in the `jsonParsed` output returned by `getTransaction`/`simulateTransaction`.

### Impact Explanation
This matches the audited "decoder misreporting" impact category: downstream integrators, explorers, and indexers that trust the `jsonParsed` `authority`/`multisigAuthority` field for `transferCheckedWithFee` to represent provenance/authorization can be misled into believing a specific account authorized a token-fee transfer when it did not (and, in the failing-execution case, no transfer occurred at all). This is purely a display/decoding correctness issue in the RPC's parsed-instruction output; it does not affect consensus, validator liveness, or on-chain execution correctness. [2](#0-1) 

### Likelihood Explanation
Fully feasible with a single unprivileged client: craft a transaction whose `TransferCheckedWithFee` instruction places a non-signer account (e.g. destination) at the authority slot, sign it with the attacker's own keys for whatever accounts are actually flagged signer in the message header, and submit via `simulateTransaction` or let it land on-chain (it will fail execution but still be recorded), then fetch it via `getTransaction` with `jsonParsed` encoding. No special privileges, staking, or multiple calls are required — one RPC call reproduces the misreporting.

### Recommendation
In `parse_signers` (transaction-status/src/parse_token.rs), before emitting the `authority`/`multisigAuthority`/`signers` fields, cross-check the target index against the actual signer bitmap available from `AccountKeys`/the compiled instruction's account metadata (i.e., verify `account_keys.is_signer(account_indexes[idx])` or equivalent) and only report positions confirmed to be signers; otherwise flag the field as unverified or omit it.

### Proof of Concept
Rust unit test plan (extending `transaction-status/src/parse_token/extension/transfer_fee.rs::test::test_parse_transfer_fee_instruction`):
1. Build a `TransferCheckedWithFee` instruction manually (bypassing the SDK's `transfer_checked_with_fee` helper, which always places a legitimate authority) so that `account_indexes = [source, mint, destination, destination]` — i.e., index 3 duplicates the destination account, which is not a signer in the compiled `Message`.
2. Compile the `Message` and instruction, then call `parse_token(compiled_instruction, &AccountKeys::new(&message.account_keys, None))`.
3. Assert that today's implementation returns `"authority": destination.to_string()` even though `message.is_signer(account_indexes[3] as usize)` is `false` for that index — demonstrating the parser's blind positional trust.
4. Add a companion assertion (post-fix) that a corrected `parse_signers`/`parse_token` should refuse to populate `authority` (or should return an error/flag) when `message.is_signer(idx)` is `false`, using `solana_message::AccountKeys` signer metadata as the ground truth.

### Citations

**File:** transaction-status/src/parse_token/extension/transfer_fee.rs (L44-66)
```rust
        TransferFeeInstruction::TransferCheckedWithFee {
            amount,
            decimals,
            fee,
        } => {
            check_num_token_accounts(account_indexes, 4)?;
            let additional_data = SplTokenAdditionalDataV2::with_decimals(decimals);
            let mut value = json!({
                "source": account_keys[account_indexes[0] as usize].to_string(),
                "mint": account_keys[account_indexes[1] as usize].to_string(),
                "destination": account_keys[account_indexes[2] as usize].to_string(),
                "tokenAmount": token_amount_to_ui_amount_v3(amount, &additional_data),
                "feeAmount": token_amount_to_ui_amount_v3(fee, &additional_data),
            });
            let map = value.as_object_mut().unwrap();
            parse_signers(
                map,
                3,
                account_keys,
                account_indexes,
                "authority",
                "multisigAuthority",
            );
```
