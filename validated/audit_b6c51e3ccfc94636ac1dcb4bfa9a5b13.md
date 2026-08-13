I have sufficient evidence to complete the analysis.

### Title
Missing zero-address check on `new_authority` in `transfer_to_new_account`/`transfer_to_new_account_pda` permanently locks migrated user funds - (File: `programs/marginfi/src/instructions/marginfi_account/transfer_account.rs`)

### Summary
The account-migration instructions `transfer_to_new_account` and `transfer_to_new_account_pda` accept `new_authority` as a completely unchecked account and use it directly to initialize the authority of the newly created `MarginfiAccount`, without ever validating it is non-zero (`Pubkey::default()`). This is the same bug class as the reported `AdminUpgradeabilityProxy` issue: a critical permission/ownership field is written without a zero-address guard, and once set to zero the resulting account becomes permanently un-administrable.

### Finding Description
`TransferToNewAccount` and `TransferToNewAccountPda` both declare: [1](#0-0) [2](#0-1) 

`new_authority` is typed as `UncheckedAccount`, and the doc comment explicitly acknowledges "New authority is completely unchecked". This key is passed straight into `initialize_migrated_account`, which calls `new_account.initialize(group, new_authority, ...)`, setting `MarginfiAccount.authority` to whatever value was supplied: [3](#0-2) [4](#0-3) 

Immediately afterward, the old account is finalized: its `lending_account` (all deposit/borrow balances) is zeroed and it is flagged `ACCOUNT_DISABLED`, meaning the balances now live exclusively in `new_marginfi_account`: [5](#0-4) [6](#0-5) 

There is no `check!(new_authority.key() != Pubkey::default(), ...)` anywhere in either instruction handler or account struct. If `new_authority` is passed as `Pubkey::default()` (System Program address, no corresponding private key), `is_signer_authorized` in every subsequent instruction (`deposit`, `withdraw`, `borrow`, `repay`, `liquidate`, etc.) requires a `Signer` matching `marginfi_account.authority`, which can never be produced for the zero address: [7](#0-6) 

This mirrors the reported bug class exactly: `changeAdmin` guards against zero, but the "constructor" path (here, account-migration initialization) does not, so all subsequently migrated collateral/liabilities become un-administrable/permanently frozen.

### Impact Explanation
All lending-account state (deposits, borrows) of the old account is moved into the new account and the old account is disabled, with no ability to reverse the migration (`migrated_to`/`AccountAlreadyMigrated` check prevents re-migrating). If the new account's authority ends up as the zero address, no signer can ever satisfy `is_signer_authorized` for that account, so the migrated collateral and liabilities become permanently frozen/inaccessible — a durable freeze of user funds with direct financial effect. This can happen either by an implementation error in a client/integrator wiring `new_authority`, or a malicious/compromised `fee_payer`/relaying party constructing the transaction with a zero `new_authority` while the actual `authority` signer authorizes the migration without realizing the destination key is invalid.

### Likelihood Explanation
The `authority` field is the signer authorizing the migration, but `new_authority` itself requires no signature and is passed as raw account metadata — it is trivial for whoever constructs the transaction (which could be a UI, relayer, or third-party integrator per the `third_party_id` support in `transfer_to_new_account_pda`) to supply an incorrect or malicious zero address. Given the instruction is user-facing and permissionless with respect to `new_authority` validation, likelihood is more than theoretical, though it requires either a construction bug or targeted malicious transaction crafting.

### Recommendation
Add an explicit zero-address check on `new_authority` in both `transfer_to_new_account` and `transfer_to_new_account_pda` before calling `initialize_migrated_account`, e.g. `check!(ctx.accounts.new_authority.key() != Pubkey::default(), MarginfiError::InvalidAuthority)`. More broadly, audit all instructions that persist externally supplied `Pubkey`s into authority/admin fields for the same missing-zero-check pattern.

### Proof of Concept
1. User (or a relaying/integrator flow) calls `transfer_to_new_account` with a valid `old_marginfi_account` owned by `authority`, a fresh `new_marginfi_account`, and `new_authority` set to `Pubkey::default()`.
2. The instruction succeeds: `initialize_migrated_account` sets `new_account.authority = Pubkey::default()` and copies over `old_account.lending_account` balances.
3. `finalize_migrated_old_account` disables `old_marginfi_account` and zeroes its `lending_account`, meaning all balances now reside solely in `new_marginfi_account`.
4. Any subsequent instruction targeting `new_marginfi_account` (e.g. `withdraw`, `borrow`, `repay`) requires a `Signer` equal to `marginfi_account.authority` via `is_signer_authorized`; since no keypair corresponds to `Pubkey::default()`, this signature can never be produced, and the funds in `new_marginfi_account` are permanently frozen.

### Citations

**File:** programs/marginfi/src/instructions/marginfi_account/transfer_account.rs (L23-37)
```rust
fn initialize_migrated_account(
    new_account: &mut MarginfiAccount,
    old_account: &MarginfiAccount,
    new_authority: Pubkey,
    current_timestamp: u64,
    old_account_key: Pubkey,
) {
    new_account.initialize(old_account.group, new_authority, current_timestamp);
    new_account.lending_account = old_account.lending_account;
    new_account.emissions_destination_account = old_account.emissions_destination_account;
    new_account.account_flags = old_account.account_flags;
    new_account.migrated_from = old_account_key;
    new_account.indexer_flags = old_account.indexer_flags;
    new_account.sync_indexer_flags();
}
```

**File:** programs/marginfi/src/instructions/marginfi_account/transfer_account.rs (L39-49)
```rust
fn finalize_migrated_old_account(
    old_account: &mut MarginfiAccount,
    new_account_key: Pubkey,
    current_timestamp: u64,
) {
    old_account.migrated_to = new_account_key;
    old_account.last_update = current_timestamp;
    old_account.lending_account = LendingAccount::zeroed();
    old_account.set_flag(ACCOUNT_DISABLED, true);
    old_account.sync_indexer_flags();
}
```

**File:** programs/marginfi/src/instructions/marginfi_account/transfer_account.rs (L91-105)
```rust
    let mut new_account = ctx.accounts.new_marginfi_account.load_init()?;
    let current_timestamp = Clock::get()?.unix_timestamp as u64;
    initialize_migrated_account(
        &mut new_account,
        &old_account,
        ctx.accounts.new_authority.key(),
        current_timestamp,
        ctx.accounts.old_marginfi_account.key(),
    );

    finalize_migrated_old_account(
        &mut old_account,
        ctx.accounts.new_marginfi_account.key(),
        current_timestamp,
    );
```

**File:** programs/marginfi/src/instructions/marginfi_account/transfer_account.rs (L158-159)
```rust
    /// CHECK: WARN: New authority is completely unchecked
    pub new_authority: UncheckedAccount<'info>,
```

**File:** programs/marginfi/src/instructions/marginfi_account/transfer_account.rs (L312-313)
```rust
    /// CHECK: WARN: New authority is completely unchecked
    pub new_authority: UncheckedAccount<'info>,
```

**File:** programs/marginfi/src/state/marginfi_account.rs (L106-121)
```rust
/// Checks if the account authority is allowed to act on their account based on frozen status.
///
/// Returns `true` if the action is allowed, `false` if blocked.
///
/// Returns `false` when both conditions are met:
/// - The account is frozen
/// - The signer is the account authority
///
/// This is intentionally separate from [`is_signer_authorized`] to return a distinct
/// `AccountFrozen` error in the instruction context  rather than `Unauthorized`.
pub fn account_not_frozen_for_authority(
    marginfi_account: &MarginfiAccount,
    signer: Pubkey,
) -> bool {
    !(marginfi_account.get_flag(ACCOUNT_FROZEN) && marginfi_account.authority == signer)
}
```

**File:** programs/marginfi/src/state/marginfi_account.rs (L124-138)
```rust
    /// Set the initial data for the marginfi account.
    fn initialize(&mut self, group: Pubkey, authority: Pubkey, current_timestamp: u64) {
        self.authority = authority;
        self.group = group;
        self.emissions_destination_account = Pubkey::default();
        self.migrated_from = Pubkey::default();
        self.last_update = current_timestamp;
        self.migrated_to = Pubkey::default();
        self.indexer_flags.is_empty = 1;
        // Seed activity flags so freshly-created accounts aren't immediately eligible for the
        // permissionless close path before the first pulse.
        self.indexer_flags.was_active_30d = 1;
        self.indexer_flags.was_active_60d = 1;
        self.active_orders = 0;
    }
```
