### Title
User-controlled `new_authority` in `transfer_to_new_account`/`transfer_to_new_account_pda` is completely unchecked, allowing permanent loss of account access - (File: programs/marginfi/src/instructions/marginfi_account/transfer_account.rs)

### Summary
The PoolTogether finding centers on a user-facing "reset/change delegate" flow that accepts an address parameter with zero validation, allowing the default/sentinel `address(0)` value to be written into a critical ownership mapping, irreversibly orphaning the user's funds. Marginfi's `transfer_to_new_account` / `transfer_to_new_account_pda` instructions have the same shape of bug: the user-supplied `new_authority` account is explicitly documented as unchecked and is written directly into the new account's `authority` field with no validation against `Pubkey::default()`.

### Finding Description
`transfer_to_new_account` and `transfer_to_new_account_pda` let an account's current authority (an unprivileged user) migrate all lending positions to a brand-new `MarginfiAccount` under a caller-specified `new_authority`: [1](#0-0) 

The account struct explicitly flags this as unchecked: `/// CHECK: WARN: New authority is completely unchecked` `pub new_authority: UncheckedAccount<'info>,` with no `constraint`, `address`, or zero-check attached. [2](#0-1) 

That raw pubkey is then written straight into the new account's `authority` field via `initialize_migrated_account`: [3](#0-2) 

Immediately afterward, the old account is permanently disabled and marked as migrated to the new account, with no rollback path (`migrated_to` check prevents re-migration): [4](#0-3) [5](#0-4) 

Notably, the CLI tooling (`p0-cli`) independently recognizes this exact hazard and defensively guards against it client-side: `if new_authority == Pubkey::default() { bail!("Cannot transfer authority to the zero pubkey"); }` [6](#0-5) 

However, this check exists only in the off-chain CLI, not in the on-chain program. Any direct caller of the instruction (via a different client, a script, a bug in another wallet/integration UI, or simple user error selecting no/empty pubkey) can pass `Pubkey::default()` as `new_authority` directly to the program with no enforcement.

### Impact Explanation
If `new_authority` is `Pubkey::default()` (or any address the caller does not control/has no private key for), the resulting new `MarginfiAccount` becomes permanently unusable: no one can sign as its `authority` to withdraw, repay, close balances, or otherwise interact with it, since `is_signer_authorized` gates nearly all user-facing instructions on matching the account's `authority` signer (deposit, withdraw, borrow, repay, liquidate flows, etc., as referenced throughout `programs/marginfi/src/instructions/marginfi_account/*.rs`). Simultaneously, the old account is disabled (`ACCOUNT_DISABLED`) and its `migrated_to` field is permanently set, blocking any second migration attempt — mirroring the exact "can't recover, can't re-delegate" dead-end described in the PoolTogether report. All positions/collateral moved into the new account are durably frozen with no recovery path exposed in the reviewed code (no admin "reclaim" or "un-migrate" instruction was found). This is a direct, financially significant, permanent loss-of-funds/freeze bug reachable by an ordinary unprivileged user in a single transaction.

### Likelihood Explanation
The instruction is fully permissionless for the account's own authority and requires no validator/admin privilege — the account owner alone triggers it, exactly matching the PoolTogether analog of an unprivileged user resetting their own delegation. The bug is latent unless the program itself is called directly with a zero/incorrect `new_authority` (bypassing any UI/CLI safety checks), so likelihood is moderate: it requires either integrator error, a compromised/incomplete front-end, or user copy-paste error providing an all-zero or otherwise unowned pubkey. The fact that the team already added this exact guard in `p0-cli` demonstrates they are aware of the risk class, but the fix was applied only off-chain rather than as an on-chain invariant.

### Recommendation
Add an on-chain constraint rejecting `new_authority == Pubkey::default()` (and optionally reject `new_authority == old authority` in a way that would be a no-op or reject known-unspendable addresses) in both `TransferToNewAccount` and `TransferToNewAccountPda` account validation structs or at the top of `transfer_to_new_account`/`transfer_to_new_account_pda`, mirroring the check already present in `p0-cli/src/processor/account.rs`. This should be enforced identically to how the CLI already bails on the zero pubkey, e.g. `require_keys_neq!(ctx.accounts.new_authority.key(), Pubkey::default(), MarginfiError::Unauthorized);` before any account state mutation occurs.

### Proof of Concept
1. User creates a `MarginfiAccount` and deposits collateral under `authority = alice`.
2. Alice calls `transfer_to_new_account` (or `transfer_to_new_account_pda`) directly against the on-chain program (bypassing the `p0-cli` safety check, e.g. via a raw RPC call, another client, or a buggy front-end) with `new_authority = Pubkey::default()`. [7](#0-6) 
3. The instruction succeeds: `old_account` becomes `ACCOUNT_DISABLED` with `migrated_to` set to the new account key; `new_account.authority` is set to `Pubkey::default()`, and all of `old_account.lending_account` (positions/collateral) is copied into `new_account`.
4. Alice (or anyone) attempts to interact with the new account — deposit, withdraw, repay, close balance — all such instructions require a signer matching `new_account.authority` via `is_signer_authorized`, but no wallet exists that can sign for `Pubkey::default()`.
5. Alice also cannot retry `transfer_to_new_account` on the old account because `check_eq!(old_account.migrated_to, Pubkey::default(), MarginfiError::AccountAlreadyMigrated)` now fails since `migrated_to` is already set. [8](#0-7) 
6. Result: all collateral/positions are permanently frozen and unrecoverable, matching the "funds lost forever" outcome of the referenced PoolTogether finding.

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

**File:** programs/marginfi/src/instructions/marginfi_account/transfer_account.rs (L51-105)
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

    finalize_migrated_old_account(
        &mut old_account,
        ctx.accounts.new_marginfi_account.key(),
        current_timestamp,
    );
```

**File:** programs/marginfi/src/instructions/marginfi_account/transfer_account.rs (L146-166)
```rust
    #[account(
        init,
        payer = fee_payer,
        space = 8 + std::mem::size_of::<MarginfiAccount>()
    )]
    pub new_marginfi_account: AccountLoader<'info, MarginfiAccount>,

    pub authority: Signer<'info>,

    #[account(mut)]
    pub fee_payer: Signer<'info>,

    /// CHECK: WARN: New authority is completely unchecked
    pub new_authority: UncheckedAccount<'info>,

    /// CHECK: Validated against group fee state cache
    #[account(mut)]
    pub global_fee_wallet: UncheckedAccount<'info>,

    pub system_program: Program<'info, System>,
}
```

**File:** p0-cli/src/processor/account.rs (L1178-1181)
```rust
) -> Result<()> {
    if new_authority == Pubkey::default() {
        bail!("Cannot transfer authority to the zero pubkey");
    }
```
