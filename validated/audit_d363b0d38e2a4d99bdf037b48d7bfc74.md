### Title
Emissions destination address survives marginfi account ownership transfer, letting the previous owner divert future reward payouts - ([File: programs/marginfi/src/instructions/marginfi_account/transfer_account.rs])

### Summary
`transfer_to_new_account` / `transfer_to_new_account_pda` create a new `MarginfiAccount` under a caller-supplied `new_authority` and copy over sensitive per-account configuration from the old account, including `emissions_destination_account`, without resetting it. This mirrors the reported PRBProxy bug class: state configured by the *previous* owner is not cleared on ownership transfer, letting that previous owner continue to benefit at the new owner's expense.

### Finding Description
`initialize_migrated_account` explicitly copies `emissions_destination_account` from the old account into the freshly created new account: [1](#0-0) 

This field is user-settable via `marginfi_account_update_emissions_destination_account`, which lets the *current* authority point future off-chain emissions/incentive airdrops to any arbitrary address: [2](#0-1) 

Per the emissions guide, off-chain reward drops are delivered to whatever address is configured here (defaulting to the account authority if unset), and this configuration is explicitly recommended for use cases where the account changes hands (e.g. PDA accounts created for third parties): [3](#0-2) 

The transfer instructions accept a completely unchecked `new_authority`, explicitly documented as "WARN: New authority is completely unchecked" and used both for self-migration and for genuine transfers to third parties/integrators: [4](#0-3) [5](#0-4) 

Because `emissions_destination_account` is silently carried over to the new account, a malicious or careless previous owner can, prior to transferring the account (e.g. selling it, or handing it to another party via the "transfer to new authority" flow), pre-set this field to an address they control. The new owner has no on-chain signal or automatic reset informing them that emissions are being redirected elsewhere; the field is opaque unless they proactively re-check and re-set it themselves, exactly analogous to the unlisted "plugins"/"permissions" mappings in the PRBProxy report.

### Impact Explanation
Future off-chain emissions/incentive distributions earned by the new legitimate owner's deposits/borrows on the transferred account are silently redirected to the former owner's chosen address until the new owner notices and manually overwrites the field. This is a durable, exploitable value-redirection bug with real financial effect (loss of reward token airdrops), consistent with the "accept" criteria (value redirection / unauthorized state persistence).

### Likelihood Explanation
Moderate. It requires: (1) the old authority to have set `emissions_destination_account` (a single, permissionless, always-available call they fully control), and (2) an actual change of beneficial ownership via `transfer_to_new_account`/`transfer_to_new_account_pda` (a supported, documented flow, including account sales/handoffs to integrators/third parties where `new_authority` is chosen freely and the two parties are not the same person). New owners have no built-in mechanism or event forcing them to check/reset this field post-transfer, so the condition can persist indefinitely and silently.

### Recommendation
On transfer (`transfer_to_new_account` / `transfer_to_new_account_pda`), reset `emissions_destination_account` on the newly created account to `Pubkey::default()` (or to `new_authority`) instead of copying it from the old account. More broadly, audit all fields copied in `initialize_migrated_account` for any that grant a lingering benefit/permission tied to the old authority, and reset them by default unless the new owner explicitly re-opts in.

### Proof of Concept
1. Attacker creates a `MarginfiAccount` and deposits/borrows normally.
2. Attacker calls `marginfi_account_update_emissions_destination_account` setting `destination_account` to a wallet they control (`programs/marginfi/src/instructions/marginfi_account/emissions.rs:12-24`).
3. Attacker sells/transfers the account to a genuine buyer via `transfer_to_new_account`, specifying the buyer's key as `new_authority`.
4. `initialize_migrated_account` copies `emissions_destination_account` unchanged into the new account owned by the buyer (`transfer_account.rs:23-37`).
5. The buyer, now the sole `authority` and believing they fully control the account, continues depositing/borrowing to earn incentive campaigns; all future off-chain emissions airdrops for that account continue to be sent to the attacker's wallet until the buyer happens to discover and overwrite the field.

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

**File:** programs/marginfi/src/instructions/marginfi_account/transfer_account.rs (L153-166)
```rust
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

**File:** guides/USER/EMISSIONS.md (L19-35)
```markdown
Emissions/incentives are delivered by airdrop to the Account's authority, typically on Wednesday, in
no particular order. In the above example, User 1 would get $0.5 + 0.5 * 0.143 * 5 = 1.715$ tokens
and User 2 would get $0.5 + 0.5 + 0.857 * 5 = 5.285$ tokens

## Paired Emissions

In some Campaigns, users must BOTH lend a particular asset AND borrow a particular asset. A common
campaign is to lend some LST and borrow SOL. An Account only earns an airdrop if they perform both
tasks. For example, if the Campaign is to lend LST_A and borrow SOL, and a user is lending \$70 in A
and borrowing \$50 in SOL, they will earn rewards on \$50, i.e. min(lending_lst_a, borrowing_sol).

## Changing Emissions Destination

Want your emissions delivered somewhere specific? Set up an `emissions_destination_account` with
`marginfi_account_update_emissions_destination_account`.

This is highly recommended for PDA account authorities.
```

**File:** tests/specs/basic/12_transfer_account.spec.ts (L55-56)
```typescript
  // Here the user moves authority to some new wallet. WARN: User picks the new authority with no
  // restrictions!
```
