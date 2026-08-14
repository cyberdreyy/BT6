The marginfi-v2 program contains a direct analog of this bug class: the on-chain `transfer_to_new_account` / `transfer_to_new_account_pda` instructions accept a completely unchecked `new_authority` account with no validation against `Pubkey::default()`, while the corresponding CLI tooling *does* perform this check client-side — mirroring the original finding where `delegate()` guarded against the zero address but `delegateBySig()` did not.

### Title
Missing zero-address validation on `new_authority` in `transfer_to_new_account`/`transfer_to_new_account_pda` permanently bricks the migrated account - (File: `programs/marginfi/src/instructions/marginfi_account/transfer_account.rs`)

### Summary
`transfer_to_new_account` and `transfer_to_new_account_pda` let an account authority migrate all lending positions to a brand-new `MarginfiAccount` under a caller-supplied `new_authority`. The `new_authority` account is documented and implemented as fully unchecked (`/// CHECK: WARN: New authority is completely unchecked`), and there is no on-chain guard preventing `new_authority` from being `Pubkey::default()`. If that happens, `authority` on the freshly-created account is permanently set to the zero pubkey while the old account is irreversibly disabled and marked migrated — exactly the same "delegate/transfer-to-zero-address, then original is bricked and cannot be recovered" pattern as the Nouns `delegateBySig` bug.

### Finding Description
`initialize_migrated_account` sets `new_account.authority = new_authority` with zero validation: [1](#0-0) 

Both instruction handlers pass `ctx.accounts.new_authority.key()` straight through without ever comparing it to `Pubkey::default()`: [2](#0-1) [3](#0-2) 

The account struct explicitly documents `new_authority` as unchecked and permissionless: [4](#0-3) 

Meanwhile, once `finalize_migrated_old_account` runs, the old account is irrecoverably disabled (`migrated_to` set, `ACCOUNT_DISABLED` flag set), and the migration path is permanently one-way — a second call is explicitly blocked via `AccountAlreadyMigrated`: [5](#0-4) [6](#0-5) 

The off-chain CLI is aware this is dangerous and defensively rejects it before ever building the instruction — confirming the protocol's own intent that `new_authority == Pubkey::default()` must never be allowed — but this guard exists only in `p0-cli`, not in the on-chain program that actually enforces security: [7](#0-6) 

`Pubkey::default()` (all-zero) is a system-owned address with no corresponding private key, so once an account's `authority` field equals it, `is_signer_authorized` can never be satisfied for that new account by any real signer.

### Impact Explanation
Any user (or any front-end/integrator/relayer building this instruction with an unvalidated input) can end up setting `new_authority = Pubkey::default()`. All lending positions (deposits/collateral and liabilities) already exist on the newly created account per `initialize_migrated_account`'s copy of `old_account.lending_account`. Once migration completes, this new account's authority is the zero pubkey — nobody can ever sign as `authority` for it, so the funds and positions on it become permanently unrecoverable (durably frozen). Simultaneously, the old account is set `ACCOUNT_DISABLED` and `migrated_to` is now non-default, which the program refuses to overwrite (`AccountAlreadyMigrated`), foreclosing any retry or recovery path even by the group admin, since neither `TransferToNewAccount` nor any other instruction bypasses this once-only migration to let the original authority reclaim funds. This is a durable, protocol-level freeze of user collateral with direct financial effect — comparable to the original bug's effect of the user permanently losing votes and being unable to transfer their NFT.

### Likelihood Explanation
The instruction is fully permissionless with respect to `new_authority` — no allowlist, no default-pubkey check, and no CPI/UI safeguard on-chain. Any integrator, wallet, or malicious relayer that constructs this transaction (e.g., via a buggy client, a malicious dApp using blind signing, or simple input mishandling) can trigger it; the account owner still needs to sign as `authority`, but they have no way to detect the danger since the program silently accepts a zero `new_authority`. The fact that the CLI already had to add an explicit guard for this exact scenario indicates it is a known, reachable failure mode in practice, not merely theoretical.

### Recommendation
Add an explicit on-chain check in both `transfer_to_new_account` and `transfer_to_new_account_pda` requiring `ctx.accounts.new_authority.key() != Pubkey::default()` (returning e.g. `MarginfiError::InvalidAuthority` otherwise), mirroring the client-side check already present in `p0-cli`.

### Proof of Concept
1. User creates a `MarginfiAccount` and deposits collateral, becoming `authority`.
2. User (or a transaction constructed on their behalf) calls `transfer_to_new_account` with `new_authority = Pubkey::default()`.
3. `initialize_migrated_account` copies `old_account.lending_account` (all balances) into `new_account` and sets `new_account.authority = Pubkey::default()`.
4. `finalize_migrated_old_account` sets `old_account.migrated_to = new_account_key` and `ACCOUNT_DISABLED`, permanently preventing any retry of the migration.
5. No signer can ever satisfy `authority` on `new_account` (it is the zero pubkey with no private key), so all deposited collateral is now permanently unrecoverable, and the old account is disabled with no funds and no way to be re-migrated.

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

**File:** programs/marginfi/src/instructions/marginfi_account/transfer_account.rs (L84-89)
```rust
    // Prevent multiple migrations from the same account
    check_eq!(
        old_account.migrated_to,
        Pubkey::default(),
        MarginfiError::AccountAlreadyMigrated
    );
```

**File:** programs/marginfi/src/instructions/marginfi_account/transfer_account.rs (L91-99)
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
```

**File:** programs/marginfi/src/instructions/marginfi_account/transfer_account.rs (L158-159)
```rust
    /// CHECK: WARN: New authority is completely unchecked
    pub new_authority: UncheckedAccount<'info>,
```

**File:** programs/marginfi/src/instructions/marginfi_account/transfer_account.rs (L233-244)
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
    new_account.account_index = account_index;
    new_account.third_party_index = third_party_id.unwrap_or(0);
    new_account.bump = ctx.bumps.new_marginfi_account;
```

**File:** p0-cli/src/processor/account.rs (L1174-1181)
```rust
pub fn marginfi_account_transfer(
    profile: &Profile,
    config: &Config,
    new_authority: Pubkey,
) -> Result<()> {
    if new_authority == Pubkey::default() {
        bail!("Cannot transfer authority to the zero pubkey");
    }
```
