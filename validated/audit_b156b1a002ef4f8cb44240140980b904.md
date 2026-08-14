Confirmed: the only guard against a zero-address `new_authority` exists in the off-chain CLI (`p0-cli/src/processor/account.rs`), not on-chain. On-chain, both `TransferToNewAccount` and `TransferToNewAccountPda` accept `new_authority` as a fully unchecked `UncheckedAccount`, and `is_signer_authorized` (in `programs/marginfi/src/state/marginfi_account.rs`) only checks who can *invoke* the transfer, not the destination validity.

### Title
Unvalidated `new_authority` in account migration permits assigning the migrated account (and all its transferred positions) to the zero address, permanently freezing funds - ([File: programs/marginfi/src/instructions/marginfi_account/transfer_account.rs])

### Summary
`transfer_to_new_account` and `transfer_to_new_account_pda` let any account authority (an unprivileged, ordinary user) migrate their `MarginfiAccount` — including all deposit/borrow positions — to a new account under an arbitrary `new_authority`. The `new_authority` account is declared `UncheckedAccount` with no on-chain validation that it is non-zero or otherwise sane, mirroring the reported feeWallet issue where an address setter accepts any value including `Pubkey::default()`.

### Finding Description
In `transfer_to_new_account`, `new_authority` is annotated only with `/// CHECK: WARN: New authority is completely unchecked` and typed as `UncheckedAccount<'info>`: [1](#0-0) 
The handler unconditionally uses `ctx.accounts.new_authority.key()` to initialize the new account's `authority` field via `initialize_migrated_account`: [2](#0-1) [3](#0-2) 
The same pattern exists in `transfer_to_new_account_pda`, where the new account is even a PDA derived from `new_authority`, meaning a `Pubkey::default()` authority is fully accepted and seeded into the PDA: [4](#0-3) 
No check anywhere in either handler rejects `new_authority == Pubkey::default()` (or any other unownable/harmful pubkey). The old account is simultaneously disabled and marked as migrated (`ACCOUNT_DISABLED`, `migrated_to`), and its entire `LendingAccount` (all balances) is copied onto the new account, so the old account can no longer be used to recover the funds.

Notably, the off-chain CLI explicitly guards against this exact case (`if new_authority == Pubkey::default() { bail!("Cannot transfer authority to the zero pubkey"); }`), confirming the team is aware zero-address assignment is dangerous, but this guard is absent from the on-chain program that any client (not just the CLI) can call directly: [5](#0-4) 

### Impact Explanation
If `new_authority` is set to `Pubkey::default()` (or any address with no known private key), the resulting `MarginfiAccount`'s `authority` field becomes a pubkey nobody can sign for. Since all subsequent user actions (deposit, withdraw, borrow, repay, liquidate CPIs, etc.) require `is_signer_authorized` to match a real signer against `MarginfiAccount.authority`, the migrated account's balances become permanently unrecoverable — a durable freeze of user funds with direct financial effect, matching the "funds becoming irretrievable" impact described in the source report. Because the old account is disabled and its lending_account zeroed as part of the same transaction, there is no rollback path.

### Likelihood Explanation
This is directly reachable by any unprivileged user with a `MarginfiAccount` — no admin or special permission is required. It could occur from a client-side bug, a copy-paste/typo error passing a default `Pubkey`, or a malicious front-end tricking a user into signing a migration with a zero/burn address. Given the CLI itself found this scenario worth explicitly blocking, it is a realistic operational hazard rather than a purely theoretical one.

### Recommendation
Add an on-chain check in both `transfer_to_new_account` and `transfer_to_new_account_pda` rejecting `new_authority.key() == Pubkey::default()` (and consider disallowing other well-known unownable addresses, e.g. the program's own ID or system program ID) before performing the migration, mirroring the guard already present in the off-chain CLI.

### Proof of Concept
1. Create a `MarginfiAccount` with deposits/borrows as `authority` A.
2. Call `transfer_to_new_account` (or `transfer_to_new_account_pda`) with `new_authority = Pubkey::default()`.
3. The instruction succeeds: the old account is disabled/migrated, and the new account is initialized with `authority = Pubkey::default()`, holding all the transferred balances (`initialize_migrated_account` copies `lending_account`).
4. Any subsequent instruction requiring `authority` to sign (deposit/withdraw/borrow/repay/liquidate) fails `is_signer_authorized` for every possible caller, since no one can produce a valid signature for `Pubkey::default()`. The funds in the new account are now permanently frozen.

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

**File:** programs/marginfi/src/instructions/marginfi_account/transfer_account.rs (L292-313)
```rust
    #[account(
        init,
        payer = fee_payer,
        space = 8 + std::mem::size_of::<MarginfiAccount>(),
        seeds = [
            MARGINFI_ACCOUNT_SEED.as_bytes(),
            group.key().as_ref(),
            new_authority.key().as_ref(),
            &account_index.to_le_bytes(),
            &third_party_id.unwrap_or(0).to_le_bytes(),
        ],
        bump
    )]
    pub new_marginfi_account: AccountLoader<'info, MarginfiAccount>,

    pub authority: Signer<'info>,

    #[account(mut)]
    pub fee_payer: Signer<'info>,

    /// CHECK: WARN: New authority is completely unchecked
    pub new_authority: UncheckedAccount<'info>,
```

**File:** p0-cli/src/processor/account.rs (L1179-1181)
```rust
    if new_authority == Pubkey::default() {
        bail!("Cannot transfer authority to the zero pubkey");
    }
```
