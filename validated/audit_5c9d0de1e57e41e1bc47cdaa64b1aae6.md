The comment right in the code answers the question directly — this is documented, intentional design, not an oversight.

### Title
No vulnerability — flat size-based contract loading fee is documented, intentional design - ([File: runtime/near-vm-runner/src/logic/gas_counter.rs])

### Summary
The contract loading fee is explicitly designed as a size-only linear fee (`contract_loading_base + contract_loading_bytes * code_len`), independent of code structure, and the codebase documents why this is the case. The report's premise — that the fee should track the CPU cost of `PrepareContext::run`/`InstrumentContext::run` per pathological structure — contradicts the code's own stated design rationale.

### Finding Description
`GasCounter::add_contract_loading_fee` charges `pay_per(ExtCosts::contract_loading_bytes, code_len)` plus `pay_base(ExtCosts::contract_loading_base)`, and the doc comment directly above it states: "This cost does not consider the structure of the contract code, only the size... A fee that takes the code structure into consideration could be added. But since that would have to happen after loading, we cannot pre-charge it. This is the main motivation to (only) have this simple fee." [1](#0-0) 

This fee is charged either pre-load (`before_loading_executable`, when `fix_contract_loading_cost` is set) or post-load (`after_loading_executable`, the legacy/stable-mainnet ordering) — in both cases it is charged once per cache-miss compile, exactly as described in the report. [2](#0-1) 

The per-byte rate (`contract_loading_bytes`) is not an "average-case" estimate pulled from typical contracts; it is derived by the params estimator using dedicated benchmark contracts and is meant to bound the compile/prepare cost as a function of size, similar to how other per-byte wasm costs (`regular_op_cost`, `linear_op_base_cost`/`linear_op_unit_cost` used inside `prepare_v3`'s finite-wasm `Analysis`) are calibrated. [3](#0-2)  Preparation itself (`prepare_v3::prepare_contract` → `PrepareContext::run` → `InstrumentContext::run`) is bounded by hard `LimitConfig` caps on function/local/block/param/table/type counts and instrumented-code size — these are enforced independent of the loading fee and reject contracts that exceed them regardless of gas paid. [4](#0-3) [5](#0-4) 

Whether the calibrated per-byte rate is generous enough to cover every combination of maximal function/block/local counts within `max_contract_size` is a params-estimation/calibration question, not a logic bug in `prepare_contract` or `add_contract_loading_fee`. No code path in the cited functions bypasses metering, undercharges relative to the protocol's own defined fee formula, or allows the attacker to avoid paying the size-based fee on every cache-miss call.

### Impact Explanation
Not applicable — this is not a bypass of the metering scheme; it is the metering scheme as designed, with a documented rationale in the code itself. There is no evidence of fee payment being skipped, reduced, or manipulated by the attacker. Any gap between worst-case prepare/instrument CPU time and the flat per-byte rate would be a calibration/DoS-hardening concern for the params-estimator methodology, not a fund-theft, freezing, consensus-divergence, or shard-halt vulnerability reachable via the described transaction flow.

### Likelihood Explanation
Not applicable.

### Recommendation
Not applicable — no code change is indicated. If there is a concern that the current `contract_loading_bytes` rate underestimates worst-case `prepare_v3`/`instrument_v3` CPU cost for maximal function/block/local-count contracts near `max_contract_size`, that would need to be addressed by re-running the params-estimator benchmarks against worst-case-shaped contracts (not just typical ones) to recalibrate the rate — an estimator/calibration exercise, not a code-logic fix.

### Proof of Concept
Not applicable.

### Citations

**File:** runtime/near-vm-runner/src/logic/gas_counter.rs (L216-227)
```rust
    /// Add a cost for loading the contract code in the VM.
    ///
    /// This cost does not consider the structure of the contract code, only the
    /// size. This is currently the only loading fee. A fee that takes the code
    /// structure into consideration could be added. But since that would have
    /// to happen after loading, we cannot pre-charge it. This is the main
    /// motivation to (only) have this simple fee.
    #[cfg(feature = "wasmtime_vm")]
    pub(crate) fn add_contract_loading_fee(&mut self, code_len: u64) -> Result<()> {
        self.pay_per(ExtCosts::contract_loading_bytes, code_len)?;
        self.pay_base(ExtCosts::contract_loading_base)
    }
```

**File:** runtime/near-vm-runner/src/logic/gas_counter.rs (L229-270)
```rust
    /// VM independent setup before loading the executable.
    ///
    /// Does VM independent checks that happen after the instantiation of
    /// VMLogic but before loading the executable. This includes pre-charging gas
    /// costs for loading the executable, which depends on the size of the WASM code.
    #[cfg(feature = "wasmtime_vm")]
    pub(crate) fn before_loading_executable(
        &mut self,
        config: &near_parameters::vm::Config,
        method_name: &str,
        wasm_code_bytes: u64,
    ) -> std::result::Result<(), super::errors::FunctionCallError> {
        if method_name.is_empty() {
            let error = super::errors::FunctionCallError::MethodResolveError(
                super::errors::MethodResolveError::MethodEmptyName,
            );
            return Err(error);
        }
        if config.fix_contract_loading_cost {
            if self.add_contract_loading_fee(wasm_code_bytes).is_err() {
                let error =
                    super::errors::FunctionCallError::HostError(super::HostError::GasExceeded);
                return Err(error);
            }
        }
        Ok(())
    }

    /// Legacy code to preserve old gas charging behaviour in old protocol versions.
    #[cfg(feature = "wasmtime_vm")]
    pub(crate) fn after_loading_executable(
        &mut self,
        config: &near_parameters::vm::Config,
        wasm_code_bytes: u64,
    ) -> std::result::Result<(), super::errors::FunctionCallError> {
        if !config.fix_contract_loading_cost {
            if self.add_contract_loading_fee(wasm_code_bytes).is_err() {
                return Err(super::errors::FunctionCallError::HostError(
                    super::HostError::GasExceeded,
                ));
            }
        }
```

**File:** runtime/near-vm-runner/src/prepare/prepare_v3.rs (L8-24)
```rust
struct PrepareContext<'a> {
    code: &'a [u8],
    config: &'a Config,
    output_code: Vec<u8>,
    function_limit: u64,
    local_limit: u64,
    function_body_size_limit: u64,
    table_limit: u32,
    table_element_limit: u64,
    type_limit: u64,
    global_limit: u64,
    validator: wp::Validator,
    func_validator_allocations: wp::FuncValidatorAllocations,
    before_import_section: bool,
    before_memory_section: bool,
    before_export_section: bool,
}
```

**File:** runtime/near-vm-runner/src/prepare/prepare_v3.rs (L410-421)
```rust
    let analysis = finite_wasm_6::Analysis::new()
        .with_stack(SimpleMaxStackCfg)
        .with_gas(SimpleGasCostCfg {
            regular: u64::from(config.regular_op_cost),
            linear_base: config.linear_op_base_cost,
            linear_unit: config.linear_op_unit_cost,
        })
        .analyze(&lightly_steamed)
        .map_err(|err| {
            tracing::error!(target: "vm", ?err, ?kind, "analysis failed");
            PrepareError::Deserialization
        })?;
```

**File:** runtime/near-vm-runner/src/prepare/instrument_v3.rs (L104-115)
```rust
pub(crate) struct InstrumentContext<'a> {
    analysis: &'a AnalysisOutcome,
    wasm: &'a [u8],
    import_env: &'a str,
    globals: u32,
    op_cost: u32,
    max_stack_height: u32,
    max_blocks_per_function: u64,
    max_blocks_per_contract: u64,
    max_params_per_function: u64,
    max_params_per_contract: u64,
    max_operand_stack_bytes_per_function: u64,
```
