### Title
Missing zero-address validation for `new_authority` in `transfer_to_new_account`/`transfer_to_new_account_pda` permanently locks migrated funds - ([File: programs/marginfi/src/instructions/marginfi_account/transfer_account.rs])

### Summary
`TransferToNewAccount` and `TransferToNewAccountPda` accept a `new_authority` account that is completely unchecked, with no validation that it is non-zero or otherwise "usable." [1](#0-0)  This mirrors the reported `USDKG` constructor bug where an unchecked `owner`/`compliance` parameter set to the zero address renders the contract unusable — here, an unchecked `new_authority` set to `Pubkey::default()` (or any key the caller does not control) permanently strands the migrated position.

### Finding Description
`transfer_to_new_account` and `transfer_to_new_account_pda` are called by the marginfi account's own `authority` (enforced via `is_signer_authorized`, which for this instruction only allows the account authority or, if frozen, the group admin) to migrate all balances from `old_marginfi_account` into a freshly initialized `new_marginfi_account`. [2](#0-1) 

The new account's authority is set directly from the caller-supplied, completely unchecked `new_authority` account:
```rust
initialize_migrated_account(
    &mut new_account,
    &old_account,
    ctx.accounts.new_authority.key(),
    current_timestamp,
    ctx.accounts.old_marginfi_account.key(),
);
``` [3](#0-2) 

Immediately after, the old account is disabled and its `lending_account` (all balances/positions) is zeroed out — the funds effectively "move" into `new_marginfi_account`:
```rust
old_account.migrated_to = new_account_key;
old_account.last_update = current_timestamp;
old_account.lending_account = LendingAccount::zeroed();
old_account.set_flag(ACCOUNT_DISABLED, true);
``` [4](#0-3) 

There is no on-chain check that `new_authority.key() != Pubkey::default()` (or any other sanity check on the value), unlike the `USDKG` fix which added zero-checks on `owner`/`compliance` in the constructor. Notably, the off-chain CLI helper `marginfi_account_transfer` in `p0-cli` *does* perform this check (`if new_authority == Pubkey::default() { bail!(...) }`) [5](#0-4) , confirming the team recognizes this as an invalid value — but the check exists only client-side and is not enforced by the on-chain program, so any other caller/integration bypasses it entirely. The account struct itself documents this as a known gap: `/// CHECK: WARN: New authority is completely unchecked`. [6](#0-5) 

### Impact Explanation
If `new_authority` is passed as the zero address (or any key the caller has no control over), the new account inherits all of the old account's collateral/debt positions, but no one can ever sign as its `authority` to operate on it (deposit, withdraw, borrow, repay, close, or migrate again). Because the old account is simultaneously disabled and zeroed, the funds are not recoverable through the old account either. This is a durable freeze of user funds with direct financial effect — permanent, unrecoverable loss of access to the account's holdings.

### Likelihood Explanation
The instruction is permissionless from the perspective of the account authority (no admin gating) and is directly reachable by any user via `transfer_to_new_account` / `transfer_to_new_account_pda`. While a careful direct user of the official SDK/CLI would not intentionally supply a zero `new_authority`, the on-chain program provides no defense against a malformed input coming from a buggy integrator, wallet, or third-party CPI caller (the PDA variant is explicitly designed to be called by third-party programs via `third_party_id`). The existence of the client-side zero-check in `p0-cli` — but its absence on-chain — indicates the missing invariant was not intended to be relied upon only off-chain.

### Recommendation
Add an on-chain check in both `transfer_to_new_account` and `transfer_to_new_account_pda` that rejects `new_authority.key() == Pubkey::default()` (and, if feasible, that `new_authority` is not equal to the program ID or other clearly non-wallet addresses) before finalizing the migration, mirroring the existing off-chain check in `p0-cli`.

### Proof of Concept
1. Account authority `A` owns `old_marginfi_account` with active lending/borrowing positions.
2. `A` (or a delegate/integrator constructing the transaction on `A`'s behalf) calls `transfer_to_new_account` (or `transfer_to_new_account_pda`) supplying `new_authority = Pubkey::default()`.
3. The instruction succeeds: `new_marginfi_account.authority = Pubkey::default()`, all balances are copied from `old_marginfi_account` into `new_marginfi_account`, and `old_marginfi_account` is disabled with its `lending_account` zeroed (`programs/marginfi/src/instructions/marginfi_account/transfer_account.rs:39-49, 91-99`).
4. No account can ever sign as `Pubkey::default()`, so `new_marginfi_account` and the funds it holds become permanently inaccessible.

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

**File:** programs/marginfi/src/instructions/marginfi_account/transfer_account.rs (L312-313)
```rust
    /// CHECK: WARN: New authority is completely unchecked
    pub new_authority: UncheckedAccount<'info>,
```

**File:** programs/marginfi/src/state/marginfi_account.rs (L84-104)
```rust
pub fn is_signer_authorized(
    marginfi_account: &MarginfiAccount,
    group_admin: Pubkey,
    signer: Pubkey,
    allow_receivership: bool,
    allow_order_execution: bool,
) -> bool {
    if allow_receivership && marginfi_account.get_flag(ACCOUNT_IN_RECEIVERSHIP) {
        return marginfi_account.authority != signer; // forbidden to take receivership of your own account
    }

    if allow_order_execution && marginfi_account.get_flag(ACCOUNT_IN_ORDER_EXECUTION) {
        return true;
    }

    if marginfi_account.get_flag(ACCOUNT_FROZEN) {
        return group_admin == signer;
    }

    marginfi_account.authority == signer
}
```

**File:** p0-cli/src/processor/account.rs (L1177-1181)
```rust
    new_authority: Pubkey,
) -> Result<()> {
    if new_authority == Pubkey::default() {
        bail!("Cannot transfer authority to the zero pubkey");
    }
```
