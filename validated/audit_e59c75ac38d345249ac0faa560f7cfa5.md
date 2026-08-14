## Title
Single-step, unverified account-authority migration allows irreversible loss of control over a MarginfiAccount — (File: `programs/marginfi/src/instructions/marginfi_account/transfer_account.rs`)

### Summary
The Beanstalk report describes a one-step `transferOwnership()` that immediately and irrevocably reassigns a privileged role, with no acceptance step from the new owner, creating risk of permanent misconfiguration from a bad address. The unprivileged-user analog in marginfi-v2 is the `TransferToNewAccount` / `TransferToNewAccountPda` instructions, which let a `MarginfiAccount`'s current `authority` migrate the account (and all its lending positions) to a caller-supplied `new_authority` in a single atomic transaction, with the new authority never signing or confirming and its correctness never validated on-chain.

### Finding Description
`transfer_to_new_account` and `transfer_to_new_account_pda` accept a `new_authority` account typed as `UncheckedAccount`, explicitly annotated `/// CHECK: WARN: New authority is completely unchecked`, and use it directly to initialize the new `MarginfiAccount`'s authority field via `initialize_migrated_account`: [1](#0-0) 

The migration is finalized irrevocably in the same instruction: the old account is permanently disabled, its lending balances are zeroed, and it is marked as migrated, with no acceptance/confirmation transaction required from `new_authority`: [2](#0-1) 

There is no re-entry path: once `migrated_to` is set, the old account can never be transferred again (`AccountAlreadyMigrated` check) [3](#0-2) , and the new authority is never required to prove control of the destination key by signing anything — only the *old* `authority` signs: [4](#0-3) 

This mirrors exactly the Beanstalk bug class: a single privileged (here, self-privileged over one's own account) call permanently reassigns control to an address with zero verification that the address is correct or reachable, and no two-step "propose/accept" pattern exists to catch mistakes before they become permanent.

### Impact Explanation
If the caller supplies an incorrect `new_authority` (typo, wrong keypair, malicious front-end substitution, or an address they do not actually control), the transaction still succeeds because there is no on-chain check tying `new_authority` to any signature or acceptance. The old account is immediately and permanently disabled (`ACCOUNT_DISABLED`, `lending_account` zeroed, `migrated_to` set) and can never be migrated again. All lending positions (deposits and borrows) are moved into a new account now controlled exclusively by the (possibly wrong/unreachable) `new_authority`. This is a durable, unrecoverable loss of control over user funds/positions with direct financial effect — the CLI helper even guards against the trivial zero-pubkey case (`marginfi_account_transfer` in `p0-cli`) but the on-chain instruction itself performs no such validation: [5](#0-4) 

### Likelihood Explanation
This is triggered by the account owner's own normal usage of a documented, permissionless instruction (`transferAccountAuthorityIx` / `transferAccountAuthorityPdaIx`), not by an attacker exploiting a privileged role. The likelihood of a mistaken address (typo, clipboard error, compromised/incorrect front-end value) is realistic for any wallet-address-entry workflow, and the protocol's own test comments acknowledge the risk ("WARN: User picks the new authority with no restrictions!"): [6](#0-5) 

### Recommendation
Implement a two-step authority migration: the current authority proposes `new_authority` (stored pending, e.g., `pending_authority` field) and the migration only finalizes when `new_authority` itself signs a follow-up "accept" instruction, proving control of the destination key before the old account is disabled and positions moved. Alternatively, require `new_authority` to co-sign the initial `TransferToNewAccount`/`TransferToNewAccountPda` instruction so the on-chain program can verify the destination key is genuinely controlled by a live signer before the migration is finalized.

### Proof of Concept
1. User A calls `transfer_to_new_account` (or the PDA variant) with `authority = A`, providing a mistyped/incorrect `new_authority = B'` (not the intended `B`).
2. Instruction succeeds: no signature or on-chain proof of control is required from `B'`. [7](#0-6) 
3. Old account is immediately disabled and zeroed, and `migrated_to` is permanently set, preventing any retry: [8](#0-7) [3](#0-2) 
4. All deposited/borrowed positions are now controlled exclusively by `B'`; if `B'` is unreachable or not actually held by anyone the user controls, the funds are permanently inaccessible with no recovery path.

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

**File:** programs/marginfi/src/instructions/marginfi_account/transfer_account.rs (L51-99)
```rust
pub fn transfer_to_new_account(ctx: Context<TransferToNewAccount>) -> MarginfiResult {
    // Validate the global fee wallet and claim a nominal fee
    let group = ctx.accounts.group.load()?;
    check_eq!(
        ctx.accounts.global_fee_wallet.key(),
        group.fee_state_cache.global_fee_wallet,
        MarginfiError::InvalidFeeAta
    );
    anchor_lang::system_program::transfer(ctx.accounts.transfer_fee(), ACCOUNT_TRANSFER_FEE)?;

    let mut old_account = ctx.accounts.old_marginfi_account.load_mut()?;

    check!(
        !old_account.get_flag(ACCOUNT_IN_FLASHLOAN),
        MarginfiError::AccountInFlashloan
    );

    check!(
        !old_account.get_flag(ACCOUNT_IN_RECEIVERSHIP),
        MarginfiError::ForbiddenIx
    );

    check!(
        !old_account.get_flag(ACCOUNT_IN_ORDER_EXECUTION),
        MarginfiError::ForbiddenIx
    );

    check!(
        old_account.active_orders == 0,
        MarginfiError::IllegalAction,
        "Close all active orders before transfer"
    );

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

**File:** programs/marginfi/src/instructions/marginfi_account/transfer_account.rs (L101-105)
```rust
    finalize_migrated_old_account(
        &mut old_account,
        ctx.accounts.new_marginfi_account.key(),
        current_timestamp,
    );
```

**File:** programs/marginfi/src/instructions/marginfi_account/transfer_account.rs (L131-153)
```rust
    #[account(
        mut,
        has_one = group @ MarginfiError::InvalidGroup,
        constraint = {
            let a = old_marginfi_account.load()?;
            account_not_frozen_for_authority(&a, authority.key())
        } @ MarginfiError::AccountFrozen,
        constraint = {
            let a = old_marginfi_account.load()?;
            let g = group.load()?;
            is_signer_authorized(&a, g.admin, authority.key(), false, false)
        } @ MarginfiError::Unauthorized
    )]
    pub old_marginfi_account: AccountLoader<'info, MarginfiAccount>,

    #[account(
        init,
        payer = fee_payer,
        space = 8 + std::mem::size_of::<MarginfiAccount>()
    )]
    pub new_marginfi_account: AccountLoader<'info, MarginfiAccount>,

    pub authority: Signer<'info>,
```

**File:** programs/marginfi/src/instructions/marginfi_account/transfer_account.rs (L155-159)
```rust
    #[account(mut)]
    pub fee_payer: Signer<'info>,

    /// CHECK: WARN: New authority is completely unchecked
    pub new_authority: UncheckedAccount<'info>,
```

**File:** p0-cli/src/processor/account.rs (L1174-1182)
```rust
pub fn marginfi_account_transfer(
    profile: &Profile,
    config: &Config,
    new_authority: Pubkey,
) -> Result<()> {
    if new_authority == Pubkey::default() {
        bail!("Cannot transfer authority to the zero pubkey");
    }
    let authority = config.authority();
```

**File:** tests/specs/basic/12_transfer_account.spec.ts (L55-56)
```typescript
  // Here the user moves authority to some new wallet. WARN: User picks the new authority with no
  // restrictions!
```
