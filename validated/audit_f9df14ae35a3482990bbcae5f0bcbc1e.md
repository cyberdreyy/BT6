Found a concrete analog: `initialize_migrated_account()` copies `emissions_destination_account` from the old account to the new account during `transfer_to_new_account`/`transfer_to_new_account_pda`, without resetting or clearing it for the new owner. [1](#0-0) 

### Title
Emissions destination account carries over on marginfi account authority transfer, allowing prior owner to redirect emissions - (File: programs/marginfi/src/instructions/marginfi_account/transfer_account.rs)

### Summary
`transfer_to_new_account` / `transfer_to_new_account_pda` are used to move a `MarginfiAccount`'s positions to a brand-new account under a new authority, intended as a full ownership transfer of the position [2](#0-1) . However, `initialize_migrated_account` unconditionally copies `old_account.emissions_destination_account` into the newly created account instead of resetting it to `Pubkey::default()` [3](#0-2) .

### Finding Description
`emissions_destination_account` is a user-settable pointer (set via `marginfi_account_update_emissions_destination_account`, callable only by the account's current `authority`) indicating the wallet whose canonical ATA should receive off-chain emissions distributions [4](#0-3) . This is directly analogous to the external report's "token allowances stay in effect on proxy ownership transfer" bug class: a setting configured by the *old* controller of an account/wallet persists after the account's ownership is handed to a *new* controller, and the new controller has no visibility into or control over it unless they proactively check and overwrite the field.

When a user calls `transfer_to_new_account`/`transfer_to_new_account_pda` to hand off their position to a new authority (e.g., selling/transferring their marginfi account, or as part of an integration/protocol-level migration where "new_authority" is a different entity), the resulting new account silently inherits the old authority's emissions destination [5](#0-4) . The new authority is not required or prompted to set `emissions_destination_account`, so unless they know to explicitly call `marginfi_account_update_emissions_destination_account` themselves, off-chain emissions continue to be routed to a wallet still controlled by the previous owner.

### Impact Explanation
Emissions distributions (a real value stream tied to the account/position) can continue to accrue to the prior owner even after the position and its risk have been fully transferred to a new authority, resulting in a durable, silent value-redirection until the new owner notices and updates the field. This is a genuine financial-effect issue matching the report's core concern (residual authorization/state controlled by a former owner persisting invisibly across an ownership-transfer instruction), though the blast radius is narrower than the original ERC20-allowance case since it's limited to the emissions off-chain distribution mechanism rather than direct on-chain fund transfer.

### Likelihood Explanation
Likely to occur in practice for any account transfer where the new authority does not already know about or actively check `emissions_destination_account`, since `transfer_to_new_account`/`transfer_to_new_account_pda` are explicitly documented, ordinary user-facing flows for authority migration [6](#0-5) , and nothing in the instruction or its accounts warns about or resets this field.

### Recommendation
Reset `new_account.emissions_destination_account` to `Pubkey::default()` in `initialize_migrated_account` during migration (matching how other transient/owner-specific fields would be expected to reset on ownership change), and require the new authority to explicitly opt in via `marginfi_account_update_emissions_destination_account` if they want emissions routed elsewhere.

### Proof of Concept
1. User A creates a `MarginfiAccount`, deposits, and calls `marginfi_account_update_emissions_destination_account` to set `emissions_destination_account` to a wallet A controls [7](#0-6) .
2. User A transfers the account to User B via `transfer_to_new_account`, setting `new_authority = B` [8](#0-7) .
3. Inspect the new account: `emissions_destination_account` still equals A's wallet, because `initialize_migrated_account` copied it verbatim from the old account [3](#0-2) .
4. Off-chain emissions distribution processes continue crediting A's wallet for B's ongoing position activity until B discovers and overwrites the field.

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

**File:** programs/marginfi/src/lib.rs (L496-500)
```rust
    /// (account authority) Transfer all positions to a new account under a new authority. The old
    /// account is disabled. Pays a flat SOL fee to the protocol.
    pub fn transfer_to_new_account(ctx: Context<TransferToNewAccount>) -> MarginfiResult {
        marginfi_account::transfer_to_new_account(ctx)
    }
```

**File:** programs/marginfi/src/lib.rs (L502-516)
```rust
    /// (account authority) Same as `transfer_to_new_account` except the resulting account is a PDA
    ///
    /// seeds:
    /// - marginfi_group
    /// - authority: The account authority (owner)  
    /// - account_index: A u16 value to allow multiple accounts per authority
    /// - third_party_id: Optional u16 for third-party tagging. Seeds < PDA_FREE_THRESHOLD can be
    ///   used freely. For a dedicated seed used by just your program (via CPI), contact us.
    pub fn transfer_to_new_account_pda(
        ctx: Context<TransferToNewAccountPda>,
        account_index: u16,
        third_party_id: Option<u16>,
    ) -> MarginfiResult {
        marginfi_account::transfer_to_new_account_pda(ctx, account_index, third_party_id)
    }
```

**File:** programs/marginfi/src/instructions/marginfi_account/emissions.rs (L10-24)
```rust
/// (account authority) Set the wallet whose canonical ATA will receive
/// off-chain emissions distributions.
pub fn marginfi_account_update_emissions_destination_account(
    ctx: Context<MarginfiAccountUpdateEmissionsDestinationAccount>,
) -> MarginfiResult {
    let mut marginfi_account = ctx.accounts.marginfi_account.load_mut()?;

    check!(
        !marginfi_account.get_flag(ACCOUNT_FROZEN),
        MarginfiError::AccountFrozen
    );

    marginfi_account.emissions_destination_account = ctx.accounts.destination_account.key();
    Ok(())
}
```
