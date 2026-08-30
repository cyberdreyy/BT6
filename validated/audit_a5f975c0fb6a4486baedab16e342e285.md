### Title
Gas metering bypass for contract compile/deserialize/link/instantiate cost via empty-method FunctionCall - (File: `runtime/near-vm-runner/src/wasmtime_runner/mod.rs`)

### Summary
In `WasmtimeVM::with_compiled_and_loaded`, the cache lookup/compile/`unsafe Module::deserialize`/link/`instantiate_pre` sequence executes entirely inside the `memory_cache().try_lookup` closure *before* `GasCounter::before_loading_executable` is invoked. Because `before_loading_executable` rejects empty method names before it ever calls `add_contract_loading_fee`, a `FunctionCall` receipt with an empty `method_name` can trigger the full one-time compile/deserialize/link/instantiate work for a large contract while paying zero `contract_loading_bytes`/`contract_loading_base` fee.

### Finding Description
`with_compiled_and_loaded` performs the cache lookup, and on a miss, `compile_and_cache` (mod.rs:726), the `unsafe { Module::deserialize(...) }` (mod.rs:747), linking, and `linker.instantiate_pre(&module)` (mod.rs:774) — all before `gas_counter.before_loading_executable(&config, &method, wasm_bytes)` is called at mod.rs:814. [1](#0-0) [2](#0-1) 

`before_loading_executable` checks `method_name.is_empty()` first and returns `MethodResolveError::MethodEmptyName` immediately, before ever reaching `add_contract_loading_fee` (the size-proportional loading fee): [3](#0-2) 

An attacker can: (1) deploy a maximal-size wasm contract via a normal `DeployContract` action (storage cost is a refundable stake, not a burnt fee), then (2) send a `FunctionCall` action with `method_name = ""` and minimal attached gas targeting that contract as the *first* invocation. This forces a real cache miss, so the full compile+deserialize+link+`instantiate_pre` work executes, and only afterward does `before_loading_executable` abort with `MethodEmptyName` — never charging `contract_loading_bytes`/`contract_loading_base` for that work.

Whether this reaches the runtime at all depends on the `RejectEmptyMethodName` protocol feature. Currently, action validation only rejects empty method names when this feature is enabled; pre-activation, the transaction is admitted and only fails inside the VM with the legacy `MethodEmptyName` error, exactly reproducing the described path: [4](#0-3) [5](#0-4) 

This confirms the nearcore team is aware of and actively fixing this exact gap by moving the empty-method rejection to transaction/receipt admission (before any VM work happens), gated behind the not-yet-stabilized `RejectEmptyMethodName` feature (per the `TODO(spice-test)` note tied to feature stabilization).

### Impact Explanation
This is a gas-metering/fee-bypass bug, not a direct theft/freeze of other users' funds: the attacker only avoids paying the `contract_loading_bytes`/`contract_loading_base` fee for compiling/loading their *own* contract on its first invocation. The uncharged compute is bounded to a one-time cost per unique `(contract_hash, config, vm_hash)` key, because subsequent lookups hit the shared per-node memory/persistent compiled-contract cache regardless of method name. The attacker must still pay the full `DeployContract` action cost and lock a real storage stake proportional to contract size to obtain a "large" contract to exploit, which caps the practical value of the underpaid fee per contract and makes this economically marginal rather than a scalable drain. It does not cause fund theft from third parties, token inflation, double-spend, authorization escalation, state-root divergence, or a shard-halting condition on its own.

### Likelihood Explanation
The path is fully reachable by an unprivileged account today wherever `RejectEmptyMethodName` is not yet active on the given protocol version: no special permissions, keys, or validator access are required — only a `DeployContract` + `FunctionCall(method_name="")` pair of ordinary signed transactions. Repeating the exploit for gain requires deploying a new distinct max-size contract each time (since the cache amortizes the cost per contract), which bounds the attacker's throughput and makes large-scale abuse costly relative to the (comparatively small) avoided per-byte loading fee.

### Recommendation
Move the empty-method-name check (and, ideally, the entire `before_loading_executable` cost-independent validation) ahead of the compile/deserialize/link/instantiate work in `with_compiled_and_loaded`, or perform the `method_name.is_empty()` check at the call site before invoking `with_compiled_and_loaded` at all. Longer term, complete the rollout of `ProtocolFeature::RejectEmptyMethodName` so that empty-method `FunctionCall` actions are rejected at receipt/tx validation, never reaching the VM.

### Proof of Concept
Unit test in `near-vm-runner` (mirroring `test-loop-tests/src/tests/reject_empty_method_name.rs` but pre-`RejectEmptyMethodName` protocol version): deploy a near-maximal-size wasm contract, call `WasmtimeVM::prepare`/`run` with `method_name = ""` as the first-ever invocation of that contract hash, and assert that `VMOutcome.burnt_gas` does not include any `contract_loading_bytes`/`contract_loading_base` charge, while independently confirming (e.g., via `crate::metrics::record_compiled_contract_cache_lookup` hit/miss instrumentation or wall-clock timing) that `compile_and_cache`, `Module::deserialize`, and `instantiate_pre` actually executed for that call.

### Citations

**File:** runtime/near-vm-runner/src/wasmtime_runner/mod.rs (L704-747)
```rust
        let (wasm_bytes, pre_result) = cache.memory_cache().try_lookup(
            key,
            || {
                is_memory_hit = false;
                let cache_record = cache.get(&key).map_err(CacheError::ReadError)?;
                let (wasm_bytes, module) =
                    if let Some(CompiledContractInfo { wasm_bytes, compiled }) = cache_record {
                        match compiled {
                            CompiledContract::CompileModuleError(err) => {
                                return Ok((
                                    err.size_bytes_approximate() as u64,
                                    to_any((wasm_bytes, Err(err))),
                                ));
                            }
                            CompiledContract::Code(module) => (wasm_bytes, module),
                        }
                    } else {
                        is_cache_hit = false;
                        let Some(code) = contract.get_code() else {
                            return Err(VMRunnerError::ContractCodeNotPresent);
                        };
                        let wasm_bytes = code.code().len() as u64;
                        match self.compile_and_cache(&code, cache)? {
                            Err(err) => {
                                return Ok((
                                    err.size_bytes_approximate() as u64,
                                    to_any((wasm_bytes, Err(err))),
                                ));
                            }
                            Ok(module) => (wasm_bytes, module),
                        }
                    };
                // (UN-)SAFETY: the `module` must have been produced by
                // a prior call to `serialize`.
                //
                // In practice this is not necessarily true. One could have
                // forgotten to change the cache key when upgrading the version of
                // the near_vm library or the database could have had its data
                // corrupted while at rest.
                //
                // There should definitely be some validation in near_vm to ensure
                // we load what we think we load.
                let compiled_size = module.len();
                let module = match unsafe { Module::deserialize(&self.engine, &module) } {
```

**File:** runtime/near-vm-runner/src/wasmtime_runner/mod.rs (L812-818)
```rust
        crate::metrics::record_compiled_contract_cache_lookup(is_cache_hit, is_memory_hit);
        let config = Arc::clone(&self.config);
        let result = gas_counter.before_loading_executable(&config, &method, wasm_bytes);
        if let Err(e) = result {
            let result = PreparationResult::OutcomeAbort(e);
            return Ok(PreparedContract { config, gas_counter, result });
        }
```

**File:** runtime/near-vm-runner/src/logic/gas_counter.rs (L234-255)
```rust
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
```

**File:** runtime/runtime/src/action_validation.rs (L261-295)
```rust
/// Validates `FunctionCallAction`. Checks that the method name is non-empty, that its length
/// doesn't exceed the limit, and that the length of the arguments doesn't exceed the limit.
fn validate_function_call_action(
    limit_config: &LimitConfig,
    action: &FunctionCallAction,
    current_protocol_version: ProtocolVersion,
    mode: ValidateReceiptMode,
) -> Result<(), ActionsValidationError> {
    if action.gas == Gas::ZERO {
        return Err(ActionsValidationError::FunctionCallZeroAttachedGas);
    }

    if mode == ValidateReceiptMode::NewReceipt
        && ProtocolFeature::RejectEmptyMethodName.enabled(current_protocol_version)
        && action.method_name.is_empty()
    {
        return Err(ActionsValidationError::FunctionCallEmptyMethodName);
    }

    if action.method_name.len() as u64 > limit_config.max_length_method_name {
        return Err(ActionsValidationError::FunctionCallMethodNameLengthExceeded {
            length: action.method_name.len() as u64,
            limit: limit_config.max_length_method_name,
        });
    }

    if action.args.len() as u64 > limit_config.max_arguments_length {
        return Err(ActionsValidationError::FunctionCallArgumentsLengthExceeded {
            length: action.args.len() as u64,
            limit: limit_config.max_arguments_length,
        });
    }

    Ok(())
}
```

**File:** test-loop-tests/src/tests/reject_empty_method_name.rs (L79-97)
```rust
    // Before the upgrade: the transaction is admitted and fails on-chain in the VM with the old
    // MethodEmptyName error. The upgrade takes ~2 epochs with an immediate voting schedule, so we
    // are comfortably still on the old protocol right after the deploy.
    assert_eq!(protocol_version_at_head(&env), old_protocol, "expected to start pre-upgrade");
    let tx = empty_method_tx(&env, &signer, &contract);
    let outcome = env
        .rpc_runner()
        .execute_tx(tx, Duration::seconds(10))
        .expect("empty-method tx admitted pre-upgrade");
    assert_matches!(
        outcome.status,
        FinalExecutionStatus::Failure(TxExecutionError::ActionError(ActionError {
            kind: ActionErrorKind::FunctionCallError(FunctionCallError::MethodResolveError(
                MethodResolveError::MethodEmptyName
            )),
            ..
        })),
        "pre-upgrade empty-method call should abort with the old MethodEmptyName error",
    );
```
