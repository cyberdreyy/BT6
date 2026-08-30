The FAQ's actual statement is the opposite of what the question assumes: "compiler crashes are always preferred to potential hiding of undefined behavior" — it explicitly says compiler crashes (i.e., `VMRunnerError`/process abort) are the *desired* deterministic-failure behavior, not that "compiler crashes must be deterministic." The document treats resource-dependent compiler misbehavior as a known theoretical risk category to be mitigated by crashing the node rather than by silently caching a divergent result. This is documentation-level design guidance, not a code invariant enforced in `cache.rs`.

Examining the actual code path: `WasmtimeVM::compile_uncached` in `runtime/near-vm-runner/src/wasmtime_runner/mod.rs:570-597` calls `self.engine.precompile_module(&prepared_code)`, and on any `Err` unconditionally wraps it as `CompilationError::WasmtimeCompileError`, which is then cached via `compile_and_persist` as `CompiledContract::CompileModuleError` and persisted to disk by `FilesystemContractRuntimeCache::put` at `runtime/near-vm-runner/src/cache.rs:630-707`. There is no code path that distinguishes an actual out-of-memory/resource-exhaustion condition from a genuine wasmtime compilation rejection — Wasmtime itself does not surface allocation failures as recoverable `Result::Err` values from `precompile_module`; genuine allocation failures in Rust typically abort the process (`abort`/OOM killer), which is consistent with the FAQ's stated preference for crashing over silently diverging. The claim that compile success/failure varies by "available host memory / thread contention" at compile time for a specific contract near `max_instrumented_code_size` is not demonstrated anywhere in the provided code, tests, or docs — no test, benchmark, or code comment in this repo acknowledges or reproduces such variability, and `runtime/near-vm-runner/src/tests/cache.rs:16-55` (`test_caches_compilation_error`) only tests that a genuinely-invalid module deterministically produces and reuses a cached `CompilationError`, not that the same valid/invalid module can flip outcomes under memory pressure.

This finding is speculative: it assumes an unproven non-determinism property of the underlying Wasmtime/Cranelift compiler under memory pressure, without any code evidence in this repository that such non-determinism exists, is triggerable by an ordinary unprivileged attacker's transaction, or is unmitigated by existing crash-preferred design. The rules explicitly instruct rejecting "speculative resource-hygiene claims with no reachable mainnet scenario," and this question fits that exclusion — there is no attacker-controlled lever demonstrated in-repo that forces a `Code`/`CompileModuleError` split for the *same* wasm bytes across honest nodes; it is a hypothesis about upstream compiler behavior, not a bug in `FilesystemContractRuntimeCache::put`/`get` or the surrounding cache logic. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) 

#No Vulnerability found for this question.

### Citations

**File:** runtime/near-vm-runner/FAQ.md (L121-129)
```markdown
### What are the dangers of bugs in compilers/VMs?

Unlike traditional software development, bugs and UB in the contract runtime could be pretty
devastating for the network coherence, as they may trigger inconsistency between nodes, and
lead to undesired blockchain forks. Thus, whenever there’s a risk of behavioral discrepancy
between nodes executing contract code - it shall be mitigated. No visible state shall rely
upon timing taken for the certain operation, compilation or execution alike, and if an
execution correctness problem exists - it must be the same on all nodes.
Thus compiler crashes are always preferred to potential hiding of undefined behavior.
```

**File:** runtime/near-vm-runner/src/wasmtime_runner/mod.rs (L569-597)
```rust
    #[tracing::instrument(target = "vm", level = "debug", "WasmtimeVM::compile_uncached", skip_all)]
    pub(crate) fn compile_uncached(&self, code: &ContractCode) -> CachedArtifact {
        let start = std::time::Instant::now();
        let prepared_code = prepare::prepare_contract(code.code(), &self.config, VMKind::Wasmtime)
            .map_err(CompilationError::PrepareError)?;
        let serialized = self.engine.precompile_module(&prepared_code).map_err(|err| {
            tracing::debug!(
                target: "vm",
                ?err,
                code_hash = %code.hash(),
                code_size = code.code().len(),
                "wasmtime contract compilation failed",
            );
            CompilationError::WasmtimeCompileError { msg: err.to_string() }
        })?;

        let elapsed = start.elapsed();
        tracing::debug!(
            target: "vm",
            original_size = %code.code().len(),
            prepared_size = %prepared_code.len(),
            compiled_size = %serialized.len(),
            elapsed_ms = %elapsed.as_millis(),
            "wasmtime compiled contract",
        );

        crate::metrics::compilation_duration(elapsed);
        Ok(serialized)
    }
```

