Found a critical confirmation: `process_precompile` in `program-runtime/src/invoke_context.rs:616-631` is invoked from `process_message` (line 516-521) as a special-case branch that **completely bypasses compute unit metering**. Unlike `process_instruction` (the normal builtin/BPF path), which wraps execution in `self.compute_meter.consume_checked(...)` (see `declare_process_instruction!` macro, lines 84-96) and measures `compute_units_consumed` via `pre_remaining_units.saturating_sub(post_remaining_units)` (line 725), the precompile branch never touches `compute_units_consumed` — it stays `0` for the entire top-level instruction. This is explicitly confirmed by the test `test_builtin_ix_precompiled` in `core/tests/scheduler_cost_adjustment.rs:381-402`, whose comment states: *"VM Execution: consume 0 from CU-meter"* and asserts `cost_adjustment = MAX_BUILTIN_ALLOCATION_COMPUTE_UNIT_LIMIT` (i.e., the entire estimated cost is refunded/never spent).

### Title
Unbounded, unmetered CPU consumption in precompile signature verification (secp256k1/ed25519/secp256r1) - Uncontrolled resource consumption analog to CVE-2024-23952 (File: `program-runtime/src/invoke_context.rs`, `precompiles/src/secp256k1.rs`, `precompiles/src/ed25519.rs`, `precompiles/src/secp256r1.rs`)

