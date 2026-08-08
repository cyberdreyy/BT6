### Title
Non-executable account referenced as a bogus `programIdIndex` causes `is_invoked()` to suppress token-balance collection for a genuine SPL-token-owned account, producing inconsistent native vs. token balances - ([File: svm/src/transaction_balances.rs])

### Summary
`BalanceCollector::collect_balances` unconditionally records the native lamport balance for every account in the transaction, but only records the SPL token balance if `!transaction.is_invoked(index)` holds. Because `is_invoked` is a purely structural check on the compiled message (it is true whenever `account_keys[index]` is referenced as a `programIdIndex` in *any* instruction, regardless of whether that account is actually executable), an attacker can force `is_invoked(index)` to be true for a real, non-program SPL-token account simply by pointing a (deliberately failing) instruction's `program_id_index` at it.

### Finding Description
In `collect_balances`: [1](#0-0) 

- Line 94 pushes `account.lamports()` into `native_balances` unconditionally for every account index.
- Lines 96-99 gate token-balance collection behind `!transaction.is_invoked(index)`, intending to exclude the token *program* account itself (which is also excluded separately via `!is_known_spl_token_id(key)`), not legitimate token *data* accounts owned by that program.

`is_invoked(index)` is derived from the compiled instructions in the sanitized message: it is true whenever index appears as a `program_id_index` in any instruction of the transaction, independent of whether the referenced account is executable or owned by a loader. Message sanitization (`SanitizedTransaction::try_new`) only validates index bounds and duplicate/signer constraints — it does not verify that a `program_id_index` points to an actual executable program. That validation happens later, during instruction execution, and only causes that specific instruction (and hence the whole transaction) to fail; it does not prevent balance collection, which runs regardless of overall transaction success so that RPC can still report `preBalances`/`postBalances` for failed transactions.

An attacker can therefore:
1. Build a transaction whose `account_keys` include (a) a real SPL Token program (invoked legitimately in one instruction) and (b) a distinct SPL-token-owned data account (e.g., a real token account with a nonzero balance) at a different index.
2. Add a second, throwaway instruction whose `program_id_index` points at the data account's index. This instruction will fail at execution time (invalid/non-executable program id), but the transaction message is still valid and processed (fee charged, balances collected) before/after execution.
3. Because that data account's index now satisfies `is_invoked(index) == true`, the token-balance branch is skipped for it at lines 96-99, even though it is a genuine, unrelated SPL token account with real token contents — while its native lamport balance is still recorded normally.

The result: `getTransaction` (jsonParsed) will show a native lamport delta for that account index but no corresponding `preTokenBalances`/`postTokenBalances` entry, even though the account is a legitimate token account whose balance a wallet/indexer would expect to see reported.

### Impact Explanation
This is a misreporting/inconsistent-account-data-returned issue: a client reading `getTransaction` for a transaction it (or another party) submitted can be given a native balance change with no matching token balance entry for an account that is, in fact, a real SPL token account. This can mislead wallets/indexers relying on token balance deltas to reflect true token account state, matching the "wrong account data returned" / decoder-misreporting bounty category. It requires only a single, unprivileged client-submitted transaction and a single subsequent read RPC call, both within the allowed attacker model.

### Likelihood Explanation
Feasible and fully attacker-controlled: constructing a transaction with an arbitrary `program_id_index` pointing at a non-program account is a standard message-construction operation available to any transaction sender; no special privileges, staked node, or leader control is needed. The only requirement is that the transaction include the real SPL Token program (to satisfy `has_token_program`) and a second account that is both owned by a token program and (ab)used as a bogus program-id target elsewhere in the same message.

### Recommendation
Change the exclusion logic in `collect_balances` so that it does not rely purely on `is_invoked(index)` to distinguish "this is the token program account" from "this is a token data account." Since `is_known_spl_token_id(key)` already excludes the well-known program pubkeys, the additional `!transaction.is_invoked(index)` check is both redundant for that purpose and unsafe as a general "not a token account" signal — remove it, or replace it with a check based on whether the account is executable/loader-owned rather than whether its index happens to appear as a `program_id_index` anywhere in the message.

### Proof of Concept
Unit test plan for `BalanceCollector::collect_balances` (in `svm/src/transaction_balances.rs`, using a mock `SVMTransaction`):
1. Construct `account_keys = [fee_payer, spl_token_program, victim_token_account]`.
2. Mock `account_loader.load_account(victim_token_account)` to return an `AccountSharedData` owned by `spl_token_program`, whose data unpacks via `generic_token::Account::unpack` into a valid token account with nonzero `amount`, and whose `mint` account also loads/unpacks successfully.
3. Mock `transaction.is_invoked(2)` (index of `victim_token_account`) to return `true` (simulating a bogus instruction whose `program_id_index == 2`), while `transaction.is_invoked(1)` (the real token program) is also `true`.
4. Call `collect_balances` and assert:
   - `native_balances[2] == victim account lamports` (native balance is recorded).
   - `token_balances` does **not** contain an entry for `account_index == 2`, despite the account being a genuine, valid SPL token account — demonstrating the native/token balance inconsistency.
5. As a control, repeat with `is_invoked(2) == false` and confirm the token balance *is* correctly collected, proving the divergence is solely caused by the `is_invoked` gating.

### Citations

**File:** svm/src/transaction_balances.rs (L86-104)
```rust
        let has_token_program = transaction.account_keys().iter().any(is_known_spl_token_id);

        for (index, key) in transaction.account_keys().iter().enumerate() {
            let Some(account) = account_loader.load_account(key) else {
                native_balances.push(0);
                continue;
            };

            native_balances.push(account.lamports());

            if has_token_program
                && !transaction.is_invoked(index)
                && !is_known_spl_token_id(key)
                && is_known_spl_token_id(account.owner())
                && let Some(token_info) =
                    SvmTokenInfo::unpack_token_account(account_loader, &account, index)
            {
                token_balances.push(token_info);
            }
```
