## Analysis

The reported bug class — a permissionless "create" instruction whose destination address is a PDA derived entirely from attacker-visible/attacker-choosable parameters (no binding to `msg.sender`), allowing a front-runner to squat that address and permanently DoS the legitimate transaction — has a direct analog in `transfer_to_new_account_pda`.

### Root cause

In `TransferToNewAccountPda`, the `new_marginfi_account` PDA is derived as: [1](#0-0) 

The seeds are `[MARGINFI_ACCOUNT_SEED, group, new_authority, account_index, third_party_id]`, and `new_authority` is explicitly documented as unchecked/unsigned: [2](#0-1) . The only signer-tied check is that the *caller* (`authority`) is authorized on `old_marginfi_account`: [3](#0-2) . Nothing ties the derived PDA to the caller's own identity — it depends only on the (`group`, `new_authority`, `account_index`, `third_party_id`) tuple, all of which are visible in the mempool once a legitimate `transfer_to_new_account_pda` transaction is submitted, and `third_party_id` restriction only applies when `Some` (via `is_allowed_cpi_for_third_party_id`) — leaving `None`/default fully unrestricted.

Because any account holder (even one who trivially created a fresh, empty `MarginfiAccount` via the permissionless `marginfi_account_initialize`/`marginfi_account_initialize_pda`) can act as `old_marginfi_account`'s authorized signer and supply an arbitrary `new_authority` + `account_index` + `third_party_id`, an attacker can observe a pending legitimate `transfer_to_new_account_pda` transaction and front-run it with the exact same `new_authority`/`account_index`/`third_party_id` tuple but their own `old_marginfi_account`. Since `new_marginfi_account` uses `init`, the account is created first by the attacker's transaction; the victim's transaction then fails with "already in use" when Anchor's `init` constraint fails on the already-occupied PDA — an exact parallel to the `createSplit()`/`salt` collision described in the report.

This differs from `MarginfiAccountInitializePda`, where the same seed pattern is safe because the seed includes `authority.key()` and `authority` must be the actual transaction signer — an attacker cannot forge a signature for someone else's authority pubkey, so that PDA cannot be squatted by a third party. `transfer_to_new_account_pda` is the vulnerable variant because it decouples the seed's `new_authority` from any required signature.

### Title
Permissionless `transfer_to_new_account_pda` PDA Derivation From Unsigned `new_authority` Allows Front-Running DoS - (File: `programs/marginfi/src/instructions/marginfi_account/transfer_account.rs`)

### Summary
`transfer_to_new_account_pda` derives the destination `new_marginfi_account` PDA from `(group, new_authority, account_index, third_party_id)`, none of which require a signature from `new_authority`. Any user who is authorized on some `old_marginfi_account` (trivially obtainable for free) can watch the mempool and front-run a legitimate migration by submitting the same parameters first, permanently occupying that PDA address and causing the victim's transaction to revert.

### Finding Description
The instruction's account constraint computes the PDA using `new_authority.key()` where `new_authority` is an `UncheckedAccount` that is never required to sign: [4](#0-3) . Authorization is only checked against the caller's ownership of `old_marginfi_account`: [3](#0-2) . Because Anchor's `init` requires the target PDA to be unoccupied, whoever creates it first for a given `(group, new_authority, account_index, third_party_id)` tuple wins, and all subsequent attempts with the same tuple permanently fail.

### Impact Explanation
This enables a denial-of-service/griefing attack: an attacker monitoring the mempool for `transfer_to_new_account_pda` calls can copy the visible `new_authority`, `account_index`, and `third_party_id` arguments and submit their own transaction (using a cheap, freely-creatable `old_marginfi_account` of their own) with higher priority fees to claim the PDA first. This permanently blocks the intended recipient (`new_authority`) from ever receiving an account migration at that specific `account_index`/`third_party_id`, forcing the legitimate user to retry with a different index or fall back to the non-deterministic `TransferToNewAccount` keypair-based path. This does not directly redirect funds (the transaction reverts atomically before `old_account` is disabled, so no fund loss occurs), but it is a durable, repeatable griefing/DoS vector against integrations or users relying on deterministic account migration addressing (e.g., third-party protocols expecting a specific PDA to exist for a specific `new_authority`).

### Likelihood Explanation
Likelihood is moderate: the attacker needs only to create a throwaway `MarginfiAccount` (permissionless, low-cost) to become an "authorized" caller, then race the legitimate transaction with a copied instruction and higher priority fee. This is realistically executable by any mempool-observing bot, mirroring the exact mechanism from the referenced report.

### Recommendation
Bind the PDA derivation (or an authorization check) to a value the intended `new_authority` actually controls/signs, e.g., require `new_authority` to co-sign, or include the calling `authority`/`old_marginfi_account` pubkey in the seed so the PDA address cannot be squatted by unrelated third parties before the legitimate transaction lands. Alternatively, detect the "already in use" collision and provide a safe re-derivation/refund path instead of an unrecoverable revert.

### Proof of Concept
1. Victim submits `transfer_to_new_account_pda(account_index=5, third_party_id=None)` with `new_authority = Alice`, migrating `old_marginfi_account_victim` → PDA derived from `[MARGINFI_ACCOUNT_SEED, group, Alice, 5, 0]`.
2. Attacker observes this pending transaction in the mempool, extracts `(group, Alice, 5, None)`.
3. Attacker creates a fresh, empty `MarginfiAccount` for themselves (`marginfi_account_initialize`), then submits their own `transfer_to_new_account_pda(account_index=5, third_party_id=None)` with `new_authority = Alice` and `old_marginfi_account = attacker's own account`, using a higher priority fee to land first.
4. The attacker's transaction succeeds, initializing the PDA at `[MARGINFI_ACCOUNT_SEED, group, Alice, 5, 0]` (with junk/empty state migrated from the attacker's dummy account).
5. The victim's original transaction then fails at the `init` constraint on `new_marginfi_account` (`programs/marginfi/src/instructions/marginfi_account/transfer_account.rs:292-305`) because the account already exists, permanently preventing Alice from receiving a migrated account at `account_index=5`/`third_party_id=None`.

### Citations

**File:** programs/marginfi/src/instructions/marginfi_account/transfer_account.rs (L277-290)
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
