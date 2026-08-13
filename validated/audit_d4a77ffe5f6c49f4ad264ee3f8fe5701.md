### Title
Missing On-Chain Validation of `new_authority` in `transfer_to_new_account`/`transfer_to_new_account_pda` Can Permanently Freeze Migrated Account Funds - ([File: programs/marginfi/src/instructions/marginfi_account/transfer_account.rs])

### Summary
The external report describes a bug in `ft4`'s `update_main_auth_descriptor()` where the new, most-privileged auth descriptor is not validated to retain the flags needed to access the account, potentially locking funds permanently. The analogous pattern exists in marginfi-v2's account-migration instructions, where the `new_authority` that becomes the sole controller of a migrated `MarginfiAccount` is never validated on-chain.

### Finding Description
`transfer_to_new_account` and `transfer_to_new_account_pda` create a brand-new `MarginfiAccount` whose only authority is `new_authority`, and irreversibly disable the old account (`ACCOUNT_DISABLED`, zeroed `lending_account`, `migrated_to` set) so the migration cannot be repeated or reverted: [1](#0-0) [2](#0-1) 

The `new_authority` account is explicitly documented as completely unchecked in both instruction contexts: [3](#0-2) [4](#0-3) 

No on-chain check exists (e.g., rejecting `Pubkey::default()`, or the old account's own key/PDA that could create unreachable state) analogous to `ft4`'s missing flag validation on the new main auth descriptor. The only place such a guard exists is in the client-side CLI helper, which cannot be relied upon since it is not enforced by the program itself: [5](#0-4) 

The test suite even acknowledges this design gap directly in a comment: "Here the user moves authority to some new wallet. WARN: User picks the new authority with no restrictions!" [6](#0-5) 

Because the old account is unconditionally disabled and its `lending_account` state zeroed out as part of the same instruction, once migration completes, the newly created account is the sole holder of all balances, and if `new_authority` is `Pubkey::default()` or any other unreachable/non-signable key, no party (including the group admin) can ever authorize further actions (withdraw, transfer again, close) on that account, since all downstream authorization checks (`is_signer_authorized`, `account_not_frozen_for_authority`) require a valid signer matching `authority`.

### Impact Explanation
This is a durable freeze of user funds with financial effect: any balances migrated to the new account become permanently inaccessible if `new_authority` is set to an unreachable pubkey (accidentally or maliciously via a compromised/buggy front-end, or a third-party integrator calling the instruction directly without the CLI's guard). This matches the reported bug class of "no auth descriptor capable of account operations, potentially locking all funds."

### Likelihood Explanation
The instructions are permissionless from the perspective of the account owner (self-service, not admin-gated) — `authority` need only be the existing account owner, and `new_authority` is a fully attacker/user-controlled `UncheckedAccount` with zero on-chain constraints. Any integrator, custom script, or user error bypassing the CLI's client-side check can trigger this; it requires no privileged access, matching the "unprivileged-user analog" scope.

### Recommendation
Add an on-chain constraint in `TransferToNewAccount` and `TransferToNewAccountPda` (mirroring the CLI check) rejecting `new_authority == Pubkey::default()`, and consider additional sanity checks (e.g., disallowing `new_authority` equal to a non-signable/system-owned degenerate key) before finalizing the migration and disabling the old account.

### Proof of Concept
1. Account owner calls `transfer_to_new_account` (or `transfer_to_new_account_pda`) with `new_authority = Pubkey::default()`.
2. The instruction succeeds: `initialize_migrated_account` sets the new account's `authority` field to the zero pubkey, and `finalize_migrated_old_account` disables the old account and zeroes its `lending_account`, per [1](#0-0) .
3. All balances now live in the new account, whose `authority` is the system-owned zero pubkey — no valid signer can ever satisfy `is_signer_authorized` for that account, permanently freezing the funds.

### Citations

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

**File:** programs/marginfi/src/instructions/marginfi_account/transfer_account.rs (L84-99)
```rust
    // Prevent multiple migrations from the same account
    check_eq!(
        old_account.migrated_to,
        Pubkey::default(),
        MarginfiError::AccountAlreadyMigrated
    );

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

**File:** programs/marginfi/src/instructions/marginfi_account/transfer_account.rs (L153-159)
```rust
    pub authority: Signer<'info>,

    #[account(mut)]
    pub fee_payer: Signer<'info>,

    /// CHECK: WARN: New authority is completely unchecked
    pub new_authority: UncheckedAccount<'info>,
```

**File:** programs/marginfi/src/instructions/marginfi_account/transfer_account.rs (L307-313)
```rust
    pub authority: Signer<'info>,

    #[account(mut)]
    pub fee_payer: Signer<'info>,

    /// CHECK: WARN: New authority is completely unchecked
    pub new_authority: UncheckedAccount<'info>,
```

**File:** p0-cli/src/processor/account.rs (L1178-1181)
```rust
) -> Result<()> {
    if new_authority == Pubkey::default() {
        bail!("Cannot transfer authority to the zero pubkey");
    }
```

**File:** tests/specs/basic/12_transfer_account.spec.ts (L55-56)
```typescript
  // Here the user moves authority to some new wallet. WARN: User picks the new authority with no
  // restrictions!
```
