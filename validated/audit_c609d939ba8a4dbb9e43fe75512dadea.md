### Title
Emissions destination set by a former account authority silently redirects future emissions rewards after `TransferToNewAccount`/`TransferToNewAccountPda` — ([File: programs/marginfi/src/instructions/marginfi_account/transfer_account.rs])

### Summary
Analogous to the OnChainLab report — where seller-installed fallback state survives an NFT ownership transfer and is inherited (and abused) under the new owner's context — marginfi's account-migration path copies the `emissions_destination_account` field (a value-redirection pointer) from the old `MarginfiAccount` into the freshly created `MarginfiAccount`, with no reset and no signal to the new authority that this field has been pre-configured by a party other than themselves.

### Finding Description
`transfer_to_new_account` and `transfer_to_new_account_pda` both call `initialize_migrated_account`, which explicitly copies `emissions_destination_account` from the old account into the new one: [1](#0-0) 

`emissions_destination_account` is a user-settable pointer used by off-chain systems to determine where a wallet's canonical ATA for weekly emissions airdrops is sent, and can be set unilaterally by the current authority via `marginfi_account_update_emissions_destination_account`: [2](#0-1) 

There is no ownership/authorization binding recorded for who set this field, and it is not reset when the account is migrated to a new `new_authority` (an entirely unchecked `Pubkey` supplied by the *old* authority): [3](#0-2) 

The type documents this as an intentionally persistent field with no owner-scoping semantics: [4](#0-3) 

This mirrors the OnChainLab root cause precisely: a piece of value-redirecting configuration, set unilaterally by the pre-transfer controller, is carried over into the post-transfer account/authority context without being cleared or requiring re-authorization by the new controller — the new owner inherits state they never configured or consented to, and that state continues to steer value away from them.

Any workflow where a marginfi account (or PDA-based account, potentially created and configured as part of an integrator flow, then handed off to an end-user via `new_authority`) is transferred is affected. The old authority can pre-set `emissions_destination_account` to their own wallet before initiating the transfer, and every subsequent emissions airdrop earned by the new owner's post-transfer activity on that account continues to be routed to the old authority's wallet, silently, until the new owner happens to notice and manually resets it with their own transaction.

### Impact Explanation
This is a genuine, concrete value-redirection bug with financial effect: emissions/incentive rewards accrued by the new account authority's real economic activity (deposits/borrows) are diverted to a third party (the previous authority) with no on-chain signal to the new owner. Unlike the OnChainLab case, this does not enable an outright drain of principal/collateral (no delegatecall-equivalent exists in marginfi's account model, and balances/shares themselves are not attacker-redirectable this way), so the blast radius is limited to *emissions rewards*, not the account's full value. Still, this satisfies the "value redirection ... with financial effect" bar: the loss is real, recurring (weekly, per `EMISSIONS.md`), and requires no cooperation or awareness from the victim to occur.

### Likelihood Explanation
Likelihood is moderate: `transfer_to_new_account`/`transfer_to_new_account_pda` are the only sanctioned mechanisms for handing off a marginfi account's control to a different authority/wallet, and any integrator or user-facing flow (e.g., "pre-warmed account" transfer/sale patterns, wallet migration services) that lets one party configure an account before designating `new_authority` is exposed. It does not require any privileged role — the transferring `authority` is a normal, unprivileged account owner, and `new_authority` is unchecked. The main mitigating factor is that the field is visible on-chain (`emissions_destination_account`) and could in principle be checked before/after a transfer, but nothing in the current flow surfaces this to the new owner, and `ACCOUNT_LIFECYCLE.md`/`EMISSIONS.md` do not mention that this value persists across migration.

### Recommendation
On `transfer_to_new_account`/`transfer_to_new_account_pda`, reset `emissions_destination_account` to `Pubkey::default()` on the newly created account rather than copying it from the old account (mirroring how `lending_account`, and other stateful fields, are explicitly zeroed/reset for the old account in `finalize_migrated_old_account`). If preserving destination continuity is desired for legitimate self-migrations (same authority, `transfer_to_new_account` with unchanged `new_authority == authority`), only copy the field when `new_authority == old_account.authority`; otherwise force the new owner to explicitly opt in via `marginfi_account_update_emissions_destination_account`.

### Proof of Concept
1. User A creates a `MarginfiAccount`, deposits, and calls `marginfi_account_update_emissions_destination_account` to set `emissions_destination_account = attacker_wallet` (a wallet A controls). [5](#0-4) 
2. User A transfers the account to `new_authority = B` (e.g., as part of a sale/handoff of a pre-configured account) via `transfer_to_new_account`/`transfer_to_new_account_pda`. [6](#0-5) 
3. `initialize_migrated_account` copies `emissions_destination_account` unchanged into B's new account. [7](#0-6) 
4. B, believing they now fully own and control the account, deposits/borrows and accrues emissions. Off-chain distribution logic sends B's earned emissions to A's `attacker_wallet`, per `EMISSIONS.md`'s documented behavior of routing to `emissions_destination_account`. [8](#0-7) 
5. B receives no on-chain indication that this field was pre-set by A; B only discovers the misdirection after failing to receive expected airdrops, and must proactively call `marginfi_account_update_emissions_destination_account` to reclaim future rewards — past rewards are unrecoverable.

Note: I was unable to find any explicit exclusion of this scenario in `SECURITY.md`, and confirmed the transfer path is reachable by unprivileged users (`authority` is a plain `Signer`, `new_authority` is fully unchecked). This is a narrower-impact analog than the original report (limited to emissions rewards rather than full account value), which should be weighed accordingly.

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

**File:** programs/marginfi/src/instructions/marginfi_account/transfer_account.rs (L155-166)
```rust
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

**File:** programs/marginfi/src/instructions/marginfi_account/transfer_account.rs (L182-265)
```rust
pub fn transfer_to_new_account_pda(
    ctx: Context<TransferToNewAccountPda>,
    account_index: u16,
    third_party_id: Option<u16>,
) -> MarginfiResult {
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

    // Validate third-party id restriction if provided
    if let Some(id) = third_party_id {
        if !is_allowed_cpi_for_third_party_id(&ctx.accounts.instructions_sysvar, id)? {
            return err!(MarginfiError::Unauthorized);
        }
    }

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

    finalize_migrated_old_account(
        &mut old_account,
        ctx.accounts.new_marginfi_account.key(),
        current_timestamp,
    );

    emit!(MarginfiAccountTransferToNewAccount {
        header: AccountEventHeader {
            signer: Some(ctx.accounts.authority.key()),
            marginfi_account: ctx.accounts.new_marginfi_account.key(),
            marginfi_account_authority: ctx.accounts.new_authority.key(),
            marginfi_group: ctx.accounts.group.key(),
        },
        old_account: ctx.accounts.old_marginfi_account.key(),
        old_account_authority: ctx.accounts.authority.key(),
        new_account_authority: ctx.accounts.new_authority.key(),
    });

    Ok(())
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

**File:** type-crate/src/types/user_account.rs (L49-50)
```rust
    /// Wallet whose canonical ATA receives off-chain emissions distributions.
    pub emissions_destination_account: Pubkey, // 32
```

**File:** programs/marginfi/tests/user_actions/emissions.rs (L178-206)
```rust

```

**File:** guides/USER/EMISSIONS.md (L30-35)
```markdown
## Changing Emissions Destination

Want your emissions delivered somewhere specific? Set up an `emissions_destination_account` with
`marginfi_account_update_emissions_destination_account`.

This is highly recommended for PDA account authorities.
```
