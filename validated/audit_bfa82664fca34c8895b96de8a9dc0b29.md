### Title
Unbounded, unmetered signature-verification loop in secp256k1/ed25519 precompiles enables leader CPU exhaustion - (File: `precompiles/src/secp256k1.rs`, `precompiles/src/ed25519.rs`)

### Summary
The `_sendFees()` unbounded-shareholders report describes a loop over an attacker-controlled-length collection whose cost is not sanity-capped, allowing gas exhaustion. The equivalent pattern exists in Agave's precompile verification: `secp256k1::verify()` and `ed25519::verify()` iterate `count` times, where `count = data[0]` (0-255, attacker-controlled), performing a full elliptic-curve signature recovery/verification per iteration, and this work is not charged against the transaction's SBF compute-unit meter.

### Finding Description
`precompiles/src/secp256k1.rs::verify()` reads a single byte `count = data[0] as usize` and loops `for i in 0..count`, and inside the loop performs `libsecp256k1::recover(...)` (an expensive EC point-recovery operation) plus a keccak hash, for every iteration: [1](#0-0) [2](#0-1) 

`ed25519.rs` has the analogous unbounded loop keyed off a similarly attacker-supplied count.

This verification is invoked via `Precompile::verify` / `verify_if_precompile` from the transaction-processing path (`InvokeContextCallback::process_precompile` in `program-runtime/src/invoke_context.rs`, wired into `runtime/src/bank.rs`) as part of executing the precompile "instruction," not inside the sanitize-only path: [3](#0-2) 

Critically, precompile instructions are treated as builtins that are billed a **fixed** default compute-unit allocation (`MAX_BUILTIN_ALLOCATION_COMPUTE_UNIT_LIMIT`) regardless of how much actual EC-recovery work is performed inside `verify()`. The test suite confirms a precompiled instruction consumes 0 CU from the VM meter and the entire allocation is refunded: [4](#0-3) 

Separately, the cost model only uses the leading `count` byte to *estimate* a fixed per-signature cost for block-cost-limit bookkeeping (`SECP256K1_VERIFY_COST`, `ED25519_VERIFY_STRICT_COST`), it does not enforce those costs as actual SBF compute charges consumed during verify() execution: [5](#0-4) [6](#0-5) 

Because a transaction can include multiple precompile instructions (bounded only by `MAX_INSTRUCTION_TRACE_LENGTH` and `PACKET_DATA_SIZE`), and each instruction's `count` byte can independently request up to 255 signature checks referencing data in other instructions of the same transaction, the real CPU cost of verifying one transaction can scale far beyond what is reflected in either the compute-budget meter (0 CU billed) or realistically anticipated by the fixed builtin allocation, mirroring the "unbounded shareholders" gas-exhaustion pattern: an attacker-controlled loop bound drives real, expensive work that is not gated by the mechanism (compute metering) meant to bound per-transaction cost.

### Impact Explanation
An attacker can construct transactions that pack the maximum practical number of secp256k1/ed25519 signature-offset entries across the instructions permitted in a single transaction. Each entry triggers a full EC signature recovery. Since this work happens during normal transaction verification/processing (reachable by any ordinary user submitting a transaction) and is not billed proportionally via the SBF compute-unit meter, an attacker can force disproportionate CPU consumption on validators (during replay and banking-stage processing) relative to the fee/priority paid and relative to the compute budget nominally reserved for the instruction. Repeated submission of such transactions can degrade leader/replay throughput, a denial-of-service class analogous to the `_sendFees()` shareholder-count DoS.

### Likelihood Explanation
The attack requires only constructing a standard transaction with precompile instructions with a maximal signature `count` byte and appropriately laid out offset data — no special privileges are needed, and precompile programs are always enabled (`feature: None` in `precompiles/src/lib.rs`). The bound on total achievable work per transaction is limited by packet size (transaction size limit) and the instruction-trace-length check, which reduces but does not eliminate the disparity between real CPU cost and metered/charged compute units.

### Recommendation
- Meter the actual work performed inside `secp256k1::verify()` / `ed25519::verify()` / `secp256r1::verify()` against the transaction's compute budget (e.g., consume CUs per EC-recovery call actually executed) rather than relying solely on a fixed builtin allocation.
- Alternatively, ensure the cost-model's per-signature cost estimates used for block-cost accounting are also enforced as hard, chargeable compute limits at execution time, so that transactions with many signature-check entries actually pay (and are throttled) proportionally to the real cryptographic work performed.
- Consider tightening the practical maximum `count` (currently up to 255 per instruction, further multiplied by number of precompile instructions per transaction) to a sane cap consistent with realistic per-transaction compute budgets.

### Proof of Concept
1. Construct a `VersionedTransaction` containing multiple `secp256k1_program`/`ed25519_program` instructions, each with `data[0]` (count) set to the maximum value the remaining packet space allows (each `SecpSignatureOffsets` entry is a small fixed-size struct), and reference-offsets pointing at other instructions' data within the same transaction to satisfy `SIGNATURE_OFFSETS_SERIALIZED_SIZE` bounds checks in `precompiles/src/secp256k1.rs` lines 44-101.
2. Fill the remaining instructions/account keys up to `PACKET_DATA_SIZE`/`MAX_INSTRUCTION_TRACE_LENGTH` limits with additional signature-check entries.
3. Submit this transaction repeatedly; observe (via `core/tests/scheduler_cost_adjustment.rs::test_builtin_ix_precompiled` behavior) that the VM/compute-budget path reports ~0 CU consumed for the precompile instructions while the underlying `libsecp256k1::recover`/ed25519 verification loop performs the full O(count) EC-cryptography work, disproportionate to the compute units charged.

Note: I was unable to fully trace, within the available iterations, whether an additional signature/CU check exists further upstream (e.g., in `runtime-transaction/src/signature_details.rs`'s `PrecompileSignatureDetails`) that might cap or reject such transactions before reaching `verify()`; the cost-tracker use of these counts appears to be for block-cost bookkeeping (`cost-model/src/cost_tracker.rs`) rather than a hard rejection/metering mechanism, but this should be confirmed against the live `checked_add`/error paths in the cost tracker and block-limit enforcement before treating this as fully confirmed exploitable at scale.

### Citations

**File:** precompiles/src/secp256k1.rs (L31-48)
```rust
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
```

**File:** precompiles/src/secp256k1.rs (L88-102)
```rust

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
```

**File:** precompiles/src/lib.rs (L98-119)
```rust
/// Check that a program is precompiled and if so verify it
pub fn verify_if_precompile(
    program_id: &Pubkey,
    precompile_instruction: &CompiledInstruction,
    all_instructions: &[CompiledInstruction],
    feature_set: &FeatureSet,
) -> Result<(), PrecompileError> {
    for precompile in PRECOMPILES.iter() {
        if precompile.check_id(program_id, |feature_id| feature_set.is_active(feature_id)) {
            let instruction_datas: Vec<_> = all_instructions
                .iter()
                .map(|instruction| instruction.data.as_ref())
                .collect();
            return precompile.verify(
                &precompile_instruction.data,
                &instruction_datas,
                feature_set,
            );
        }
    }
    Ok(())
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

**File:** cost-model/src/block_cost_limits.rs (L9-14)
```rust
/// Number of compute units for one signature verification.
pub const SIGNATURE_COST: u64 = COMPUTE_UNIT_TO_US_RATIO * 24;
/// Number of compute units for one secp256k1 signature verification.
pub const SECP256K1_VERIFY_COST: u64 = COMPUTE_UNIT_TO_US_RATIO * 223;
/// Number of compute units for one ed25519 strict signature verification.
pub const ED25519_VERIFY_STRICT_COST: u64 = COMPUTE_UNIT_TO_US_RATIO * 80;
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
