### Title
Floor-division rounding in CPI instruction-translation compute-unit charging under-quotes the CU cost of translating attacker-controlled data — ([File: program-runtime/src/cpi.rs])

### Summary
`translate_instruction_c` and its Rust-ABI equivalent in `program-runtime/src/cpi.rs` compute the compute-unit (CU) cost of translating CPI instruction `data` and `account_metas` out of guest VM memory using `checked_div` (a floor/truncating division), then charge that value via `invoke_context.compute_meter.consume_checked(...)`. Because the division rounds toward zero rather than up, any translated byte count that isn't an exact multiple of `cpi_bytes_per_unit` is under-charged, and byte counts smaller than `cpi_bytes_per_unit` are charged **zero** CU for real work performed. This mirrors the Peapods `convertToShares` bug class: an "amount owed/needed" is computed with a rounding-down conversion function, silently under-quoting the true cost.

### Finding Description
In `translate_instruction_c`:
```rust
let mut total_cu_translation_cost: u64 = (data.len() as u64)
    .checked_div(invoke_context.get_execution_cost().cpi_bytes_per_unit)
    .unwrap_or(u64::MAX);

let account_meta_translation_cost = (ix_c
    .accounts_len
    .saturating_mul(size_of::<AccountMeta>() as u64))
.checked_div(invoke_context.get_execution_cost().cpi_bytes_per_unit)
.unwrap_or(u64::MAX);
``` [1](#0-0) 

The identical pattern also exists in the Rust-ABI `translate_instruction_rust` path: [2](#0-1) 

Both call sites use `checked_div`, which performs integer (floor) division — there is no `+ (cpi_bytes_per_unit - 1)` ceiling adjustment as is used correctly elsewhere in the codebase for similar per-unit conversions, e.g. `CostModel::calculate_pages_for_bytes`:
```rust
fn calculate_pages_for_bytes(bytes: u32) -> u64 {
    u64::from(bytes)
        .saturating_add(ACCOUNT_DATA_COST_PAGE_SIZE.saturating_sub(1))
        .saturating_div(ACCOUNT_DATA_COST_PAGE_SIZE)
}
``` [3](#0-2) 

and the prioritization-fee calculation, which explicitly rounds up to avoid under-charging lamports:
```rust
micro_lamport_fee
    .saturating_add(MICRO_LAMPORTS_PER_LAMPORT.saturating_sub(1) as u128)
    .checked_div(MICRO_LAMPORTS_PER_LAMPORT as u128)
``` [4](#0-3) 

The CPI translation path (`data.len()` bytes and `accounts_len * size_of::<AccountMeta>()` bytes of VM memory that must be translated/copied per CPI call) is directly reachable by any unprivileged, deployed BPF program performing a cross-program invocation — this is one of the most common runtime operations (`sol_invoke_signed_c`/`sol_invoke_signed_rust`), not privileged or node-level code.

### Impact Explanation
Because the CU charge for CPI data/account-meta translation floors to zero for any byte count smaller than `cpi_bytes_per_unit`, and floors the remainder on every call otherwise, a program can perform many CPI calls each carrying instruction data/account metas sized just under the `cpi_bytes_per_unit` threshold and have the real memory-translation work (`translate_slice`, per-account-meta validation and cloning) performed essentially for free from a compute-metering perspective. This is a compute-unit metering bypass: the on-chain declared/charged cost systematically understates the actual work the validator performs to service the CPI, allowing a transaction to induce disproportionately more real CPU work per declared CU than intended, which is exactly the "metering bypass" impact category called out as acceptable for this analysis. It does not cause immediate fund loss but degrades the accuracy of the cost model that block-cost limits and packing rely on.

### Likelihood Explanation
High reachability: this code executes on every native/`solana_program`-ABI CPI invocation, which is ubiquitous in real programs (token transfers, PDAs, etc.), and is fully controlled by the calling BPF program (attacker can set `data.len()`/`accounts_len` to any value up to instruction-size limits). No special privileges, feature flags, or leaked keys are required — only a deployed program invoking CPI with crafted instruction sizes.

### Recommendation
Change both `checked_div` calls in `translate_instruction_c` and `translate_instruction_rust` (program-runtime/src/cpi.rs) to ceiling division, matching the pattern already used in `CostModel::calculate_pages_for_bytes`, e.g.:
```rust
let total_cu_translation_cost = (data.len() as u64)
    .saturating_add(cpi_bytes_per_unit.saturating_sub(1))
    .checked_div(cpi_bytes_per_unit)
    .unwrap_or(u64::MAX);
```
applied identically to the account-meta translation cost term.

### Proof of Concept
Not executed (index/context limits — this requires building and running an SBF test harness). Conceptually: deploy a program that repeatedly performs `invoke`/`invoke_signed` with `data.len()` and `accounts_len * size_of::<AccountMeta>()` each set to `cpi_bytes_per_unit - 1` bytes; per the floor-division formula both `total_cu_translation_cost` terms evaluate to `0`, so `compute_meter.consume_checked(0)` is charged despite the runtime performing full `translate_slice` validation/copy work for that many bytes on every CPI call. Repeating this within the compute-unit budget lets far more actual translation work be performed than the compute-unit accounting reflects.

### Citations

**File:** program-runtime/src/cpi.rs (L560-571)
```rust
    let mut total_cu_translation_cost: u64 = (data.len() as u64)
        .checked_div(invoke_context.get_execution_cost().cpi_bytes_per_unit)
        .unwrap_or(u64::MAX);

    // Each account meta is 34 bytes (32 for pubkey, 1 for is_signer, 1 for is_writable)
    let account_meta_translation_cost =
        (account_metas.len().saturating_mul(size_of::<AccountMeta>()) as u64)
            .checked_div(invoke_context.get_execution_cost().cpi_bytes_per_unit)
            .unwrap_or(u64::MAX);

    total_cu_translation_cost =
        total_cu_translation_cost.saturating_add(account_meta_translation_cost);
```

**File:** program-runtime/src/cpi.rs (L695-708)
```rust
    let mut total_cu_translation_cost: u64 = (data.len() as u64)
        .checked_div(invoke_context.get_execution_cost().cpi_bytes_per_unit)
        .unwrap_or(u64::MAX);

    // Each account meta is 34 bytes (32 for pubkey, 1 for is_signer, 1 for is_writable)
    let account_meta_translation_cost = (ix_c
        .accounts_len
        .saturating_mul(size_of::<AccountMeta>() as u64))
    .checked_div(invoke_context.get_execution_cost().cpi_bytes_per_unit)
    .unwrap_or(u64::MAX);

    total_cu_translation_cost =
        total_cu_translation_cost.saturating_add(account_meta_translation_cost);

```

**File:** cost-model/src/cost_model.rs (L185-190)
```rust
    /// Compute the number of pages needed to contain provided number of bytes.
    fn calculate_pages_for_bytes(bytes: u32) -> u64 {
        u64::from(bytes)
            .saturating_add(ACCOUNT_DATA_COST_PAGE_SIZE.saturating_sub(1))
            .saturating_div(ACCOUNT_DATA_COST_PAGE_SIZE)
    }
```

**File:** compute-budget/src/compute_budget_limits.rs (L62-68)
```rust
    let micro_lamport_fee: MicroLamports =
        (compute_unit_price as u128).saturating_mul(compute_unit_limit as u128);
    micro_lamport_fee
        .saturating_add(MICRO_LAMPORTS_PER_LAMPORT.saturating_sub(1) as u128)
        .checked_div(MICRO_LAMPORTS_PER_LAMPORT as u128)
        .and_then(|fee| u64::try_from(fee).ok())
        .unwrap_or(u64::MAX)
```
