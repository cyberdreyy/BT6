## No vulnerability found for this question.

**Analysis:**

The `liquidity_vault_authority` account in `JuplendDeposit` is constrained by both a `seeds` clause tied to `bank.key()` and an explicit `bump = bank.load()?.liquidity_vault_authority_bump` check [1](#0-0) . Since `bank.key()` refers to the specific `bank` `AccountLoader` account passed into that particular instruction call, Anchor re-derives the PDA using that exact bank pubkey and the stored bump, and rejects any account that doesn't match. The signer seeds used in the CPI (`bank_signer!(BankVaultType::Liquidity, self.bank.key(), authority_bump)`) likewise use `self.bank.key()`, the loaded bank account, not an attacker-supplied key [2](#0-1) .

For two distinct banks (even sharing the same mint) to produce a colliding `liquidity_vault_authority`, `Pubkey::find_program_address` would need to yield an identical PDA for two different `bank.key()` values [3](#0-2) . Bank keys are themselves PDAs derived from `[group, bank_mint, bank_seed]` [4](#0-3) , so distinct `bank_seed` values (even attacker-chosen ones, since `bank_seed` is a `u64` argument) yield distinct bank pubkeys, and the subsequent authority PDA derivation depends on that unique bank pubkey as a seed component — this is a preimage/collision problem on SHA-256-derived PDAs, not a "bump collision" issue. The bump byte itself is not attacker-chosen; it's the canonical bump computed by `find_program_address` at bank-creation time and stored on-chain in `Bank.liquidity_vault_authority_bump`, then re-verified by the `bump = ...` constraint at every subsequent CPI-signing instruction.

Additionally, `lending_pool_add_bank_juplend` (which creates JupLend banks) requires `has_one = admin @ MarginfiError::Unauthorized`, meaning this instruction is not reachable by an unprivileged attacker at all [5](#0-4) . The premise that "bank_seed" is attacker-influenced permissionlessly does not hold for JupLend banks; only `lending_pool_add_bank_permissionless` (staked-collateral banks) is permissionless, and that pathway is unrelated to JupLend's `liquidity_vault_authority` PDA scheme.

Since (1) PDA derivation is cryptographically bound to the specific bank's pubkey, which is itself unique per `[group, mint, bank_seed]` combination, (2) the bump is canonical and re-verified on every use, and (3) bank creation for JupLend requires admin privileges, there is no reachable path for an unprivileged attacker to make one bank's `liquidity_vault_authority` sign for another bank's vault operations.

### Citations

**File:** programs/marginfi/src/instructions/juplend/deposit.rs (L180-188)
```rust
    #[account(
        mut,
        seeds = [
            LIQUIDITY_VAULT_AUTHORITY_SEED.as_bytes(),
            bank.key().as_ref(),
        ],
        bump = bank.load()?.liquidity_vault_authority_bump
    )]
    pub liquidity_vault_authority: SystemAccount<'info>,
```

**File:** programs/marginfi/src/instructions/juplend/deposit.rs (L303-304)
```rust
        let signer_seeds: &[&[&[u8]]] =
            bank_signer!(BankVaultType::Liquidity, self.bank.key(), authority_bump);
```

**File:** programs/marginfi/src/utils/general.rs (L43-45)
```rust
pub fn find_bank_vault_authority_pda(bank_pk: &Pubkey, vault_type: BankVaultType) -> (Pubkey, u8) {
    Pubkey::find_program_address(bank_authority_seed!(vault_type, bank_pk), &crate::ID)
}
```

**File:** programs/marginfi/src/instructions/juplend/add_pool.rs (L122-129)
```rust
pub struct LendingPoolAddBankJuplend<'info> {
    #[account(
        mut,
        has_one = admin @ MarginfiError::Unauthorized
    )]
    pub group: AccountLoader<'info, MarginfiGroup>,

    pub admin: Signer<'info>,
```

**File:** programs/marginfi/src/instructions/juplend/add_pool.rs (L141-147)
```rust
        seeds = [
            group.key().as_ref(),
            bank_mint.key().as_ref(),
            &bank_seed.to_le_bytes(),
        ],
        bump,
    )]
```
