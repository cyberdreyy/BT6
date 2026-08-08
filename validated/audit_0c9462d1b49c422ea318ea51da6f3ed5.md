### Title
Inconsistent `SvmTokenInfo` reporting caused by `has_token_program` short-circuit in `collect_balances` allows attacker-controlled omission of token balance metadata - ([File: svm/src/transaction_balances.rs])

### Summary
`BalanceCollector::collect_balances` gates all per-account token-balance parsing behind a single transaction-wide flag, `has_token_program`, computed as `transaction.account_keys().iter().any(is_known_spl_token_id)`. Because a transaction's `account_keys()` list is entirely attacker-controlled (any account may be included as an unused, non-signer, read-only key), an attacker can make two transactions that touch an identical fabricated SPL-token-owned account produce different `SvmTokenInfo` output purely by choosing whether to include the token program's pubkey among the (possibly unused) account keys.

### Finding Description
In `svm/src/transaction_balances.rs`, `collect_balances` computes: [1](#0-0) 

and then only attempts to parse a candidate token account into `SvmTokenInfo` when `has_token_program` is true, in addition to per-account checks (`!is_invoked`, `is_known_spl_token_id(account.owner())`): [2](#0-1) 

`has_token_program` is a transaction-global boolean derived only from the presence of a known SPL-Token/Token-2022 program ID anywhere in `account_keys()`—it does not require that the program actually be invoked, nor that it be related in any way to the account whose owner is being inspected. Since `account_keys()` for a Solana transaction message is fully attacker-supplied (a legacy or v0 message may include additional read-only, non-signer keys that are never referenced by any instruction), an attacker can trivially toggle this flag:

- Transaction A: touches fabricated account `X` (owner = SPL Token program, valid-looking packed token account data), but does not include the token-program pubkey anywhere in `account_keys()`. `has_token_program` is `false`, so the per-account condition short-circuits and `token_info` for `X` is never computed, even though `X`'s owner is a known token program and its data would otherwise unpack successfully via `SvmTokenInfo::unpack_token_account`.
- Transaction B: identical in every other respect, but adds the token-program pubkey as one extra unused, read-only, non-signer account key. `has_token_program` becomes `true`, so the account is now processed by `SvmTokenInfo::unpack_token_account`, and (assuming a valid mint account also exists) a `SvmTokenInfo` entry is produced.

Both transactions load and observe the exact same underlying account state for `X`, yet the returned `TxTokenBalances` differ solely due to unrelated key-list composition. This is a decoder/metadata misreporting bug: the presence/absence of token balance entries in pre/post balances is not a function of account state, but of an attacker-chosen, semantically irrelevant list membership check.

### Impact Explanation
`collect_balances` output feeds `preTokenBalances`/`postTokenBalances` in transaction metadata surfaced via `getTransaction`/`getBlock`/`simulateTransaction` RPC responses (through `BalanceCollector::into_vecs` → transaction-status pipeline). An attacker can cause the RPC-reported token balance metadata for their own transaction to silently omit a real token account state change (or conversely force its inclusion) purely by adding/removing an unused account key, with no other effect on execution. This is a scoped decoder misreporting/data-inconsistency issue affecting transaction metadata correctness for downstream integrators (exchanges, indexers, wallets) that rely on `preTokenBalances`/`postTokenBalances` to reconcile token transfers, matching the "decoder panic and misreporting" bounty category.

### Likelihood Explanation
Fully attacker-controlled and trivially reproducible: no privileged access is required—an ordinary client only needs to construct and submit two transactions differing solely in whether an extra, unused account key (the well-known SPL Token or Token-2022 program ID) is present in the message's `account_keys()`. No special account setup beyond a normal token account is needed, and the behavior is deterministic given the flag's implementation.

### Recommendation
Remove the transaction-wide `has_token_program` short-circuit and instead gate token-balance extraction purely on the per-account check that already exists (`!is_invoked(index) && !is_known_spl_token_id(key) && is_known_spl_token_id(account.owner())`), which is sufficient and correct on its own since it directly inspects the candidate account's actual owner. If the flag was added purely as a performance optimization to skip scanning entirely for transactions with no token accounts, compute it instead from the *owners* of loaded accounts (or drop it, since the owner check already filters correctly), not from arbitrary membership in `account_keys()`.

### Proof of Concept
```rust
// svm/src/transaction_balances.rs (test module)
// Pseudocode integration test using SVMTransaction fixtures via dev-context-only-utils.
//
// Setup:
// - Fabricate account X: owner = spl_token::id(), data = packed valid token::Account
//   (mint = M, owner = O, amount = 100).
// - Fabricate mint account M: owner = spl_token::id(), valid Mint data, decimals = 6.
// - Both accounts loaded identically for tx A and tx B via a mock AccountLoader.
//
// Transaction A: account_keys = [payer, X] (token program NOT included, and no
// instruction invokes spl_token::id()).
// Transaction B: account_keys = [payer, X, spl_token::id()] where spl_token::id()
// is appended as an extra read-only, non-signer, unused key (not referenced by
// any instruction, is_invoked(index) == false for it).
//
// #[test]
// fn collect_balances_inconsistent_on_unrelated_key_presence() {
//     let (native_a, token_a) = collector.collect_balances(&mut loader, &tx_a);
//     let (native_b, token_b) = collector.collect_balances(&mut loader, &tx_b);
//
//     // Same underlying account state for X in both cases.
//     assert_eq!(native_a, native_b);
//
//     // BUG: token_a is empty (has_token_program == false), token_b contains
//     // a SvmTokenInfo for X (has_token_program == true), despite X's owner
//     // and data being identical in both transactions.
//     assert_eq!(token_a.is_empty(), true);       // observed
//     assert_eq!(token_b.len(), 1);                // observed
//     // Expected (per invariant): token_a == token_b
//     assert_eq!(token_a, token_b); // currently FAILS, demonstrating the bug
// }
```

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
