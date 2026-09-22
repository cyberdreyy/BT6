### Title
Integer truncation in `InstructionDataLenBuilder::process_instruction` undercounts total instruction data size for transaction cost accounting - ([File: runtime-transaction/src/instruction_data_len.rs])

### Summary
`InstructionDataLenBuilder` accumulates the total instruction-data byte length of a transaction into a `u16` field by casting each instruction's `data.len()` (a `usize`) down to `u16` *before* accumulating it with `saturating_add`. This is the same bug class as the reported CVE (integer truncation on a length/size value leading to an incorrect, smaller-than-actual value being used downstream), here applied to compute-cost accounting instead of memory access.

### Finding Description [1](#0-0) 

```rust
pub struct InstructionDataLenBuilder {
    value: u16,
}

impl InstructionDataLenBuilder {
    pub fn process_instruction(&mut self, _program_id: &Pubkey, instruction: &SVMInstruction) {
        self.value = self.value.saturating_add(instruction.data.len() as u16);
    }

    pub fn build(self) -> u16 {
        self.value
    }
}
```

The cast `instruction.data.len() as u16` truncates the length **per-instruction** before the saturating add occurs. If a single instruction's data length exceeds `u16::MAX` (65535), the truncation wraps the value modulo 65536 instead of clamping it. For example, an instruction with `data.len() == 65540` truncates to `4` rather than saturating to `65535`. Because truncation happens before accumulation, this is a true modulo-wraparound bug, not merely a saturation/clamping choice.

This value flows into `TransactionMeta::instruction_data_len` via `InstructionMeta::try_new` [2](#0-1) , which is used by the cost model to compute `data_bytes_cost` for leader-side block cost accounting, e.g. `vote_transaction.instruction_data_len() / (INSTRUCTION_DATA_BYTES_COST as u16)` [3](#0-2) , and `TransactionCost::data_bytes_cost: u16` [4](#0-3) .

### Impact Explanation
Since `instruction_data_len` and `data_bytes_cost` are `u16`, the maximum representable value is 65535 regardless of truncation behavior, and Solana's `MAX_INSTRUCTION_DATA_LEN`/CPI limits already bound individual instruction data sizes far below `u16::MAX` in most paths, which limits practical exploitability of the wraparound in the current transaction-size envelope. I could not fully verify (within the available search results) what the maximum top-level (non-CPI) instruction data size actually permitted by transaction size limits (~1232 bytes packet limit for legacy paths, but larger for `TransactionData`/quic-buffered transactions or program-runtime `MAX_INSTRUCTION_DATA_LEN`) is on this branch, nor whether any legitimate transaction path can construct a single instruction with `data.len() > 65535`. This is an important gap: if such a large single instruction is reachable (e.g., via large program deployment instructions or a newly-introduced expanded transaction size format), the truncation would allow an attacker to submit a transaction whose real instruction-data footprint is undercounted for leader cost-tracking (`data_bytes_cost`), corrupting the block cost accounting field used for leader-side cost limits — a low-severity, denial-of-consensus-adjacent effect (a validator/leader misjudging its cost budget) rather than direct fund movement or consensus divergence between validators (since this same low-severity, deterministic computation runs identically on all validators, it does not by itself cause state-hash divergence).

### Likelihood Explanation
Low-to-uncertain. The `u16` clamp elsewhere in the type system already caps this value at 65535, and I was unable to confirm within the current search results that an unprivileged sender can construct a single instruction whose `data.len()` genuinely exceeds `u16::MAX` under this repository's transaction-size and CPI/instruction-data-length limits. Without confirming a reachable single-instruction data length > 65535 bytes, this cannot be validated as a concretely exploitable, transaction-triggerable issue.

### Recommendation
Saturate before casting, e.g. `self.value = self.value.saturating_add(instruction.data.len().min(u16::MAX as usize) as u16);` so an oversized instruction saturates the accumulator to `u16::MAX` rather than wrapping. Additionally, confirm (and if necessary enforce) that no code path allows a single instruction's `data` field to exceed `u16::MAX` bytes before this accumulation runs, to eliminate the wraparound class entirely.

### Proof of Concept
Not independently verified against a live constructible transaction due to inability to confirm within available context whether any consensus-reachable transaction-construction path permits a single instruction's `data.len()` to exceed 65535 bytes; if confirmed, a transaction containing one instruction with e.g. 65540 bytes of instruction data would cause `InstructionDataLenBuilder::build()` to return `4` instead of saturating to `65535`, corrupting downstream `data_bytes_cost` computation in `cost-model/src/transaction_cost.rs`.

### Citations

**File:** runtime-transaction/src/instruction_data_len.rs (L1-16)
```rust
use {solana_pubkey::Pubkey, solana_svm_transaction::instruction::SVMInstruction};

#[derive(Default)]
pub struct InstructionDataLenBuilder {
    value: u16,
}

impl InstructionDataLenBuilder {
    pub fn process_instruction(&mut self, _program_id: &Pubkey, instruction: &SVMInstruction) {
        self.value = self.value.saturating_add(instruction.data.len() as u16);
    }

    pub fn build(self) -> u16 {
        self.value
    }
}
```

**File:** runtime-transaction/src/instruction_meta.rs (L16-31)
```rust
impl InstructionMeta {
    pub fn try_new<'a>(
        instructions: impl Iterator<Item = (&'a Pubkey, SVMInstruction<'a>)>,
    ) -> Result<Self, TransactionError> {
        let mut precompile_signature_details_builder = PrecompileSignatureDetailsBuilder::default();
        let mut instruction_data_len_builder = InstructionDataLenBuilder::default();
        for (program_id, instruction) in instructions {
            precompile_signature_details_builder.process_instruction(program_id, &instruction);
            instruction_data_len_builder.process_instruction(program_id, &instruction);
        }

        Ok(Self {
            precompile_signature_details: precompile_signature_details_builder.build(),
            instruction_data_len: instruction_data_len_builder.build(),
        })
    }
```

**File:** cost-model/src/transaction_cost.rs (L8-16)
```rust
pub struct TransactionCost<'a, Tx> {
    pub transaction: &'a Tx,
    pub signature_cost: u64,
    pub write_lock_cost: u64,
    pub data_bytes_cost: u16,
    pub programs_execution_cost: u64,
    pub loaded_accounts_data_size_cost: u64,
    pub allocated_accounts_data_size: u64,
}
```

**File:** cost-model/src/transaction_cost.rs (L310-312)
```rust
            let write_lock_cost = 2 * block_cost_limits::WRITE_LOCK_UNITS;
            let data_bytes_cost =
                vote_transaction.instruction_data_len() / (INSTRUCTION_DATA_BYTES_COST as u16);
```
