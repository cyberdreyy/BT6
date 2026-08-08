### Title
Token balance silently dropped for accounts at index ≥256 in v0 transactions using address lookup tables - ([File: svm/src/transaction_balances.rs])

### Summary
`SvmTokenInfo::unpack_token_account` truncates the account's position with `index.try_into().ok()?`, converting `usize` to `u8`. Because address-lookup-table (ALT) expansion in v0 messages lets `transaction.account_keys()` exceed 255 entries while only costing 1 byte per lookup index, an attacker can put a legitimate, correctly-initialized token account at index ≥256 and have its balance entry silently dropped from both pre- and post-token-balances, while accounts below 255 are still reported correctly.

### Finding Description
`collect_balances` iterates `transaction.account_keys()` and, for any account owned by a known SPL-token program, calls `SvmTokenInfo::unpack_token_account(account_loader, &account, index)` [1](#0-0) . Inside that function, after successfully unpacking the token account and its mint, the result is only returned if the numeric index fits in a `u8`: [2](#0-1) 

The `?` on `index.try_into().ok()?` at line 196 means that once `index >= 256`, the entire `Some(Self { .. })` construction is aborted and `None` is returned to the caller, even though the token account was valid, correctly owned by a known SPL program, and had a valid mint. The caller then simply skips pushing anything into `token_balances` for that index [3](#0-2) . Nothing distinguishes "account had no token data" from "account index too large to represent," so downstream consumers (RPC `getTransaction`/`getBlock` `preTokenBalances`/`postTokenBalances`) receive an incomplete balance set with no indication of truncation.

v0 messages support `address_table_lookups`, and each lookup only encodes 1-byte indices into the referenced ALT (not full 32-byte pubkeys), so an attacker can reference several address lookup tables in one transaction to expand `account_keys()` well past 255 entries while staying under the 1232-byte packet size limit. A legitimate token account can be resolved at index 256 or higher this way, while a decoy (fake/uninitialized) account sits below index 255 and is correctly reported — producing an asymmetric, misleading balance set.

### Impact Explanation
This causes wrong/incomplete on-chain data to be returned through the standard RPC query path (`getTransaction`, `getBlock` with token balance metadata) for a single, unprivileged submitted transaction — no crash, but a decoder misreporting bug matching the "wrong account data returned" bounty category. Tooling relying on `preTokenBalances`/`postTokenBalances` to detect token movement would falsely conclude no token activity occurred for the truncated account.

### Likelihood Explanation
Requires: (1) a v0 transaction using address lookup tables that expands `account_keys()` past 255 total entries — feasible since ALT indices cost only 1 byte each, well within the 1232-byte transaction size limit; (2) a legitimate, correctly-initialized token account resolved at index ≥256. Both are achievable by a single unprivileged client with one crafted transaction; no special privileges or multiple calls needed. Feasibility of assembling >255 total account keys via multiple ALTs in one v0 message should be validated with a constructed `VersionedMessage`/`SVMTransaction` fixture, since the account-locks/account-count validation paths were not fully traced in this review.

### Recommendation
Do not use a lossy `u8` truncation for `account_index`. Either widen `SvmTokenInfo::account_index` to `u16`/`usize` (matching what downstream `TransactionTokenBalance` can encode), or, if the field type must remain `u8` for external API compatibility, explicitly skip/flag entries whose index cannot be represented instead of silently discarding a validly-parsed token account.

### Proof of Concept
Integration test outline:
```rust
// Build a synthetic SVMTransaction/AccountKeys fixture where account_keys().len() > 256,
// e.g. by using a mock SVMTransaction impl (or a v0 VersionedMessage with several
// address_table_lookups) so the loop in collect_balances reaches index >= 256.
// Place a legitimate, correctly-initialized SPL token account at index 256.
// Place another legitimate token account at index < 255.
//
// Call BalanceCollector::collect_post_balances(...) and assert:
// - token_balances contains an entry for the low-index account
// - token_balances is MISSING an entry for the index-256 account
//   (demonstrating silent drop rather than an error or explicit "unsupported index" marker)
```
This mirrors the described scoped impact: `account_index.try_into().ok()?` (svm/src/transaction_balances.rs:196) silently converts a valid, fully-unpacked `SvmTokenInfo` into `None` for indices ≥256, producing an incomplete/misleading balance set instead of documented truncation behavior.

### Citations

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

**File:** svm/src/transaction_balances.rs (L174-203)
```rust
impl SvmTokenInfo {
    fn unpack_token_account<CB: TransactionProcessingCallback>(
        account_loader: &mut AccountLoader<CB>,
        account: &AccountSharedData,
        index: usize,
    ) -> Option<Self> {
        let program_id = *account.owner();
        let generic_token::Account {
            mint,
            owner,
            amount,
        } = generic_token::Account::unpack(account.data(), &program_id)?;

        let mint_account = account_loader.load_account(&mint)?;
        if *mint_account.owner() != program_id {
            return None;
        }

        let generic_token::Mint { decimals, .. } =
            generic_token::Mint::unpack(mint_account.data(), &program_id)?;

        Some(Self {
            account_index: index.try_into().ok()?,
            mint,
            amount,
            owner,
            program_id,
            decimals,
        })
    }
```
