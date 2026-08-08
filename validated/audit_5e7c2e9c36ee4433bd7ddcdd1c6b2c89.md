### Title
Token balances silently omitted from transaction meta for token accounts referenced without invoking the SPL token program - ([File: svm/src/transaction_balances.rs])

### Summary
`BalanceCollector::collect_balances` gates all per-account token-balance extraction behind a single transaction-wide flag, `has_token_program`, which is only `true` if one of the SPL token program IDs literally appears in the transaction's `account_keys()`. A transaction can legitimately include and touch a valid, initialized token account (owned by a known token program) without ever loading the token program itself as one of its account keys, causing the resulting `getTransaction` response to omit that account's pre/post token balances entirely.

### Finding Description
In `collect_balances`, the code computes: [1](#0-0) 

`has_token_program = transaction.account_keys().iter().any(is_known_spl_token_id)`, then in the per-account loop: [2](#0-1) 

token-balance extraction (`SvmTokenInfo::unpack_token_account`) only runs `if has_token_program && ...`. This conflates two distinct things: (1) whether the *account itself* is owned by a known token program (checked correctly per-account via `is_known_spl_token_id(account.owner())`), and (2) whether the token *program* pubkey happens to be present anywhere in the transaction's key set. On Solana, only accounts that are *executed as a program* (top-level or via CPI) must be loaded into the transaction's account keys; a data-only account that is merely read (e.g., passed to another program which inspects its bytes directly, or referenced by a program not invoking `spl-token`/`spl-token-2022` at all) does not require the owning program's pubkey to also be present as a loaded key. Therefore a transaction can validly reference a token-owned account with `!transaction.is_invoked(index)` while `has_token_program` is `false`, and the per-account guard short-circuits, so `token_balances` stays empty for that account even though it is a legitimate, initialized token account touched by the transaction.

The `has_token_program` check appears intended purely as a fast-path optimization to skip the owner/unpack work for transactions with no token activity at all, but it is unsound: presence of the token program key is not equivalent to presence of a token-owned account, so it produces false negatives for balance collection.

### Impact Explanation
This is a data-misreporting bug, not a crash or DoS: `getTransaction` (and the transaction-status meta more generally, since `collect_pre_balances`/`collect_post_balances` are invoked from `svm/src/transaction_processor.rs` during normal transaction execution) will silently under-report `preTokenBalances`/`postTokenBalances` for otherwise valid token accounts. Any RPC/indexer/wallet consumer relying on `getTransaction` to see a complete and faithful view of token balance changes can be misled into believing a token account was not touched, or lose an entry it expected, even though the account was read and is genuinely owned by a known SPL token program. This falls under the "decoder/misreporting" impact category — parsed transaction output not faithfully representing on-chain state.

### Likelihood Explanation
The precondition is narrow but achievable by an unprivileged client: craft (or find/ construct) a transaction where a token-owned account is included among the loaded account keys (statically or via an ALT), is not invoked as a program, and no instruction in the same transaction separately invokes `spl-token`/`spl-token-2022`/Token-2022 program IDs so that the program key never appears in `account_keys()`. Any ordinary program that reads token-account data directly (without CPI into the token program) — e.g., a program reading raw account bytes to check a balance/owner — combined with the account being readonly/non-invoked, triggers this gap. This requires only a single self-submitted transaction plus a single `getTransaction` call, well within the single-caller/one-request-per-slot constraint.

### Recommendation
Remove the transaction-wide `has_token_program` short-circuit and rely solely on the per-account check (`is_known_spl_token_id(account.owner())`), which is already the correct/sufficient condition; if an optimization is desired, it should be based on whether any account owner (not any account key) matches a known token program, computed by scanning owners rather than keys, or simply drop the pre-check since `unpack_token_account` already fails cheaply for non-token accounts.

### Proof of Concept
Add a unit test in `svm/src/transaction_balances.rs` using a mock `SVMTransaction` whose `account_keys()` returns `[some_wallet_key, token_account_key]` (neither equal to any `is_known_spl_token_id` program ID), where `account_loader.load_account(&token_account_key)` returns an `AccountSharedData` with `owner() == spl_token::id()` and valid packed `Account` data (with a corresponding valid mint account loadable), and `transaction.is_invoked(1) == false`. Call `collect_balances` and assert that:
- Expected (per documented behavior): `token_balances` contains one `SvmTokenInfo` entry for `token_account_key`.
- Actual: `token_balances` is empty because `has_token_program` evaluates to `false`, demonstrating the omission.

### Citations

**File:** svm/src/transaction_balances.rs (L86-86)
```rust
        let has_token_program = transaction.account_keys().iter().any(is_known_spl_token_id);
```

**File:** svm/src/transaction_balances.rs (L96-104)
```rust
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
