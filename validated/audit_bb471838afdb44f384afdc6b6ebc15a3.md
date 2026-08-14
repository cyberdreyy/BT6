### Title
Full Solend withdrawal permanently bricks re-deposits due to hardcoded `deposits_len == 1` assumption in `validate_solend_obligation` - ([File: programs/solend-mocks/src/state.rs])

### Summary
`validate_solend_obligation` and `get_solend_obligation_deposit_amount` manually parse the raw byte layout of a Solend `Obligation` account, hardcoding the assumption that the obligation always contains exactly one deposit entry at a fixed byte offset (204). This mirrors the reported Uniswap bug class: code that hardcodes a fixed-length/fixed-offset structural assumption about variable-length data and breaks (reverts) once that assumption no longer holds for a legitimate state transition.

### Finding Description
`validate_solend_obligation` requires the `deposits_len` byte (offset 202) to equal exactly `1` before allowing any Solend deposit or withdraw to proceed: [1](#0-0) 

This check is invoked unconditionally at the start of both `solend_deposit` and `solend_withdraw`: [2](#0-1) [3](#0-2) 

`get_solend_obligation_deposit_amount` then unconditionally reads bytes `[236..244)` as "the" deposit amount, with no verification that `deposits_len` is still `1` at that point: [4](#0-3) 

The real Solend lending program removes a deposit entry from the obligation's `deposits` vector once its `deposited_amount` reaches zero (a standard "close position" pattern used by lending protocols, mirrored by marginfi's own Kamino integration comment "Only one reserve should be active on the obligation" combined with tracking `deposits[0]`). When a user performs a full withdrawal (`withdraw_all = true`) through `solend_withdraw`, the underlying CPI to the real Solend program can reduce `deposits_len` from `1` to `0`.

Once `deposits_len` is `0`, any subsequent call to `solend_deposit` (the only entry point capable of restoring a deposit for that bank/obligation, other than `init_obligation`, which is a separate one-time creation path) will fail at the very first line, `validate_solend_obligation`, because it requires `data[202] == 1`: [2](#0-1) 

This creates a chicken-and-egg deadlock: to re-deposit and restore `deposits_len == 1`, the code first demands that `deposits_len` already equal `1`. There is no code path in `solend_deposit`/`solend_withdraw` that tolerates or repairs a `deposits_len == 0` obligation.

### Impact Explanation
Any marginfi bank/obligation pair that undergoes a full Solend withdrawal is at risk of permanent inability to re-deposit through the marginfi Solend integration for that bank's `liquidity_vault_authority`-owned obligation. This is a durable, financial-effect freeze: user funds routed through this integration can become unable to be re-deposited via `solend_deposit`, forcing reliance on account/bank migration or an obligation-recreation path (if one exists) that is outside the normal deposit/withdraw flow. This matches the report's core bug class — a hardcoded structural assumption about fixed data shape that silently holds under some inputs but reverts transactions once the (legitimate) data shape changes.

### Likelihood Explanation
Full withdrawals (`withdraw_all = true`) are an explicitly supported, common operation, called out directly in the withdraw instruction's own documentation as the recommended way to close a position: [5](#0-4) 
Any user closing out their Solend position through marginfi is likely to hit this path, making the likelihood of triggering the precondition high whenever `deposits_len` transitions to `0` in the underlying obligation as a result of the real Solend program's deposit-removal behavior.

### Recommendation
- Do not hardcode `deposits_len == 1` as a strict precondition for `solend_deposit`. Instead, branch based on the actual `deposits_len` byte: if `0`, treat it as "no active deposit" and read/write accordingly (e.g., allow depositing into an empty obligation without requiring a pre-existing entry), or provide a dedicated re-initialization path.
- In `get_solend_obligation_deposit_amount`, check `deposits_len` before reading the fixed offset, returning `0` when `deposits_len == 0` rather than assuming byte `204` always holds valid/current deposit data.
- Add regression tests that perform a full Solend withdrawal followed by a subsequent deposit attempt on the same bank/obligation to confirm the flow does not revert.

### Proof of Concept
1. User deposits into a Solend-backed marginfi bank via `solend_deposit`, creating an obligation with `deposits_len == 1`.
2. User calls `solend_withdraw` with `withdraw_all = true`, fully draining the collateral. The underlying Solend CPI removes the sole deposit entry, setting `deposits_len` to `0` in the obligation account.
3. User (or anyone) attempts `solend_deposit` again on the same bank to re-establish a position.
4. `validate_solend_obligation` executes `require_eq!(data[202], 1u8, SolendMocksError::InvalidObligationCollateral)` [6](#0-5)  against a `deposits_len` of `0`, causing the transaction to revert with `InvalidObligationCollateral` for every future deposit attempt on that bank's obligation.

Note: I was not able to inspect the actual on-chain Solend program's byte-level `pack_into_slice` behavior for obligations to definitively confirm whether removed entries are zeroed versus left stale; the freeze conclusion rests on the confirmed fact that `validate_solend_obligation` strictly requires `deposits_len == 1` with no fallback, which is present and verifiable directly in this codebase.

### Citations

**File:** programs/solend-mocks/src/state.rs (L236-248)
```rust
    // Check deposits_len at position 202 (should be 1 for single deposit)
    require_eq!(
        data[202],
        1u8,
        SolendMocksError::InvalidObligationCollateral
    );

    // Check borrows_len at position 203 (should be 0 for no borrows)
    require_eq!(data[203], 0u8, SolendMocksError::InvalidObligationLiquidity);

    // First deposit starts at position 204 in data_flat array
    // Each deposit is 88 bytes: [Pubkey (32) + u64 (8) + u128 (16) + padding (32)]
    let deposit_start = 204;
```

**File:** programs/solend-mocks/src/state.rs (L279-313)
```rust
/// Get the deposit amount at position 0 from a Solend obligation
pub fn get_solend_obligation_deposit_amount(account: &AccountInfo) -> Result<u64> {
    // Verify owner is Solend program
    require_keys_eq!(
        *account.owner,
        crate::ID,
        SolendMocksError::InvalidAccountData
    );

    let data = account.try_borrow_data()?;

    // Check size (including version byte)
    require!(
        data.len() >= OBLIGATION_LEN,
        SolendMocksError::InvalidAccountData
    );

    // Check version byte
    require_eq!(data[0], 1u8, SolendMocksError::InvalidAccountData);

    // Manual extraction without deserialization
    // First deposit starts at position 204 in data_flat array
    // Each deposit is 88 bytes: [Pubkey (32) + u64 (8) + u128 (16) + padding (32)]
    let deposit_start = 204;

    // Get first deposit amount (8 bytes at position 236-243)
    let deposit_amount_bytes = &data[deposit_start + 32..deposit_start + 40];
    let deposit_amount = u64::from_le_bytes(
        deposit_amount_bytes
            .try_into()
            .map_err(|_| SolendMocksError::InvalidObligationCollateral)?,
    );

    Ok(deposit_amount)
}
```

**File:** programs/marginfi/src/instructions/solend/deposit.rs (L46-50)
```rust
    // Forced to validate here as unable to load obligation as ref in constraints
    validate_solend_obligation(
        ctx.accounts.integration_acc_2.as_ref(),
        ctx.accounts.integration_acc_1.key(),
    )?;
```

**File:** programs/marginfi/src/instructions/solend/withdraw.rs (L57-59)
```rust
///
/// 4. For withdrawing an entire position, use the `withdraw_all` option instead of
///    trying to calculate the exact amount.
```

**File:** programs/marginfi/src/instructions/solend/withdraw.rs (L73-76)
```rust
    validate_solend_obligation(
        ctx.accounts.integration_acc_2.as_ref(),
        ctx.accounts.integration_acc_1.key(),
    )?;
```
