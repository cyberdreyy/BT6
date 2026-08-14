### Title
Missing zero-address check on `new_authority` in `transfer_to_new_account`/`transfer_to_new_account_pda` permanently locks account funds — (File: `programs/marginfi/src/instructions/marginfi_account/transfer_account.rs`)

### Summary
The `TransferToNewAccount` and `TransferToNewAccountPda` instructions accept a `new_authority` account that is explicitly documented as unchecked and is never validated against `Pubkey::default()` (Solana's analog to `address(0)`). This mirrors the reported bug class where delegating/transferring control to the null address permanently destroys the ability to control/recover the underlying value.

### Finding Description
In `transfer_to_new_account` and `transfer_to_new_account_pda`, the `new_authority` field is declared with no on-chain constraint: [1](#0-0) 

The value is taken directly from the unchecked account and written as the new account's authority with no zero-check: [2](#0-1) 

`initialize_migrated_account` sets `new_account.authority = new_authority` unconditionally, and `finalize_migrated_old_account` disables the old account and marks it as migrated, with no way to reverse the migration: [3](#0-2) 

Authorization for subsequent operations (`deposit`, `withdraw`, `borrow`, `repay`, etc.) is gated by `is_signer_authorized`, which compares the transaction signer to `marginfi_account.authority`: [4](#0-3) 

Since `Pubkey::default()` has no corresponding private key, no signer can ever satisfy `marginfi_account.authority == signer` for the new account. Interestingly, the off-chain CLI helper does guard against this exact case (`if new_authority == Pubkey::default() { bail!(...) }`), confirming the team is aware zero-address is an invalid value — but this guard exists only client-side, not in the on-chain instruction: [5](#0-4) 

The same missing check exists in the PDA variant, where `new_authority` is also used unchecked as a signing seed and stored authority: [6](#0-5) 

### Impact Explanation
If a user (or any front-end/integration bug) calls `transfer_to_new_account`/`transfer_to_new_account_pda` with `new_authority = Pubkey::default()`:
- The old account is disabled (`ACCOUNT_DISABLED`) and its `lending_account` is zeroed out — all balances move to the new account.
- The new account is created with `authority = Pubkey::default()`, which no wallet can ever sign for.
- All standard user paths (deposit/withdraw/borrow/repay/close) become permanently unreachable by any normal signer, since `is_signer_authorized` requires `marginfi_account.authority == signer` outside of receivership/order-execution/frozen-admin-override paths.

The only theoretical recovery path is the group admin explicitly freezing the account via `SetAccountFreeze` and then operating on it as admin (since the frozen-path check compares against `group_admin`, not `account.authority`): [7](#0-6) 

This is not a self-service recovery — it requires manual, out-of-band admin intervention per affected account, and the funds are otherwise durably stranded. This matches the reported bug class: value directed to the null "address" becomes effectively frozen/lost from the normal permission model, with direct financial impact (locked deposits/positions).

### Likelihood Explanation
The instruction is user-callable and permissionless with respect to the choice of `new_authority` — the field is explicitly commented `WARN: New authority is completely unchecked`. Triggering this requires either user error (fat-fingering an address, integration bug passing an uninitialized/default pubkey) or a buggy front-end/SDK. Given the CLI tooling itself already had to add a defensive check for this exact value, this is a realistic, foreseeable failure mode rather than a purely theoretical one. However, it does require an incorrect authority parameter rather than being remotely exploitable by an attacker against a victim's account (the caller must supply `new_authority` themselves via their own signed transaction, or convince the account authority to sign such a transaction), which limits it to a self-inflicted/integration-risk severity rather than a direct fund-theft vector.

### Recommendation
Add an explicit on-chain check in both `transfer_to_new_account` and `transfer_to_new_account_pda` that rejects `new_authority == Pubkey::default()`, mirroring the guard already present in the CLI (`p0-cli/src/processor/account.rs`), e.g.:
```rust
check!(
    ctx.accounts.new_authority.key() != Pubkey::default(),
    MarginfiError::InvalidAuthority // or a new dedicated error
);
```
This should be added before `initialize_migrated_account` is called and before the old account is finalized/disabled, so that the migration fails atomically rather than stranding funds.

### Proof of Concept
1. User creates a `MarginfiAccount` with deposits/borrows via normal flow.
2. User (or a buggy integration) calls `transfer_to_new_account` with `new_authority = Pubkey::default()`.
3. Transaction succeeds: old account is disabled and `migrated_to` set; new account is created with `authority = Pubkey::default()` and holds the migrated `lending_account` state.
4. Any subsequent call to `deposit`/`withdraw`/`borrow`/`repay`/`close_account` with any signer fails `is_signer_authorized`'s `marginfi_account.authority == signer` check, because no private key exists for `Pubkey::default()`.
5. Funds remain locked in the new account, recoverable only via manual group-admin freeze + admin-operated withdrawal, which is outside normal user self-service and requires the admin to be alerted out-of-band.

### Citations

**File:** programs/marginfi/src/instructions/marginfi_account/transfer_account.rs (L23-49)
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

**File:** programs/marginfi/src/instructions/marginfi_account/transfer_account.rs (L93-99)
```rust
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

**File:** p0-cli/src/processor/account.rs (L1178-1181)
```rust
) -> Result<()> {
    if new_authority == Pubkey::default() {
        bail!("Cannot transfer authority to the zero pubkey");
    }
```

**File:** programs/marginfi/src/instructions/marginfi_account/freeze.rs (L1-19)
```rust
/// Admin-only instruction to toggle `ACCOUNT_FROZEN` on a marginfi account.
///
/// Behavior:
/// - When frozen, the account authority is blocked from major actions (borrow/deposit/withdraw/repay/transfer/etc.) with `AccountFrozen`.
/// - The group admin retains access to operate the account while frozen (for remediation/seizure).
/// - Setting `frozen = false` clears the flag and returns control to the authority under normal auth rules.
pub fn set_account_freeze(ctx: Context<SetAccountFreeze>, frozen: bool) -> MarginfiResult {
    let group = ctx.accounts.group.load()?;
    check_eq!(
        group.admin,
        ctx.accounts.admin.key(),
        MarginfiError::Unauthorized
    );
    let mut marginfi_account = ctx.accounts.marginfi_account.load_mut()?;
    if frozen {
        marginfi_account.set_flag(ACCOUNT_FROZEN, true);
    } else {
        marginfi_account.unset_flag(ACCOUNT_FROZEN, true);
    }
```
