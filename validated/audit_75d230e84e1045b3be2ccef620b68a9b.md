Based on the investigation, this maps to a valid, fee-model-consistent DoS analog in the precompile signature-verification path.

### Title
Precompile Signature Verification (ed25519/secp256k1/secp256r1) Consumes Zero Compute Units, Enabling Underpriced CPU-Bound DoS - ([File: program-runtime/src/invoke_context.rs])

### Summary
`InvokeContext::process_message` dispatches precompile program IDs (`ed25519`, `secp256k1`, `secp256r1`) to `process_precompile`, which performs expensive cryptographic verification (ECDSA recovery/signature checks, Ed25519 `verify_strict`, secp256r1 OpenSSL ECDSA) but never charges any compute units for the work performed, unlike the sibling `process_instruction`/`process_executable_chain` path that meters pre/post remaining compute units for every builtin/BPF instruction.

### Finding Description
In `process_message` [1](#0-0) , each top-level instruction is dispatched either to `process_precompile` (for `is_precompile(program_id)`) or `process_instruction` (which meters compute via `compute_units_consumed`). `compute_units_consumed` is initialized to `0` for each iteration and is only mutated inside `process_instruction`/`process_executable_chain`, via `pre_remaining_units`/`post_remaining_units` deltas [2](#0-1) . The precompile branch, `process_precompile`, never touches `compute_units_consumed` [3](#0-2) , so it always contributes `0` to `accumulated_consumed_units`.

Each precompile's `verify()` function loops over an attacker-declared `count`/`num_signatures` field (`data[0]`, up to `u8::MAX` = 255) and performs one full cryptographic verification per iteration:
- `precompiles/src/secp256k1.rs` loops `0..count` performing `libsecp256k1::recover` + Keccak hashing per iteration [4](#0-3) .
- `precompiles/src/ed25519.rs` loops `0..num_signatures` performing `ed25519_dalek` `verify_strict` per iteration [5](#0-4) .
- `precompiles/src/secp256r1.rs` loops `0..num_signatures` (capped at 8) performing OpenSSL ECDSA verification per iteration [6](#0-5) .

Meanwhile, the fee actually charged to the transaction's fee payer for these signatures is computed by `solana_fee::calculate_signature_fee`, which multiplies the *count* of precompile signatures by `lamports_per_signature` — the same flat per-signature rate charged for a normal Ed25519 transaction signature verification [7](#0-6) . This "signature fee" bears no relationship to compute units and is not gated by the transaction's compute-unit budget; it is a fixed per-signature lamport charge regardless of how CPU-expensive the underlying cryptographic operation is (secp256k1 ECDSA recovery and secp256r1 OpenSSL verification are meaningfully more expensive than a plain Ed25519 check, yet all are billed identically per "signature").

Because `process_precompile` never calls `compute_meter.consume_checked(...)` and never updates `compute_units_consumed`, this expensive cryptographic work is entirely free from the perspective of the transaction's compute unit budget — it does not count against `compute_unit_limit`, and does not reduce the "remaining compute units" available for the rest of the transaction's execution. A single transaction can chain multiple secp256k1/ed25519/secp256r1 precompile instructions (each declaring up to 255 signatures within packet-size limits), and the leader will execute all of that cryptography inside `process_message` at essentially zero compute-unit cost, while other instructions in the same transaction still have their full compute budget available.

### Impact Explanation
The cost-model's `SIGNATURE_COST`/`SECP256K1_VERIFY_COST`/`ED25519_VERIFY_STRICT_COST` constants (in `cost-model/src/cost_model.rs`) are advisory quantities used purely for leader-side block-packing/scheduling heuristics (`CostModel::estimate_cost`), not an actual enforced compute-unit charge inside `InvokeContext`. The real, VM-enforced compute-unit budget (used to bound wall-clock CPU time per transaction) is untouched by this cryptography. This allows a sender to craft transactions that consume disproportionate leader CPU time relative to the compute units actually metered and relative to the fee paid, since the `lamports_per_signature`-based signature fee does not scale with the CPU cost differential between the three signature schemes (secp256k1/secp256r1 recovery/verification is materially more CPU-intensive than incrementing a counter, yet costs the same per unit as a basic signature). Repeated submission of many such transactions (each cheap in compute-unit terms and thus able to be packed densely against the block's compute limit) can be used to inflate leader-side signature-verification CPU load beyond what the compute-unit accounting and block cost limits are designed to bound, since the actual crypto work escapes the mechanism (compute meter) that both prices and bounds execution time per transaction.

### Likelihood Explanation
High likelihood of reachability: any unprivileged sender can construct a legacy or versioned transaction containing `secp256k1_program`/`ed25519_program`/`secp256r1_program` instructions with an inflated but internally-consistent signature count and valid signature/message offsets (all straightforward, publicly documented instruction formats used by `new_secp256k1_instruction_with_signature`/`new_ed25519_instruction_with_signature`). No special privileges, staking, or leader coordination are required — this is a standard user-submitted-transaction path (`Bank::verify_transaction` → `InvokeContext::process_message`) exercised on every validator that processes the transaction, not just the leader that packs it.

### Recommendation
Charge compute units for precompile verification within `process_precompile`/`InvokeContext`, proportional to the number of signatures actually processed and to the relative cost of each signature scheme (mirroring `SIGNATURE_COST`/`SECP256K1_VERIFY_COST`/`ED25519_VERIFY_STRICT_COST`/`SECP256R1_VERIFY_COST` used in the cost model), and enforce this against `compute_meter.consume_checked` the same way `process_executable_chain` does for builtin/BPF instructions. This ensures the leader-side scheduling estimate and the actual VM-enforced compute budget agree, preventing compute-unit-free execution of expensive cryptographic verification loops.

### Proof of Concept
1. Construct a transaction whose instructions include one or more `secp256k1_program` (or `ed25519_program`/`secp256r1_program`) instructions, each with `data[0]` set to the maximum signature count supportable within the packet size (bounded by `PACKET_DATA_SIZE`, referencing other instructions' data via `signature_instruction_index`/`eth_address_instruction_index`/`message_instruction_index` offsets to avoid inflating the precompile instruction's own data size), following the layout used by `new_secp256k1_instruction_with_signature` in `precompiles/src/secp256k1.rs` tests.
2. Submit the transaction with `set_compute_unit_limit` set to a minimal value and observe (via `execute_timings`/tracing) that `accumulated_consumed_units` reported by `process_message` does not increase for the precompile instruction, despite `secp256k1::verify`/`ed25519::verify` performing `count` full cryptographic verifications inside `process_precompile` (verifiable by instrumenting `precompiles/src/secp256k1.rs::verify` and `program-runtime/src/invoke_context.rs::process_precompile`).
3. Compare wall-clock time spent in `process_precompile` for high signature counts vs. the compute units actually deducted from the transaction's budget — the discrepancy demonstrates uncharged, effectively free CPU-bound work bounded only by transaction/packet size rather than by the compute-unit-based enforcement mechanism.

### Citations

**File:** program-runtime/src/invoke_context.rs (L503-525)
```rust
    pub fn process_message(
        &mut self,
        message: &'ix_data impl SVMMessage,
        execute_timings: &mut ExecuteTimings,
        accumulated_consumed_units: &mut u64,
    ) -> Result<(), (u8, InstructionError)> {
        self.prepare_top_level_instructions(message)?;

        for (top_level_instruction_index, (program_id, instruction)) in
            message.program_instructions_iter().enumerate()
        {
            let mut compute_units_consumed = 0;
            let (result, process_instruction_us) = measure_us!({
                if self.is_precompile(program_id) {
                    self.process_precompile(
                        program_id,
                        instruction.data,
                        message.instructions_iter().map(|ix| ix.data),
                    )
                } else {
                    self.process_instruction(&mut compute_units_consumed, execute_timings)
                }
            });
```

**File:** program-runtime/src/invoke_context.rs (L616-632)
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

**File:** program-runtime/src/invoke_context.rs (L672-729)
```rust
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
```

**File:** precompiles/src/secp256k1.rs (L44-103)
```rust
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

**File:** precompiles/src/ed25519.rs (L30-79)
```rust
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
    Ok(())
}
```

**File:** precompiles/src/secp256r1.rs (L59-139)
```rust
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
```

**File:** fee/src/lib.rs (L41-56)
```rust
/// Calculate fees from signatures.
pub fn calculate_signature_fee(
    SignatureCounts {
        num_transaction_signatures,
        num_ed25519_signatures,
        num_secp256k1_signatures,
        num_secp256r1_signatures,
    }: SignatureCounts,
    lamports_per_signature: u64,
) -> u64 {
    let signature_count = num_transaction_signatures
        .saturating_add(num_ed25519_signatures)
        .saturating_add(num_secp256k1_signatures)
        .saturating_add(num_secp256r1_signatures);
    signature_count.saturating_mul(lamports_per_signature)
}
```
