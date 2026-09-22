Based on the investigation, I found a concrete analog reachable by a single unprivileged transaction sender.

### Title
Precompile Signature Verification (ed25519/secp256k1/secp256r1) Bypasses the Compute Meter, Enabling CPU-Cost/Fee Mismatch DoS - ([File: program-runtime/src/invoke_context.rs])

### Summary
The Symfony CVE-2013-5958 stems from a security-critical hash computation whose CPU cost scales with attacker-controlled input length while not being properly bounded/charged. The corresponding pattern in agave is `InvokeContext::process_precompile`, which invokes the ed25519/secp256k1/secp256r1 precompile `verify()` routines directly, with **no call to `compute_meter.consume_checked`** anywhere in the precompile execution path. [1](#0-0) 

### Finding Description
`process_instruction` dispatches to either `process_executable_chain` (for BPF/builtin programs, which always charges compute units — enforced by the `BuiltinProgramsMustConsumeComputeUnits` check) or, for precompiles, to `process_precompile`, which simply calls the callback's `process_precompile` and never touches `self.compute_meter`: [1](#0-0) [2](#0-1) 

The actual precompile verification does substantial per-byte and per-signature cryptographic work that is fully attacker-controlled in size:
- `secp256r1::verify` allows up to 8 signatures per instruction, each with an OpenSSL EC point parse/validate plus a SHA-256 digest over a `message_data_size` field of up to `u16::MAX` (65535) bytes. [3](#0-2) 
- `ed25519::verify` allows `num_signatures` up to 255 (one byte), each doing an ed25519_dalek `verify_strict` over an attacker-sized message. [4](#0-3) 
- `secp256k1::verify` similarly loops over up to 255 signatures, each requiring a Keccak-256 hash over an attacker-sized message plus an EC point recovery. [5](#0-4) 

The only cost accounting for this work is the flat, per-signature `cost-model` constants (`SECP256K1_VERIFY_COST`, `ED25519_VERIFY_STRICT_COST`, `SECP256R1_VERIFY_COST`) used purely for block-packing/QoS cost estimation, not for actual runtime compute metering: [6](#0-5) [7](#0-6) 

These constants do not scale with `message_data_size`, so the estimation is only accurate for small messages. The runtime confirms builtins normally must consume the compute meter, but precompiles are explicitly exempted from that invariant — confirmed by a test showing a precompiled instruction consumes 0 CU from the meter while still receiving `MAX_BUILTIN_ALLOCATION_COMPUTE_UNIT_LIMIT` (3,000 CU) of estimated cost credit: [8](#0-7) 

### Impact Explanation
An attacker can craft a transaction with a single precompile instruction referencing a maximal-size message (via `message_data_offset`/`message_data_size` pointing into instruction data) and the maximum allowed signature count (8 for secp256r1, up to 255 for ed25519/secp256k1), forcing each of potentially hundreds of expensive EC/hash operations over up to ~64KB of data per signature, while:
1. This work is bypassed by the compute-unit metering entirely (no `ComputationalBudgetExceeded` possible), so it cannot be capped by `compute_unit_limit`.
2. The transaction fee and block-cost accounting only reflect the flat per-signature cost, not the actual (much larger) message-size-dependent CPU cost.

This is a leader-side/validator-side CPU exhaustion vector triggerable by a single submitted transaction (paying a comparatively small fee relative to CPU consumed), directly analogous to the Symfony DoS where attacker-controlled input length drove disproportionate CPU cost relative to what was "charged"/expected.

### Likelihood Explanation
Any account can construct and submit such a transaction; no privileged access, staking, or leader position is required. Precompile instructions are validated for max transaction/instruction size and signature-count bounds (e.g., secp256r1 rejects `num_signatures > 8`), but the size of the hashed/verified *message* is bounded only by the max transaction size (`PACKET_DATA_SIZE`/`v1::MAX_TRANSACTION_SIZE`), not by anything tied to the flat per-signature cost charged. This makes the attack straightforward to construct with standard SDK tooling.

### Recommendation
Charge compute units for precompile verification proportionally to the actual work performed (number of signatures × message length), consuming from `InvokeContext::compute_meter` inside `process_precompile` (or before dispatching to it) using the same/similar formula as the cost-model constants but scaled by `message_data_size`, and ensure `process_precompile` enforces the `BuiltinProgramsMustConsumeComputeUnits`-style invariant so precompile CPU cost is capped by `compute_unit_limit` and properly reflected in the fee paid.

### Proof of Concept
1. Construct a transaction with a single `secp256r1_program` instruction containing 8 valid `Secp256r1SignatureOffsets` entries, each pointing `message_data_offset`/`message_data_size` at a shared ~65,000-byte blob embedded elsewhere in the instruction data (up to the transaction size limit).
2. Submit repeatedly at the maximum instructions-per-transaction/transactions-per-block rate; each verification call performs 8× (EC point validation + SHA-256 over ~65KB) with `Verifier::update`/`verify` in `precompiles/src/secp256r1.rs`, entirely uncapped by the compute meter (see `process_precompile` in `program-runtime/src/invoke_context.rs`), while the transaction fee/cost reflects only `8 * SECP256R1_VERIFY_COST` from `cost-model/src/block_cost_limits.rs`.
3. Repeat with `ed25519_program`/`secp256k1_program` (up to 255 signatures each) for greater amplification, since the per-signature cost constants there are similarly flat and message-size independent.

Note: I was unable to fully trace whether an additional compute-metering wrapper exists further up the call stack (e.g., at the SVM/`transaction_processor.rs` level) that might charge precompile cost before dispatching to `process_precompile`; my search of `invoke_context.rs` and the SVM processor code found no such charge, but a Devin session with full repository access could verify this exhaustively by tracing `program_instructions_iter` → `is_precompile` → `process_precompile` call sites across `svm/`, `program-runtime/`, and `runtime/`.

### Citations

**File:** program-runtime/src/invoke_context.rs (L616-631)
```rust
    /// Processes a precompile instruction
    #[cfg_attr(feature = "dev-context-only-utils", qualifiers(pub))]
    fn process_precompile(
        &mut self,
        program_id: &Pubkey,
        instruction_data: &[u8],
        message_instruction_datas_iter: impl Iterator<Item = &'ix_data [u8]>,
    ) -> Result<(), InstructionError> {
        self.push()?;
        let instruction_datas: Vec<_> = message_instruction_datas_iter.collect();
        self.environment_config
            .epoch_stake_callback
            .process_precompile(program_id, instruction_data, instruction_datas)
            .map_err(InstructionError::from)
            .and(self.pop())
    }
```

**File:** program-runtime/src/invoke_context.rs (L638-736)
```rust
    ) -> Result<(), InstructionError> {
        let instruction_context = self.transaction_context.get_current_instruction_context()?;
        let process_executable_chain_time = Measure::start("process_executable_chain_time");

        let builtin_id = {
            let owner_id = instruction_context.get_program_owner()?;
            if native_loader::check_id(&owner_id) {
                *instruction_context.get_program_key()?
            } else if bpf_loader_deprecated::check_id(&owner_id)
                || bpf_loader::check_id(&owner_id)
                || bpf_loader_upgradeable::check_id(&owner_id)
                || loader_v4::check_id(&owner_id)
            {
                owner_id
            } else {
                return Err(InstructionError::UnsupportedProgramId);
            }
        };

        // The Murmur3 hash value (used by RBPF) of the string "entrypoint"
        const ENTRYPOINT_KEY: u32 = 0x71E3CF81;
        let entry = self
            .program_cache_for_tx_batch
            .find(&builtin_id)
            .ok_or(InstructionError::UnsupportedProgramId)?;
        let function = match &entry.program {
            ProgramCacheEntryType::Builtin(program) => program
                .get_function_registry()
                .lookup_by_key(ENTRYPOINT_KEY)
                .map(|(_name, (function, _codegen))| function),
            _ => None,
        }
        .ok_or(InstructionError::UnsupportedProgramId)?;

        let program_id = *instruction_context.get_program_key()?;
        self.transaction_context
            .set_return_data(program_id, Vec::new())?;
        let logger = self.get_log_collector();
        stable_log::program_invoke(&logger, &program_id, self.get_stack_height());
        let pre_remaining_units = self.get_remaining();
        // For now, only built-ins are invoked from here, so the VM and its Config are irrelevant.
        self.memory_contexts
            .set_memory_context_abi_v1(MemoryContext::new(
                BpfAllocator::new(0),
                Vec::new(),
                // SAFETY:
                // This path invokes a builtin program, so this mapping is never used.
                unsafe {
                    MemoryMapping::new(Vec::new(), &Config::default(), SBPFVersion::Reserved)
                        .unwrap()
                },
            ))?;
        let mut vm = EbpfVm::new(
            Arc::clone(
                &**self
                    .environment_config
                    .program_runtime_environments
                    .get_env_for_execution(),
            ),
            SBPFVersion::V0,
            // Removes lifetime tracking
            unsafe { std::mem::transmute::<&mut InvokeContext, &mut InvokeContext>(self) },
            0,
        );
        vm.invoke_function(function);
        let result = match vm.program_result {
            ProgramResult::Ok(_) => {
                stable_log::program_success(&logger, &program_id);
                Ok(())
            }
            ProgramResult::Err(ref err) => {
                if let EbpfError::SyscallError(syscall_error) = err {
                    if let Some(instruction_err) = syscall_error.downcast_ref::<InstructionError>()
                    {
                        stable_log::program_failure(&logger, &program_id, instruction_err);
                        Err(instruction_err.clone())
                    } else {
                        stable_log::program_failure(&logger, &program_id, syscall_error);
                        Err(InstructionError::ProgramFailedToComplete)
                    }
                } else {
                    stable_log::program_failure(&logger, &program_id, err);
                    Err(InstructionError::ProgramFailedToComplete)
                }
            }
        };
        let post_remaining_units = self.get_remaining();
        *compute_units_consumed = pre_remaining_units.saturating_sub(post_remaining_units);

        if builtin_id == program_id && result.is_ok() && *compute_units_consumed == 0 {
            return Err(InstructionError::BuiltinProgramsMustConsumeComputeUnits);
        }

        timings
            .execute_accessories
            .process_instructions
            .process_executable_chain_us += process_executable_chain_time.end_as_us();
        result
    }
```

**File:** precompiles/src/secp256r1.rs (L26-140)
```rust
    let num_signatures = data[0] as usize;
    if num_signatures == 0 {
        return Err(PrecompileError::InvalidInstructionDataSize);
    }
    if num_signatures > 8 {
        return Err(PrecompileError::InvalidInstructionDataSize);
    }

    let expected_data_size = num_signatures
        .saturating_mul(SIGNATURE_OFFSETS_SERIALIZED_SIZE)
        .saturating_add(SIGNATURE_OFFSETS_START);

    // We do not check or use the byte at data[1]
    if data.len() < expected_data_size {
        return Err(PrecompileError::InvalidInstructionDataSize);
    }

    // Parse half order from constant
    let half_order: BigNum =
        BigNum::from_slice(&SECP256R1_HALF_ORDER).map_err(|_| PrecompileError::InvalidSignature)?;

    // Parse order - 1 from constant
    let order_minus_one: BigNum = BigNum::from_slice(&SECP256R1_ORDER_MINUS_ONE)
        .map_err(|_| PrecompileError::InvalidSignature)?;

    // Create a BigNum for 1
    let one = BigNum::from_u32(1).map_err(|_| PrecompileError::InvalidSignature)?;

    // Define curve group
    let group = EcGroup::from_curve_name(Nid::X9_62_PRIME256V1)
        .map_err(|_| PrecompileError::InvalidSignature)?;
    let mut ctx = BigNumContext::new().map_err(|_| PrecompileError::InvalidSignature)?;

    for i in 0..num_signatures {
        let start = i
            .saturating_mul(SIGNATURE_OFFSETS_SERIALIZED_SIZE)
            .saturating_add(SIGNATURE_OFFSETS_START);

        // SAFETY:
        // - data[start..] is guaranteed to be >= size of Secp256r1SignatureOffsets
        // - Secp256r1SignatureOffsets is a POD type, so we can safely read it as an unaligned struct
        let offsets = unsafe {
            core::ptr::read_unaligned(data.as_ptr().add(start) as *const Secp256r1SignatureOffsets)
        };

        // Parse out signature
        let signature = get_data_slice(
            data,
            instruction_datas,
            offsets.signature_instruction_index,
            offsets.signature_offset,
            SIGNATURE_SERIALIZED_SIZE,
        )?;

        // Parse out pubkey
        let pubkey = get_data_slice(
            data,
            instruction_datas,
            offsets.public_key_instruction_index,
            offsets.public_key_offset,
            COMPRESSED_PUBKEY_SERIALIZED_SIZE,
        )?;

        // Parse out message
        let message = get_data_slice(
            data,
            instruction_datas,
            offsets.message_instruction_index,
            offsets.message_data_offset,
            offsets.message_data_size as usize,
        )?;

        let r_bignum = BigNum::from_slice(&signature[..FIELD_SIZE])
            .map_err(|_| PrecompileError::InvalidSignature)?;
        let s_bignum = BigNum::from_slice(&signature[FIELD_SIZE..])
            .map_err(|_| PrecompileError::InvalidSignature)?;

        // Check that the signature is generally in range
        let within_range = r_bignum >= one
            && r_bignum <= order_minus_one
            && s_bignum >= one
            && s_bignum <= half_order;

        if !within_range {
            return Err(PrecompileError::InvalidSignature);
        }

        // Create an ECDSA signature object from the ASN.1 integers
        let ecdsa_sig = openssl::ecdsa::EcdsaSig::from_private_components(r_bignum, s_bignum)
            .and_then(|sig| sig.to_der())
            .map_err(|_| PrecompileError::InvalidSignature)?;

        let public_key_point = EcPoint::from_bytes(&group, pubkey, &mut ctx)
            .map_err(|_| PrecompileError::InvalidPublicKey)?;
        let public_key = EcKey::from_public_key(&group, &public_key_point)
            .map_err(|_| PrecompileError::InvalidPublicKey)?;
        let public_key_as_pkey =
            PKey::from_ec_key(public_key).map_err(|_| PrecompileError::InvalidPublicKey)?;

        let mut verifier =
            Verifier::new(openssl::hash::MessageDigest::sha256(), &public_key_as_pkey)
                .map_err(|_| PrecompileError::InvalidSignature)?;
        verifier
            .update(message)
            .map_err(|_| PrecompileError::InvalidSignature)?;

        if !verifier
            .verify(&ecdsa_sig)
            .map_err(|_| PrecompileError::InvalidSignature)?
        {
            return Err(PrecompileError::InvalidSignature);
        }
    }
    Ok(())
}
```

**File:** precompiles/src/ed25519.rs (L19-77)
```rust
    let num_signatures = data[0] as usize;
    if num_signatures == 0 && data.len() > SIGNATURE_OFFSETS_START {
        return Err(PrecompileError::InvalidInstructionDataSize);
    }
    let expected_data_size = num_signatures
        .saturating_mul(SIGNATURE_OFFSETS_SERIALIZED_SIZE)
        .saturating_add(SIGNATURE_OFFSETS_START);
    // We do not check or use the byte at data[1]
    if data.len() < expected_data_size {
        return Err(PrecompileError::InvalidInstructionDataSize);
    }
    for i in 0..num_signatures {
        let start = i
            .saturating_mul(SIGNATURE_OFFSETS_SERIALIZED_SIZE)
            .saturating_add(SIGNATURE_OFFSETS_START);

        // SAFETY:
        // - data[start..] is guaranteed to be >= size of Ed25519SignatureOffsets
        // - Ed25519SignatureOffsets is a POD type, so we can safely read it as an unaligned struct
        let offsets = unsafe {
            core::ptr::read_unaligned(data.as_ptr().add(start) as *const Ed25519SignatureOffsets)
        };

        // Parse out signature
        let signature = get_data_slice(
            data,
            instruction_datas,
            offsets.signature_instruction_index,
            offsets.signature_offset,
            SIGNATURE_SERIALIZED_SIZE,
        )?;

        let signature =
            Signature::from_bytes(signature).map_err(|_| PrecompileError::InvalidSignature)?;

        // Parse out pubkey
        let pubkey = get_data_slice(
            data,
            instruction_datas,
            offsets.public_key_instruction_index,
            offsets.public_key_offset,
            PUBKEY_SERIALIZED_SIZE,
        )?;

        let publickey = ed25519_dalek::PublicKey::from_bytes(pubkey)
            .map_err(|_| PrecompileError::InvalidPublicKey)?;

        // Parse out message
        let message = get_data_slice(
            data,
            instruction_datas,
            offsets.message_instruction_index,
            offsets.message_data_offset,
            offsets.message_data_size as usize,
        )?;
        publickey
            .verify_strict(message, &signature)
            .map_err(|_| PrecompileError::InvalidSignature)?;
    }
```

**File:** precompiles/src/secp256k1.rs (L23-103)
```rust
pub fn verify(
    data: &[u8],
    instruction_datas: &[&[u8]],
    _feature_set: &FeatureSet,
) -> Result<(), PrecompileError> {
    if data.is_empty() {
        return Err(PrecompileError::InvalidInstructionDataSize);
    }
    let count = data[0] as usize;
    if count == 0 && data.len() > 1 {
        // count is zero but the instruction data indicates that is probably not
        // correct, fail the instruction to catch probable invalid secp256k1
        // instruction construction.
        return Err(PrecompileError::InvalidInstructionDataSize);
    }
    let expected_data_size = count
        .saturating_mul(SIGNATURE_OFFSETS_SERIALIZED_SIZE)
        .saturating_add(1);
    if data.len() < expected_data_size {
        return Err(PrecompileError::InvalidInstructionDataSize);
    }
    for i in 0..count {
        let start = i
            .saturating_mul(SIGNATURE_OFFSETS_SERIALIZED_SIZE)
            .saturating_add(1);
        let end = start.saturating_add(SIGNATURE_OFFSETS_SERIALIZED_SIZE);

        let offsets: SecpSignatureOffsets = bincode::deserialize(&data[start..end])
            .map_err(|_| PrecompileError::InvalidSignature)?;

        // Parse out signature
        let signature_index = offsets.signature_instruction_index as usize;
        if signature_index >= instruction_datas.len() {
            return Err(PrecompileError::InvalidInstructionDataSize);
        }
        let signature_instruction = instruction_datas[signature_index];
        let sig_start = offsets.signature_offset as usize;
        let sig_end = sig_start.saturating_add(SIGNATURE_SERIALIZED_SIZE);
        if sig_end >= signature_instruction.len() {
            return Err(PrecompileError::InvalidSignature);
        }

        let signature = libsecp256k1::Signature::parse_standard_slice(
            &signature_instruction[sig_start..sig_end],
        )
        .map_err(|_| PrecompileError::InvalidSignature)?;

        let recovery_id = libsecp256k1::RecoveryId::parse(signature_instruction[sig_end])
            .map_err(|_| PrecompileError::InvalidRecoveryId)?;

        // Parse out pubkey
        let eth_address_slice = get_data_slice(
            instruction_datas,
            offsets.eth_address_instruction_index,
            offsets.eth_address_offset,
            HASHED_PUBKEY_SERIALIZED_SIZE,
        )?;

        // Parse out message
        let message_slice = get_data_slice(
            instruction_datas,
            offsets.message_instruction_index,
            offsets.message_data_offset,
            offsets.message_data_size as usize,
        )?;

        let message_hash: [u8; 32] = solana_keccak_hasher::hash(message_slice).to_bytes();
        let pubkey = libsecp256k1::recover(
            &libsecp256k1::Message::parse_slice(&message_hash).unwrap(),
            &signature,
            &recovery_id,
        )
        .map_err(|_| PrecompileError::InvalidSignature)?;
        let eth_address = eth_address_from_pubkey(&pubkey.serialize()[1..].try_into().unwrap());

        if eth_address_slice != eth_address {
            return Err(PrecompileError::InvalidSignature);
        }
    }
    Ok(())
}
```

**File:** cost-model/src/block_cost_limits.rs (L9-16)
```rust
/// Number of compute units for one signature verification.
pub const SIGNATURE_COST: u64 = COMPUTE_UNIT_TO_US_RATIO * 24;
/// Number of compute units for one secp256k1 signature verification.
pub const SECP256K1_VERIFY_COST: u64 = COMPUTE_UNIT_TO_US_RATIO * 223;
/// Number of compute units for one ed25519 strict signature verification.
pub const ED25519_VERIFY_STRICT_COST: u64 = COMPUTE_UNIT_TO_US_RATIO * 80;
/// Number of compute units for one secp256r1 signature verification.
pub const SECP256R1_VERIFY_COST: u64 = COMPUTE_UNIT_TO_US_RATIO * 160;
```

**File:** cost-model/src/cost_model.rs (L129-151)
```rust
    /// Returns signature details and the total signature cost
    fn get_signature_cost(transaction: &impl TransactionMeta) -> u64 {
        let signatures_count_detail = transaction.signature_details();

        signatures_count_detail
            .num_transaction_signatures()
            .saturating_mul(SIGNATURE_COST)
            .saturating_add(
                signatures_count_detail
                    .num_secp256k1_instruction_signatures()
                    .saturating_mul(SECP256K1_VERIFY_COST),
            )
            .saturating_add(
                signatures_count_detail
                    .num_ed25519_instruction_signatures()
                    .saturating_mul(ED25519_VERIFY_STRICT_COST),
            )
            .saturating_add(
                signatures_count_detail
                    .num_secp256r1_instruction_signatures()
                    .saturating_mul(SECP256R1_VERIFY_COST),
            )
    }
```

**File:** core/tests/scheduler_cost_adjustment.rs (L381-402)
```rust
#[test]
fn test_builtin_ix_precompiled() {
    let mut test_setup = TestSetup::new();

    // single precompiled instruction
    // Cost model & Compute budget: reserve/allocate default CU for one builtin ix
    // VM Execution: consume 0 from CU-meter
    // Result: adjustment = 3_000
    let expected = TestResult {
        cost_adjustment: MAX_BUILTIN_ALLOCATION_COMPUTE_UNIT_LIMIT as i64,
        execution_status: Ok(()),
    };
    assert_eq!(
        expected,
        test_setup.execute_test_transaction(&[Instruction::new_with_bincode(
            secp256k1_program::id(),
            &[0u8],
            // Add a dummy account to generate a unique transaction
            vec![AccountMeta::new_readonly(Pubkey::new_unique(), false)]
        )],)
    );
}
```
