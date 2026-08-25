### Title
Precompile signature verification (secp256k1 / ed25519 / secp256r1) performs unbounded cryptographic work with zero compute-unit metering, allowing underpriced CPU exhaustion analogous to unbounded password-hashing DoS - ([File: precompiles/src/secp256r1.rs])

### Summary
The `ed25519`, `secp256k1`, and `secp256r1` precompile `verify()` functions perform expensive cryptographic verification (Ed25519 signature checks, secp256k1 ECDSA recovery + Keccak, and OpenSSL BigNum/EC-point ECDSA verification) but never charge the SVM `compute_meter`. The cost model instead allocates these builtins a fixed, capped compute-unit budget (`MAX_BUILTIN_ALLOCATION_COMPUTE_UNIT_LIMIT` = 3,000 CU) regardless of the actual number of signatures or message sizes processed. This mirrors the reported Nextcloud bug class: an operation whose real CPU/resource cost scales with attacker-controlled input length is not properly bounded or priced by the system's cost-accounting mechanism.

### Finding Description
Each precompile's `verify()` loops over `num_signatures` (up to 255 for ed25519/secp256k1 since it's read from a single `u8`, up to 8 for secp256r1) and, for every signature, performs a full cryptographic verification against a message slice whose size/location is attacker-specified via `Ed25519SignatureOffsets`/`SecpSignatureOffsets`/`Secp256r1SignatureOffsets`: [1](#0-0) [2](#0-1) 

None of these loops call `invoke_context.compute_meter.consume_checked(...)` — contrast this with ordinary syscalls such as `SyscallHash` or `SyscallMemcmp`, which explicitly meter cost per byte processed: [3](#0-2) [4](#0-3) 

Instead, when a precompile instruction is executed, its actual work is invoked via `process_precompile`, which is unmetered by any CU consumption inside the precompile's own logic: [5](#0-4) 

Cost-model accounting for these builtins treats them as a fixed low-cost allocation independent of real work performed, confirmed by a scheduler cost-adjustment test showing that a precompiled instruction consumes `0` CU from the meter and is refunded the entire flat allocation: [6](#0-5) 

Each precompile instruction can reference up to 255 (ed25519/secp256k1) or 8 (secp256r1) signatures, and a single transaction may contain up to `MAX_INSTRUCTION_TRACE_LENGTH` (64) instructions: [7](#0-6) 
All of these signatures can point (via `signature_instruction_index`/`message_instruction_index` offsets) at the *same* large message data embedded elsewhere in the transaction, so verification cost scales as `num_instructions * num_signatures_per_instruction * message_verification_cost`, while the CU/cost-model charge stays flat.

Notably, the sigverify (TPU ingest) stage only verifies the outer transaction's own Ed25519 signatures against the message; it never invokes the precompile `verify()` logic at all: [8](#0-7) 
This means the disproportionately expensive work happens later, during transaction execution (banking/replay), where it is only "paid for" via the flat per-builtin-instruction cost-model allocation rather than a cost proportional to the cryptographic work actually performed.

### Impact Explanation
An attacker can construct a transaction (within `PACKET_DATA_SIZE`/`v1::MAX_TRANSACTION_SIZE` limits) containing several `secp256r1`/`ed25519`/`secp256k1` precompile instructions, each declaring the maximum signature count and referencing the largest available message slice in the transaction. Because the cost model does not scale CU charges with signature count or message size for these builtins, leaders/validators can be forced to spend CPU time on hundreds of EC/ECDSA/Ed25519 verifications (secp256r1's OpenSSL BigNum/EC-point path is especially expensive) for a cost that is not commensurate with the compute-unit budget consumed. Submitted en masse (or replayed cheaply if the transaction later fails for unrelated reasons, e.g. account-not-found), this creates a CPU amplification vector during transaction processing — an analog of the "unbounded input causing disproportionate CPU/memory cost" DoS class described in the reference report, occurring in a builtin-program/precompile code path explicitly in scope.

### Likelihood Explanation
Constructing such a transaction requires no privileged access — any user can submit an ordinary transaction with multiple precompile instructions and crafted offsets pointing at shared, maximally sized message data, all within existing packet-size and instruction-count limits. The relevant checks (`num_signatures <= 255` or `<= 8`, `MAX_INSTRUCTION_TRACE_LENGTH`, packet size caps) bound the total amount of amplification but do not tie the cost-model/CU charge to the real cryptographic work, so the underlying metering gap is reliably reachable by an unprivileged actor.

### Recommendation
Meter precompile verification cost proportional to the actual cryptographic work performed (number of signatures processed and bytes of message data hashed/verified) by charging the transaction's compute budget (or an equivalent cost-model factor) inside `verify_if_precompile`/`process_precompile`, similar to how `SyscallHash` and memory-operation syscalls charge cost proportional to input size, rather than relying solely on the flat `MAX_BUILTIN_ALLOCATION_COMPUTE_UNIT_LIMIT` allocation.

### Proof of Concept
1. Construct a transaction containing up to `MAX_INSTRUCTION_TRACE_LENGTH` (64) `secp256r1_program`/`ed25519_program` instructions.
2. In each instruction's data, set `num_signatures` to the maximum allowed (8 for secp256r1, 255 for ed25519/secp256k1) and set every `Secp256r1SignatureOffsets`/`Ed25519SignatureOffsets` entry's `message_instruction_index`/`message_data_offset`/`message_data_size` to point at the largest instruction-data blob present elsewhere in the same transaction (bounded only by `PACKET_DATA_SIZE`/`v1::MAX_TRANSACTION_SIZE`).
3. Submit the transaction; during execution each precompile instruction triggers `num_signatures` full EC/ECDSA/Ed25519 verifications against the shared large message, none of which are charged to the compute meter, while the cost model only allocates the flat `MAX_BUILTIN_ALLOCATION_COMPUTE_UNIT_LIMIT` (3,000 CU) per instruction — confirmed by `precompiles/src/secp256r1.rs`'s verification loop and the zero-CU-consumption behavior demonstrated in `core/tests/scheduler_cost_adjustment.rs::test_builtin_ix_precompiled`.

### Citations

**File:** precompiles/src/ed25519.rs (L19-29)
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
```

**File:** precompiles/src/secp256r1.rs (L26-41)
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
```

**File:** syscalls/src/lib.rs (L2759-2778)
```rust
        if vals_len > 0 {
            let vals = translate_slice::<VmSlice<u8>>(
                memory_mapping,
                vals_addr,
                vals_len,
                check_aligned,
            )?;

            for val in vals.iter() {
                let bytes = translate_vm_slice(val, memory_mapping, check_aligned)?;
                let cost = mem_op_base_cost.max(
                    hash_byte_cost.saturating_mul(
                        val.len()
                            .checked_div(2)
                            .expect("div by non-zero literal"),
                    ),
                );
                invoke_context.compute_meter.consume_checked(cost)?;
                hasher.hash(bytes);
            }
```

**File:** syscalls/src/mem_ops.rs (L3-9)
```rust
fn mem_op_consume(invoke_context: &mut InvokeContext, n: u64) -> Result<(), Error> {
    let compute_cost = invoke_context.get_execution_cost();
    let cost = compute_cost.mem_op_base_cost.max(
        n.checked_div(compute_cost.cpi_bytes_per_unit)
            .unwrap_or(u64::MAX),
    );
    invoke_context.compute_meter.consume_checked(cost)
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

**File:** runtime/src/bank/tests.rs (L9539-9572)
```rust
#[test]
fn test_verify_transactions_instruction_limit() {
    let GenesisConfigInfo { genesis_config, .. } =
        create_genesis_config_with_leader(42, &solana_pubkey::new_rand(), 42);
    let bank = Bank::new_for_tests(&genesis_config);

    let recent_blockhash = Hash::new_unique();
    let keypair = Keypair::new();
    let pubkey = keypair.pubkey();
    let ix_count = 65;
    let ixs: Vec<_> = std::iter::repeat_with(|| CompiledInstruction {
        program_id_index: 1,
        accounts: vec![0],
        data: vec![],
    })
    .take(ix_count)
    .collect();
    let message = Message::new_with_compiled_instructions(
        1,
        0,
        1,
        vec![pubkey, Pubkey::new_unique()],
        recent_blockhash,
        ixs,
    );
    let tx = Transaction::new(&[&keypair], message, recent_blockhash);

    assert!(bincode::serialized_size(&tx).unwrap() <= PACKET_DATA_SIZE as u64);

    assert_matches!(
        bank.verify_transaction(tx.into(), TransactionVerificationMode::FullVerification),
        Err(TransactionError::SanitizeFailure)
    );
}
```

**File:** perf/src/sigverify.rs (L20-63)
```rust
fn verify_packet(packet: &mut PacketRefMut, reject_non_vote: bool, enable_tx_v1: bool) -> bool {
    // If this packet was already marked as discard, drop it
    if packet.meta().discard() {
        return false;
    }

    let Some(data) = packet.data(..) else {
        return false;
    };

    let (is_simple_vote_tx, verified) = {
        let Ok(view) = SanitizedTransactionView::try_new_sanitized(data, &sanitize_config()) else {
            return false;
        };

        if !enable_tx_v1 && matches!(view.version(), TransactionVersion::V1) {
            return false;
        }

        let is_simple_vote_tx = is_simple_vote_transaction_view(&view);
        if reject_non_vote && !is_simple_vote_tx {
            (is_simple_vote_tx, false)
        } else {
            let signatures = view.signatures();
            if signatures.is_empty() {
                (is_simple_vote_tx, false)
            } else {
                let message = view.message_data();
                let static_account_keys = view.static_account_keys();
                let verified = signatures
                    .iter()
                    .zip(static_account_keys.iter())
                    .all(|(signature, pubkey)| signature.verify(pubkey.as_ref(), message));
                (is_simple_vote_tx, verified)
            }
        }
    };

    if is_simple_vote_tx {
        packet.meta_mut().flags |= PacketFlags::SIMPLE_VOTE_TX;
    }

    verified
}
```
