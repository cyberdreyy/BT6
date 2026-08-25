## Title
Missing per-CPI compute-unit overhead charge on the builtin-program cross-program-invocation path (`native_invoke_signed`) — ([File: program-runtime/src/invoke_context.rs])

## Summary
This is a direct structural analog of the M-5 pattern: two code paths achieve the same effect (a cross-program invocation), but the accounting update (compute-unit consumption for the invocation overhead) is applied on only one of them.

## Finding Description
For SBPF-program-initiated CPI, `cpi_common()` explicitly charges the fixed per-invocation compute-unit overhead before performing the callee call: [1](#0-0) 

This overhead value is `invoke_units`, defaulting to `DEFAULT_INVOCATION_COST = 946`: [2](#0-1) [3](#0-2) 

However, `InvokeContext::native_invoke_signed`, the entrypoint used for a builtin program to cross-program-invoke another program (mirrors the SBF CPI path structurally, including privilege/PDA-signer derivation), never consumes `invoke_units` from `compute_meter` — it only prepares the instruction and calls `process_instruction`: [4](#0-3) 

Compare directly to `cpi_common`, where the very first action is `invoke_context.compute_meter.consume_checked(amount)` using `invoke_units` — this line/analog is entirely absent from `native_invoke_signed`.

The comment on `native_invoke_signed` states it "mirrors the SBF CPI path" for privilege-escalation purposes, but it does not mirror it for compute-cost accounting purposes. This is the same class of bug as M-5: one call path (SBPF `cpi_common`) updates the resource accounting (compute-unit ledger) for the invocation, while an alternate call path reaching the same underlying invocation machinery (`prepare_next_cpi_instruction` + `process_instruction`) does not.

## Impact Explanation
Every native/builtin-triggered CPI (used by the SPL/native programs, e.g. bpf_loader migrations and other builtins that invoke other programs on behalf of a caller) is charged zero overhead for the invocation itself, whereas the identical operation from an SBF program is charged 946 CU. Because the callee's actual execution work is still metered separately via the same `compute_meter` (shared for the whole transaction), the impact is limited to under-accounting a fixed constant per native invocation rather than uncapping total computation. It does not allow unbounded compute usage, so it does not rise to a full compute-metering-bypass DoS. However, it violates the intended cost model/spec that every CPI has an invocation overhead charge, and could allow a caller to compose a transaction with more native-invoked CPIs than the compute budget would otherwise permit, since the fixed cost accounted for each planned invocation in the cost model / worst-case scheduling assumptions is silently discounted on this path. This is analogous to a low/informational discrepancy rather than a fund-loss or consensus-divergence bug — I could not find production user-transaction flows that repeatedly and cheaply trigger `native_invoke_signed` at attacker-controlled scale within the code I was able to inspect (its call sites in `programs/bpf_loader/src/lib.rs` and `programs/vote/src/vote_state/mod.rs` were not fully enumerated due to grep result limits in this session), so I cannot confirm a materially exploitable resource-exhaustion path, only the confirmed accounting discrepancy itself.

## Likelihood Explanation
The discrepancy is deterministic and always present whenever `native_invoke_signed` is used instead of the SBF `cpi_common` path — no special conditions are required to trigger the missing charge, only that a builtin program performs a CPI via this API. Whether this is remotely triggerable at meaningful scale by an ordinary user transaction depends on which builtin instructions call `native_invoke_signed` and how many times per transaction, which I was unable to fully confirm with the available tool calls in this session.

## Recommendation
Charge `invoke_context.get_execution_cost().invoke_units` against `invoke_context.compute_meter` inside `native_invoke_signed`, mirroring the exact `consume_checked(amount)` call performed at the top of `cpi_common`, so that both CPI entrypoints (SBF-syscall-driven and builtin-driven) apply identical invocation-overhead accounting.

## Proof of Concept
Not independently verified end-to-end in this session (no terminal/execution access). Structural PoC sketch:
1. Identify a builtin instruction handler that calls `InvokeContext::native_invoke_signed` (e.g., a bpf_loader or vote-program code path).
2. Construct a transaction that repeatedly triggers that instruction/CPI within the compute budget.
3. Compare consumed compute units reported for that transaction against the expected total that would include `946` CU (`DEFAULT_INVOCATION_COST`) per native CPI — the actual consumption will be lower by `946 * (number of native invocations)`, confirming the missing charge.

Because I could not fully enumerate and test the concrete call sites of `native_invoke_signed` in `programs/bpf_loader/src/lib.rs` and `programs/vote/src/vote_state/mod.rs` (tool output was truncated/empty on the last search), I recommend a background Devin session with full repository/terminal access to confirm exact reachability and quantify real-world impact before treating this as more than a Low/Informational accounting-spec discrepancy.

### Citations

**File:** program-runtime/src/cpi.rs (L781-786)
```rust
    // CPI entry.
    //
    // Translate the inputs to the syscall and synchronize the caller's account
    // changes so the callee can see them.
    let amount = invoke_context.get_execution_cost().invoke_units;
    invoke_context.compute_meter.consume_checked(amount)?;
```

**File:** program-runtime/src/execution_budget.rs (L20-21)
```rust
//Default CPI invocation cost
pub const DEFAULT_INVOCATION_COST: u64 = 946;
```

**File:** program-runtime/src/execution_budget.rs (L207-212)
```rust
impl Default for SVMTransactionExecutionCost {
    fn default() -> Self {
        SVMTransactionExecutionCost {
            log_64_units: 100,
            create_program_address_units: 1500,
            invoke_units: DEFAULT_INVOCATION_COST,
```

**File:** program-runtime/src/invoke_context.rs (L319-345)
```rust
    /// Entrypoint for a cross-program invocation from a builtin program.
    ///
    /// Takes signer seeds and derives PDAs internally via
    /// `create_program_address`, mirroring the SBF CPI path. This makes
    /// it structurally impossible for a builtin to vouch for a non-PDA
    /// address (e.g. a user wallet) as a signer.
    pub fn native_invoke_signed(
        &mut self,
        instruction: Instruction,
        signer_seeds: &[&[&[u8]]],
    ) -> Result<(), InstructionError> {
        let caller_program_id = *self
            .transaction_context
            .get_current_instruction_context()?
            .get_program_key()?;
        // The conversion from `PubkeyError` to `InstructionError` through
        // num-traits is incorrect, but it's the existing behavior.
        let signers = signer_seeds
            .iter()
            .map(|seeds| Pubkey::create_program_address(seeds, &caller_program_id))
            .collect::<Result<Vec<Pubkey>, solana_pubkey::PubkeyError>>()
            .map_err(|e| e as u64)?;
        self.prepare_next_cpi_instruction(instruction, &signers)?;
        let mut compute_units_consumed = 0;
        self.process_instruction(&mut compute_units_consumed, &mut ExecuteTimings::default())?;
        Ok(())
    }
```
