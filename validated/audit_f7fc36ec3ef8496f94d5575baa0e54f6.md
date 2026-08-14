Based on the code review of `programs/marginfi/src/instructions/kamino/init_obligation.rs`, this instruction is not exploitable as described:

**Key protections that block the described attack:**

1. `bank.integration_acc_1` (Kamino reserve) and `bank.integration_acc_2` (Kamino obligation) are set exactly once at bank creation in `lending_pool_add_bank_kamino`, gated by `has_one = admin`, i.e. admin-only, not attacker-reachable. [1](#0-0) 

2. `KaminoInitObligation` enforces `has_one = integration_acc_1` and `has_one = integration_acc_2` against those already-fixed bank fields, so an attacker cannot substitute a different reserve/obligation account. [2](#0-1) 

3. The obligation account (`integration_acc_2`) is constrained by `seeds`/`bump` derived from `liquidity_vault_authority` (a canonical PDA of the bank, with bump stored in the immutable `bank.liquidity_vault_authority_bump`) and the passed `lending_market`. Because the seeds must reproduce the exact key already fixed via `has_one`, an attacker cannot supply an alternate `lending_market` and still pass validation — any substitution would derive a different address and fail the seeds check.
<invoke name="grep_search">
<parameter name="pattern">placeholder</parameter>
</invoke>

### Citations

**File:** programs/marginfi/src/instructions/kamino/add_pool.rs (L86-87)
```rust
    bank.integration_acc_1 = reserve_key;
    bank.integration_acc_2 = obligation_key;
```

**File:** programs/marginfi/src/instructions/kamino/init_obligation.rs (L60-68)
```rust
    #[account(
        has_one = liquidity_vault @ MarginfiError::InvalidLiquidityVault,
        has_one = integration_acc_1 @ MarginfiError::InvalidKaminoReserve,
        has_one = integration_acc_2 @ MarginfiError::InvalidKaminoObligation,
        has_one = mint @ MarginfiError::InvalidMint,
        constraint = is_kamino_asset_tag(bank.load()?.config.asset_tag)
            @ MarginfiError::WrongAssetTagForKaminoInstructions
    )]
    pub bank: AccountLoader<'info, Bank>,
```
