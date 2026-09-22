### Title
Precompile signature verification consumes zero compute units at execution time, decoupling actual CPU cost from the transaction's compute-unit budget - (File: `program-runtime/src/invoke_context.rs`)

### Summary
`InvokeContext::process_message` dispatches precompile instructions (`secp256k1`, `ed25519`, `secp256r1`) to `process_precompile`, which never calls `self.consume(..)` on the compute meter, leaving `compute_units_consumed` at `0` for that top-level instruction regardless of how many signatures the instruction asks the precompile to verify.

### Finding Description
In `process_message`, for each top-level instruction the code branches on whether the target program is a precompile: [1](#0-0) 

For the precompile branch, `process_precompile` is invoked directly, and unlike `process_instruction` (the non-precompile path), it takes no `compute_units_consumed` output parameter and performs no metering: [2](#0-1) 

The actual cryptographic work done inside a precompile "verify" call is proportional to the attacker-controlled `num_signatures`/`count` byte read from instruction data (up to `u8::MAX = 255` for `ed25519`/`secp256k1`, up to `8` for `secp256r1`), and each iteration performs expensive asymmetric-crypto operations (`ed25519_dalek::verify_strict`, `libsecp256k1::recover`, or OpenSSL ECDSA `EcKey`/`Verifier` operations): [3](#0-2) [4](#0-3) [5](#0-4) 

The block-level cost model does account for these signature counts for scheduling/fee purposes via `SIGNATURE_COST`/`SECP256K1_VERIFY_COST`/`ED25519_VERIFY_STRICT_COST`/`SECP256R1_VERIFY_COST` and `get_signature_cost`: [6](#0-5) [7](#0-6) 

However, that estimate is used only for cost-tracking/scheduling (`CostTracker`, block cost limits) — it is not enforced against the transaction's own `compute_unit_limit` during actual execution. The test suite explicitly documents this gap: a transaction containing a single precompiled instruction is credited only the flat `MAX_BUILTIN_ALLOCATION_COMPUTE_UNIT_LIMIT` (3,000 CU) for cost adjustment purposes, and "VM Execution: consume 0 from CU-meter": [8](#0-7) 

So the per-transaction compute budget (which a client can set arbitrarily low via `ComputeBudgetInstruction::SetComputeUnitLimit`) has no bearing on how much real CPU time is spent verifying precompile signatures during execution — the compute meter is simply never decremented for that work.

### Impact Explanation
An attacker can construct a transaction with several precompile instructions (bounded by `MAX_INSTRUCTION_TRACE_LENGTH = 64` top-level instructions and by transaction serialized-size limits), each declaring up to 255 (`secp256k1`/`ed25519`) or 8 (`secp256r1`) signatures to verify, while requesting a minimal `compute_unit_limit`. Because `process_precompile` performs no CU metering, this expensive verification work executes fully regardless of the requested/allocated compute budget, unlike ordinary SBF/native instruction execution which is bounded by the CU meter and aborts once exhausted. This breaks the fundamental invariant that a transaction's declared compute-unit limit bounds the CPU work a validator must perform to execute it, and lets a low-fee, low-CU-limit transaction perform disproportionately expensive cryptographic work (elliptic-curve signature verification/recovery) during bank replay/execution on every validator in the cluster, which is a resource-exhaustion vector analogous to the referenced Starlite multipart-parsing DoS (unbounded resource consumption reachable from a single request/transaction).

Note: the block-level `CostTracker`/cost-model accounting for signature counts does provide some mitigation for scheduler-side block-building throttling, and the instruction/packet-size limits (`MAX_INSTRUCTION_TRACE_LENGTH`, `PACKET_DATA_SIZE`) cap the absolute worst case per transaction. I could not fully verify within the available context whether this metering gap is a known/intentional design decision (precompiles are commonly documented as "free" in Solana because their cost is charged via the flat builtin allocation plus the cost-model signature multipliers, rather than the VM compute meter) or represents an unpatched regression; this distinction affects whether it should be treated as a fresh finding versus a documented, already-mitigated design tradeoff.

### Likelihood Explanation
Reaching this code path only requires submitting a single, otherwise well-formed signed transaction containing precompile instructions with a high declared signature count and a low compute-unit limit — no privileged access, special validator role, or malicious peer/leader behavior is required. The instruction data needed to declare many signatures is compact (fixed-size offset structs), so the attacker-controlled amplification factor (verification cost vs. instruction/data size) is significant, though bounded by transaction size and per-instruction data limits.

### Recommendation
Meter precompile verification against the transaction's compute-unit budget in `process_precompile`/`process_message`, e.g., by charging a cost per signature verified (consistent with `SECP256K1_VERIFY_COST`/`ED25519_VERIFY_STRICT_COST`/`SECP256R1_VERIFY_COST`) into `compute_units_consumed`/the compute meter, so that a transaction with an insufficient `compute_unit_limit` fails with a compute-budget-exceeded error rather than performing unmetered cryptographic work. This would make the CU meter, not just the leader-side cost model, the actual gatekeeper for this work.

### Proof of Concept
Conceptual PoC (requires a running validator/test harness to fully confirm, not verifiable purely from static code reading):
1. Build a transaction with several `secp256k1_program`/`ed25519_program` instructions, each with `num_signatures` set to 255 and correspondingly-sized (but structurally minimal/invalid or reused) offset data, up to the `MAX_INSTRUCTION_TRACE_LENGTH` and packet-size limits.
2. Attach a `ComputeBudgetInstruction::SetComputeUnitLimit` instruction requesting a very low CU limit (e.g., 1 CU).
3. Submit the transaction; observe that despite the negligible declared CU limit, the validator still performs the full O(255 × instructions) elliptic-curve verification work in `process_precompile` (confirmed by `compute_units_consumed` remaining `0` for precompile instructions per `core/tests/scheduler_cost_adjustment.rs`'s `test_builtin_ix_precompiled`), i.e., the cost is not bounded by the requested compute budget.

### Citations

**File:** program-runtime/src/invoke_context.rs (L511-525)
```rust
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

**File:** precompiles/src/ed25519.rs (L30-77)
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

**File:** precompiles/src/secp256r1.rs (L59-140)
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
}
```

**File:** cost-model/src/block_cost_limits.rs (L7-20)
```rust
/// Cluster averaged compute unit to micro-sec conversion rate
pub const COMPUTE_UNIT_TO_US_RATIO: u64 = 30;
/// Number of compute units for one signature verification.
pub const SIGNATURE_COST: u64 = COMPUTE_UNIT_TO_US_RATIO * 24;
/// Number of compute units for one secp256k1 signature verification.
pub const SECP256K1_VERIFY_COST: u64 = COMPUTE_UNIT_TO_US_RATIO * 223;
/// Number of compute units for one ed25519 strict signature verification.
pub const ED25519_VERIFY_STRICT_COST: u64 = COMPUTE_UNIT_TO_US_RATIO * 80;
/// Number of compute units for one secp256r1 signature verification.
pub const SECP256R1_VERIFY_COST: u64 = COMPUTE_UNIT_TO_US_RATIO * 160;
/// Number of compute units for one write lock
pub const WRITE_LOCK_UNITS: u64 = COMPUTE_UNIT_TO_US_RATIO * 10;
/// Number of data bytes per compute units
pub const INSTRUCTION_DATA_BYTES_COST: u64 = 140 /*bytes per us*/ / COMPUTE_UNIT_TO_US_RATIO;
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