### Summary
The `secp256k1`, `ed25519`, and `secp256r1` precompile programs perform expensive cryptographic verification work (ECDSA/EdDSA/secp256r1 signature recovery or verification, Keccak/SHA-256 hashing) per signature-offset entry declared in instruction data, but this work is executed via `InvokeContext::process_precompile` [1](#0-0)  which is dispatched from `process_message` outside of the normal compute-metered `process_instruction` path [2](#0-1) . No `compute_meter.consume_checked` call is made for precompile verification, so `compute_units_consumed` for that top-level instruction remains `0`, confirmed by the test comment "VM Execution: consume 0 from CU-meter" [3](#0-2) . The upfront cost-model charge (`SECP256K1_VERIFY_COST`, `ED25519_VERIFY_STRICT_COST`, `SECP256R1_VERIFY_COST` per declared signature count) [4](#0-3)  is a fixed per-signature estimate that does not scale with the actual message size being hashed/verified, and this actual runtime cost is entirely disconnected from the transaction's `compute_unit_limit`/`MAX_COMPUTE_UNIT_LIMIT` accounting used to bound program execution.

### Finding Description
Each precompile's `verify()` function loops over `count`/`num_signatures` (attacker-controlled, up to 255 for secp256k1/ed25519, up to 8 for secp256r1) and, for each entry, performs full cryptographic work over a `message_data_size` (attacker-controlled `u16`, up to the size of any instruction's data in the transaction) [5](#0-4) [6](#0-5) [7](#0-6) . Because `message_instruction_index`/`eth_address_instruction_index`/etc. can point to the *same* underlying instruction data slice for every iteration, a single ~4KB (v1 transaction) or ~1.2KB (legacy) instruction data payload can be referenced by up to 255 signature-offset entries, causing the same bytes to be repeatedly hashed and elliptic-curve-verified (secp256r1 additionally does full ECDSA verification with OpenSSL `BigNum`/`EcPoint`/`Verifier` operations per entry) [8](#0-7) .

This work is dispatched via `InvokeContext::process_precompile`, which unlike the normal builtin path never calls `compute_meter.consume_checked`, and the surrounding `process_message` loop never accumulates a non-zero `compute_units_consumed` for it [9](#0-8) . The cost model only charges a fixed cost per declared signature count (`SECP256K1_VERIFY_COST`, etc.) at admission time for scheduling/fee purposes [10](#0-9) , but this cost is not tied to the message size being processed, and — critically — it is not enforced as a hard limit on the *actual* CPU work performed during precompile verification the way `compute_unit_limit` bounds BPF/builtin execution. A single transaction can therefore force validators (including non-leader replay/vote nodes) to perform substantial CPU work (up to ~255 iterations × ~4KB Keccak/secp256r1 elliptic-curve verify operations) while paying only the flat per-signature fee, analogous to how a small ZIP file can force disproportionate decompression work in the reported Superset CVE.

### Impact Explanation
This is a resource-consumption amplification, not a fund-movement or consensus-divergence bug on its own: all validators execute the identical deterministic verification, so it does not cause consensus divergence by itself. However, it allows a single signed transaction sender to impose disproportionate, largely unmetered CPU cost on every validator (leader and non-leader) that must sanitize/replay the transaction, particularly relevant for secp256r1's OpenSSL-based signature verification which is markedly more expensive than a hash. Because the actual cost is decoupled from the compute-unit metering that normally throttles single-transaction execution, this weakens the intended "pay-for-what-you-use" DoS protection that the cost model is meant to provide.

### Likelihood Explanation
Likelihood is moderate: any unprivileged signer can construct such a transaction (secp256k1/ed25519 up to 255 signature entries, secp256r1 up to 8) referencing large instruction data, without needing any special permissions, cooperating validators, or program deployment. The main constraint is the transaction size limit (~1232–4096 bytes), which bounds the amount of unique message data but does not prevent the attacker from having many signature-offset entries reuse (alias) the same data multiple times.

### Recommendation
Meter precompile verification against the transaction's compute budget proportionally to work performed (e.g., charge compute units based on `num_signatures * message_data_size` rather than a flat per-signature cost, and route that cost through `compute_meter.consume_checked` inside `process_precompile` the same way builtin/BPF execution is metered) so `compute_units_consumed` reflects actual precompile work and is bounded by `MAX_COMPUTE_UNIT_LIMIT`.

### Proof of Concept
1. Construct a v1 transaction with one `secp256r1_program` (or `secp256k1_program`/`ed25519_program`) instruction whose `data[0]` (count) is set to the maximum (8 for secp256r1, 255 for the others).
2. Populate `SignatureOffsets` entries such that each entry's `message_instruction_index`/`message_data_offset`/`message_data_size` all point at the same large (near max instruction-data-length) payload embedded in another instruction of the transaction.
3. Submit the transaction. `agave_precompiles::secp256r1::verify` (or `secp256k1`/`ed25519`) will loop `num_signatures` times, each time performing a full OpenSSL ECDSA verify (or Keccak hash / Ed25519 verify) over the aliased message, per `precompiles/src/secp256r1.rs:59-139`.
4. Observe via `process_message`/`process_precompile` in `program-runtime/src/invoke_context.rs:503-631` that `compute_units_consumed` remains `0` for this instruction despite the real CPU time spent, matching the behavior asserted in `test_builtin_ix_precompiled` (`core/tests/scheduler_cost_adjustment.rs:381-402`).

### Citations

**File:** program-runtime/src/invoke_context.rs (L503-551)
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

            *accumulated_consumed_units =
                accumulated_consumed_units.saturating_add(compute_units_consumed);
            // The per_program_timings are only used for metrics reporting at the trace
            // level, so they should only be accumulated when trace level is enabled.
            if log::log_enabled!(log::Level::Trace) {
                execute_timings.details.accumulate_program(
                    program_id,
                    process_instruction_us,
                    compute_units_consumed,
                    result.is_err(),
                );
            }
            self.timings = {
                execute_timings.details.accumulate(&self.timings);
                ExecuteDetailsTimings::default()
            };
            execute_timings
                .execute_accessories
                .process_instructions
                .total_us += process_instruction_us;

            result.map_err(|err| (top_level_instruction_index as u8, err))?;
        }
        Ok(())
    }
```

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

**File:** precompiles/src/secp256k1.rs (L44-101)
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
```

**File:** precompiles/src/ed25519.rs (L30-78)
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
