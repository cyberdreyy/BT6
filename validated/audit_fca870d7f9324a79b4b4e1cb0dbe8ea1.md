Confirmed: `check_num_accounts` only enforces a **minimum** (`accounts.len() < num` → error), never a maximum. This means an attacker fully controls the *count* of trailing account indices in a raw instruction, and `parse_signers` in `transaction-status/src/parse_token.rs` decides the JSON shape purely from that count.

### Title
Decoder misreports single-authority SPL Token instructions as multisig based solely on trailing-account count, fabricating `multisigAuthority`/`signers` fields - ([File: transaction-status/src/parse_token.rs])

### Summary
`parse_signers` (transaction-status/src/parse_token.rs:937-961) branches on `accounts.len() > last_nonsigner_index + 1` alone, with no verification that the referenced account is an actual `Multisig` account on-chain. Because `check_num_accounts` (transaction-status/src/parse_instruction.rs:142-154) enforces only a lower bound (`accounts.len() < num`), an attacker can append arbitrary extra account indices to a single-authority `Transfer`/`Approve`/`Burn` (etc.) instruction, causing `getTransaction`/`jsonParsed` output to fabricate a `multisigAuthority` + `signers` array from unrelated pubkeys.

### Finding Description
For every SPL Token instruction that has an authority/owner (Transfer, Approve, Revoke, Burn, TransferChecked, ApproveChecked, ThawAccount, WithdrawExcessLamports, UnwrapLamports, extension updates, etc.), `parse_token` calls `parse_signers(map, last_nonsigner_index, account_keys, &instruction.accounts, owner_field, multisig_field)`: [1](#0-0) 

The branch condition is purely `accounts.len() > last_nonsigner_index + 1` — it never checks whether `account_keys[accounts[last_nonsigner_index]]` is actually a `Multisig`-owned account, nor whether the trailing entries are signers of the transaction at all; it just reads whatever pubkeys sit at those indices. Meanwhile `check_num_token_accounts`/`check_num_accounts` only rejects too few accounts, never too many: [2](#0-1) 

**On-chain behavior mismatch:** the real SPL Token program's `validate_owner` decides multisig vs. single-owner by inspecting the *account's owner/data* (whether it unpacks as a `Multisig` struct), not by counting how many accounts were passed in the instruction. If the owner account is a plain wallet, the program checks that it is a signer and simply leaves any extra trailing accounts in the `AccountInfo` iterator unconsumed — it does not error. Thus an attacker can submit a perfectly valid, successfully-executing Transfer/Approve/Burn transaction where accounts[last_nonsigner_index] is a genuine single-key wallet authority (correctly signed), but append arbitrary unrelated account indices afterward (which can even be non-signer, readonly accounts already present in the transaction's account list — no additional signature requirement is imposed by adding read-only AccountMeta entries). The on-chain program ignores the extras and the tx succeeds; the transaction-status decoder, however, sees `accounts.len() > last_nonsigner_index + 1` and unconditionally reports the transfer as multisig-authorized, inventing a `multisigAuthority` field and a `signers` array populated with the attacker-chosen trailing pubkeys — pubkeys that never signed anything and have no relation to any multisig account.

### Impact Explanation
This is decoder misreporting reachable from data an unprivileged attacker fully controls (their own transaction's instruction encoding), which is explicitly an accepted bug class per the audit's Validate section ("decoder panic and misreporting"). Any RPC client calling `getTransaction`/`getConfirmedTransaction` (or subscribing via `transactionSubscribe`) with `jsonParsed` encoding on the resulting transaction will see fabricated `multisigAuthority` + `signers` data for what was actually a normal single-key-authorized SPL Token operation. Downstream integrators (block explorers, compliance/AML tooling, wallets) that trust the parsed `multisigAuthority`/`signers` fields as ground truth about the token account's ownership model will draw incorrect conclusions (e.g., believing a transfer required multisig quorum from specific pubkeys when it did not, or attributing signing responsibility to uninvolved pubkeys).

### Likelihood Explanation
Fully attacker-controlled and deterministic: the attacker only needs to author their own transaction's instruction (append extra `AccountMeta`s to a standard SPL Token instruction using a raw builder rather than the SDK helper, which is trivial), submit it once via `sendTransaction`, and it will execute successfully on-chain since the token program ignores the unused trailing accounts for a non-multisig owner. No multisig account, no special privileges, no more than one RPC call are required. This is repeatable for every authority-bearing SPL Token instruction type that funnels through `parse_signers`.

### Recommendation
`parse_signers` (and thus every call site in `parse_token.rs` and the extension parsers) should not infer multisig status from account-list length alone. Options: (1) accept an explicit "is this a multisig instruction" hint derived from how the instruction was actually built/validated rather than trailing count, or (2) clearly document in the RPC API/response schema that the `multisigAuthority`/`signers` fields are a best-effort heuristic based on instruction shape only and are not verified against on-chain account state, so integrators do not treat them as authoritative. If stronger guarantees are desired, `check_num_accounts` could also enforce an exact expected count for non-multisig-capable call sites, but since SPL Token itself allows arbitrary extra accounts to be silently ignored, the safest fix is documentation plus explicit non-verification disclaimer, since deep on-chain-state verification is not feasible inside a stateless instruction decoder.

### Proof of Concept
```rust
// transaction-status/src/parse_token.rs (new test)
#[test]
fn test_fabricated_multisig_authority_misreport() {
    let source = Pubkey::new_unique();
    let destination = Pubkey::new_unique();
    let real_single_authority = Pubkey::new_unique();
    // attacker-chosen, unrelated pubkeys - not part of any Multisig account
    let junk1 = Pubkey::new_unique();
    let junk2 = Pubkey::new_unique();

    // Manually build a Transfer instruction (bypassing the SDK helper's
    // account-list construction) with extra trailing account metas.
    let mut ix = spl_token_2022_interface::instruction::transfer(
        &spl_token_2022_interface::id(),
        &source,
        &destination,
        &real_single_authority,
        &[], // no multisig signers per SDK
        100,
    ).unwrap();
    // Attacker appends unrelated read-only accounts after the authority.
    ix.accounts.push(AccountMeta::new_readonly(junk1, false));
    ix.accounts.push(AccountMeta::new_readonly(junk2, false));

    let message = Message::new(&[ix], None);
    let compiled_instruction = &message.instructions[0];
    let parsed = parse_token(
        compiled_instruction,
        &AccountKeys::new(&message.account_keys, None),
    ).unwrap();

    // BUG: decoder reports a fabricated multisig authority + signers,
    // even though real_single_authority is a plain single-key signer
    // and junk1/junk2 never signed anything nor belong to any multisig.
    assert_eq!(parsed.info["multisigAuthority"], real_single_authority.to_string());
    assert_eq!(
        parsed.info["signers"],
        json!([junk1.to_string(), junk2.to_string()])
    );
    // Expected/desired: since no real Multisig account exists, decoder
    // should not assert a multisig shape from account count alone.
}
```
This demonstrates that toggling only the trailing-account count (with no multisig account ever existing) deterministically flips the JSON shape from single-authority to multisig, confirming the misreport is driven purely by cardinality rather than actual ownership semantics.

### Citations

**File:** transaction-status/src/parse_token.rs (L937-961)
```rust
fn parse_signers(
    map: &mut Map<String, Value>,
    last_nonsigner_index: usize,
    account_keys: &AccountKeys,
    accounts: &[u8],
    owner_field_name: &str,
    multisig_field_name: &str,
) {
    if accounts.len() > last_nonsigner_index + 1 {
        let mut signers: Vec<String> = vec![];
        for i in accounts[last_nonsigner_index + 1..].iter() {
            signers.push(account_keys[*i as usize].to_string());
        }
        map.insert(
            multisig_field_name.to_string(),
            json!(account_keys[accounts[last_nonsigner_index] as usize].to_string()),
        );
        map.insert("signers".to_string(), json!(signers));
    } else {
        map.insert(
            owner_field_name.to_string(),
            json!(account_keys[accounts[last_nonsigner_index] as usize].to_string()),
        );
    }
}
```

**File:** transaction-status/src/parse_instruction.rs (L142-154)
```rust
pub(crate) fn check_num_accounts(
    accounts: &[u8],
    num: usize,
    parsable_program: ParsableProgram,
) -> Result<(), ParseInstructionError> {
    if accounts.len() < num {
        Err(ParseInstructionError::InstructionKeyMismatch(
            parsable_program,
        ))
    } else {
        Ok(())
    }
}
```
