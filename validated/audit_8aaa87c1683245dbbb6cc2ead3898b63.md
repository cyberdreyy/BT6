Based on the code in `programs/marginfi/src/instructions/marginfi_group/config_bank_emode.rs`, the accounts context enforces:

- `group.has_one = emode_admin` — checked by Anchor during account validation.
- `bank.has_one = group` — checked by Anchor during account validation.

Both `has_one` constraints are validated by Anchor's account-deserialization/`Accounts::try_accounts` phase, **before** the `lending_pool_configure_bank_emode` handler body ever executes. This means no protected field on `bank.emode` (metadata, tag, entries, pause/enabled state) can be mutated unless:
1. The `emode_admin` signer matches the exact pubkey stored in the target `group`'s `emode_admin` field, and
2. The `bank` account's `group` field matches the passed-in `group` pubkey. [1](#0-0) 

An attacker with "candidate groups from another environment sharing delegate structure" would need a `group` account whose stored `emode_admin` equals their own signer key. If the attacker supplies their *own* group (which they legitimately administer), the `bank.has_one = group` constraint blocks pairing it with a target bank belonging to a different group — that bank's `group` field won't match. Conversely, if they supply the real target group to satisfy the bank binding, the `has_one = emode_admin` constraint fails because they don't control the legitimate `emode_admin` key. There is no code path in which `bank.emode.emode_tag`, `emode_config.entries`, or `timestamp` are written before these constraint checks resolve — Anchor's constraint checking happens strictly prior to entering the function body, so there's no "late binding" or partial-mutation-then-rollback scenario to exploit; the handler is simply never invoked on constraint failure. [2](#0-1) 

No exploitable bypass exists: signer/ownership/group-binding guards fully prevent reaching the mutation logic without the correct `emode_admin` authority bound to the correct group/bank pair.

### No Vulnerability found for this question.

### Citations

**File:** programs/marginfi/src/instructions/marginfi_group/config_bank_emode.rs (L7-33)
```rust
pub fn lending_pool_configure_bank_emode(
    ctx: Context<LendingPoolConfigureBankEmode>,
    emode_tag: u16,
    entries: [EmodeEntry; MAX_EMODE_ENTRIES],
) -> MarginfiResult {
    let mut bank = ctx.accounts.bank.load_mut()?;
    let group = ctx.accounts.group.load()?;

    let mut sorted_entries = entries;
    sorted_entries.sort_by_key(|e| e.collateral_bank_emode_tag);

    // Prevent footguns from passing data in padding, which could interfere with future values in
    // that assumed-empty space. Yes, we could simply take a struct without padding as input, but
    // having a separate config type has proved to be more of a pain than dealing with padding.
    for entry in sorted_entries.iter_mut() {
        entry.pad0 = [0; 5];
    }

    bank.emode.emode_tag = emode_tag;
    bank.emode.emode_config.entries = sorted_entries;
    bank.emode.timestamp = Clock::get()?.unix_timestamp;

    bank.emode.validate_entries_with_liability_weights(
        &bank.config,
        group.emode_max_init_leverage,
        group.emode_max_maint_leverage,
    )?;
```

**File:** programs/marginfi/src/instructions/marginfi_group/config_bank_emode.rs (L52-66)
```rust
#[derive(Accounts)]
pub struct LendingPoolConfigureBankEmode<'info> {
    #[account(
        has_one = emode_admin @ MarginfiError::Unauthorized
    )]
    pub group: AccountLoader<'info, MarginfiGroup>,

    pub emode_admin: Signer<'info>,

    #[account(
        mut,
        has_one = group @ MarginfiError::InvalidGroup,
    )]
    pub bank: AccountLoader<'info, Bank>,
}
```