**File:** runtime/near-vm-runner/src/wasmtime_runner/mod.rs (L656-678)
```rust
    /// Inner Double-Checked-Lock: re-check + actual compile + cache write.
    fn compile_and_persist(
        &self,
        key: CryptoHash,
        code: &ContractCode,
        cache: &dyn ContractRuntimeCache,
        _lock_guard: MutexGuard<'_, ()>,
    ) -> Result<CachedArtifact, CacheError> {
        // The cache may have been populated while we waited on the per-key lock.
        if let Some(compiled) = read_cache(cache, &key)? {
            return Ok(compiled);
        }
        let serialized_or_error = self.compile_uncached(code);
        let record = CompiledContractInfo {
            wasm_bytes: code.code().len() as u64,
            compiled: match &serialized_or_error {
                Ok(serialized) => CompiledContract::Code(serialized.clone()),
                Err(err) => CompiledContract::CompileModuleError(err.clone()),
            },
        };
        cache.put(&key, record).map_err(CacheError::WriteError)?;
        Ok(serialized_or_error)
    }
```

**File:** runtime/near-vm-runner/src/cache.rs (L630-668)
```rust
    fn put(&self, key: &CryptoHash, value: CompiledContractInfo) -> std::io::Result<()> {
        let weight = entry_disk_size(&value);

        const MAX_ATTEMPTS: u32 = 5;
        let final_filename = key.to_string();
        let mode = Mode::RUSR | Mode::WUSR | Mode::RGRP | Mode::WGRP;
        let flags = OFlags::CREATE | OFlags::TRUNC | OFlags::WRONLY;
        let mut attempt = 0;
        let (temp_filename, mut file) = loop {
            attempt += 1;
            let mut temporary_filename = final_filename.clone();
            temporary_filename.push('.');
            for b in rand::thread_rng().sample_iter(rand::distributions::Alphanumeric).take(8) {
                temporary_filename.push(b as char);
            }
            temporary_filename.push_str(".temp");
            match openat(&self.state.dir, &temporary_filename, flags, mode) {
                Ok(f) => break (temporary_filename, std::fs::File::from(f)),
                Err(e) if attempt > MAX_ATTEMPTS => return Err(e.into()),
                Err(e) if e.kind() == std::io::ErrorKind::AlreadyExists => continue,
                Err(e) => return Err(e.into()),
            }
        };

        // This section manually "serializes" the data. The cache is quite sensitive to
        // unnecessary overheads and in order to enable things like mmap-based file access, we want
        // to have full control of what has been written.
        match value.compiled {
            CompiledContract::CompileModuleError(e) => {
                borsh::to_writer(&mut file, &e)?;
                file.write_all(&[ERROR_TAG])?;
            }
            CompiledContract::Code(bytes) => {
                file.write_all(&bytes)?;
                // Writing the tag at the end gives us well aligned buffer of the data above which
                // is necessary for 0-copy deserialization later on.
                file.write_all(&[CODE_TAG])?;
            }
        }
```

**File:** runtime/near-vm-runner/src/tests/cache.rs (L16-55)
```rust
#[test]
fn test_caches_compilation_error() {
    with_vm_variants(|vm_kind: VMKind| {
        let config = Arc::new(test_vm_config(Some(vm_kind)));
        // The cache is currently properly implemented only for Wasmtime
        match vm_kind {
            VMKind::Wasmtime => {}
            VMKind::Wasmer0 | VMKind::Wasmer2 | VMKind::NearVm => return,
        }
        let cache = MockContractRuntimeCache::default();
        let code = [42; 1000];
        let code = ContractCode::new(code.to_vec(), None);
        let code_hash = *code.hash();
        let terragas = 1000000000000u64;
        assert_eq!(cache.len(), 0);
        let outcome1 = make_cached_contract_call_vm(
            Arc::clone(&config),
            &cache,
            code_hash,
            Some(&code),
            "method_name1",
            terragas,
            vm_kind,
        )
        .expect("bad failure");
        println!("{:?}", cache);
        assert_eq!(cache.len(), 1);
        let outcome2 = make_cached_contract_call_vm(
            Arc::clone(&config),
            &cache,
            code_hash,
            None,
            "method_name2",
            terragas,
            vm_kind,
        )
        .expect("bad failure");
        assert_eq!(outcome1.aborted.as_ref(), outcome2.aborted.as_ref());
    })
}
```
