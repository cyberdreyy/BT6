### Title
Inner-instruction index truncation causes duplicate/misattributed instruction indices in transaction status - (File: transaction-status/src/lib.rs)

### Summary
`InnerInstructions::index`, which identifies which top-level instruction an inner (CPI) instruction belongs to, is declared as `u8` [1](#0-0) , but the number of top-level instructions in a transaction message is not bounded to `u8::MAX` (255) anywhere before this cast is applied. `map_inner_instructions` builds this field by simply truncating the loop counter with `index as u8` [2](#0-1) . This is the exact bug class from the report: a wider-range value (the position of a top-level instruction in a message, which can exceed 255) is downcast to a narrower integer type, causing distinct indices to collide/wrap and be reported as duplicates.

### Finding Description
A `VersionedTransaction`/legacy `Message` places no explicit cap on the number of top-level instructions besides the 1232-byte (`PACKET_DATA_SIZE`) transaction size limit. A minimal `CompiledInstruction` (no accounts, no data) serializes to only a few bytes, so it is possible to pack well over 255 top-level instructions into a single transaction while still fitting in one packet, reusing a single fee-payer/program pair to keep the `account_keys` short.

After execution, `TransactionBatchProcessor::deconstruct_transaction` builds a `Vec<Vec<InnerInstruction>>` indexed by the top-level instruction position [3](#0-2) . This list is then converted via `map_inner_instructions`, which enumerates the outer vector and casts the position to `u8` when constructing `InnerInstructions { index: index as u8, .. }` [2](#0-1) . If a top-level instruction beyond position 255 performs a CPI, its true index (e.g., 256) truncates to `0` (256 mod 256), and the entry is emitted with `index = 0`, colliding with (or replacing/misattributing relative to) the legitimate instruction 0's inner-instruction group.

The same truncation exists independently on the storage/protobuf read path: the wire format uses `uint32 index` [4](#0-3) , but decoding back into the internal representation narrows it to `u8` via `index: value.index as u8` [5](#0-4) , confirming the internal `u8` width mismatch is treated as insufficient even by the project's own wire schema.

This is reachable purely by an unprivileged user submitting a single crafted transaction and later querying it via `getTransaction`/`getConfirmedBlock`-style RPCs, which use `UiInnerInstructions { index: u8, .. }` [6](#0-5)  built directly from the truncated `InnerInstructions`.

### Impact Explanation
The bug causes wrong data to be returned from an RPC query: inner instructions belonging to top-level instruction N (N ≥ 256) are reported under index `N mod 256`, misattributing CPI call data to the wrong top-level instruction in the JSON response for `getTransaction`/`getBlock`. This is a decoder misreporting issue impacting any consumer (explorers, indexers, wallets) that trusts the `index` field to map inner instructions to their parent instruction, without any consensus-state corruption.

### Likelihood Explanation
Likelihood is low: it requires crafting a transaction with more than 255 top-level instructions that fits within the 1232-byte packet limit and includes at least one CPI-performing instruction beyond position 255. This is achievable by an unprivileged user with a single transaction submission (no special privileges needed), though it requires careful transaction construction to satisfy compute-budget and account constraints for that many instructions to execute far enough to trigger the fault. Overall likelihood mirrors the original finding's "Low" rating.

### Recommendation
Widen `InnerInstructions::index` (and the corresponding `UiInnerInstructions::index`) from `u8` to a type that can represent the full possible range of top-level instruction positions (e.g., `u16` or `usize`), matching the `uint32` already used in the protobuf schema, and update all conversions in `transaction-status/src/lib.rs`, `transaction-status-client-types/src/lib.rs`, and `storage-proto/src/convert.rs` accordingly instead of truncating with `as u8`.

### Proof of Concept
1. Construct a `v0` transaction with a single fee-payer/signer and a single non-signer program account, and pack ≥257 top-level `CompiledInstruction`s (each with empty `accounts`/`data`) referencing that program, such that the total serialized size stays under `PACKET_DATA_SIZE` (1232 bytes).
2. Make the 257th instruction (index 256) invoke a program that performs at least one CPI (so it produces a non-empty `InnerInstructions` entry).
3. Submit and confirm the transaction, then query it via `getTransaction`.
4. Observe that `meta.innerInstructions` reports the CPI recorded for top-level instruction 256 under `index: 0`, colliding with (or in place of) any inner instructions from instruction 0, verified against `map_inner_instructions`'s `index as u8` cast [7](#0-6) .

### Citations

**File:** transaction-status-client-types/src/lib.rs (L618-645)
```rust
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct UiInnerInstructions {
    /// Transaction instruction index
    pub index: u8,
    /// List of inner instructions
    pub instructions: Vec<UiInstruction>,
}

impl From<InnerInstructions> for UiInnerInstructions {
    fn from(inner_instructions: InnerInstructions) -> Self {
        Self {
            index: inner_instructions.index,
            instructions: inner_instructions
                .instructions
                .iter()
                .map(
                    |InnerInstruction {
                         instruction: ix,
                         stack_height,
                     }| {
                        UiInstruction::Compiled(UiCompiledInstruction::from(ix, *stack_height))
                    },
                )
                .collect(),
        }
    }
}
```

**File:** transaction-status-client-types/src/lib.rs (L647-653)
```rust
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize, SchemaRead, SchemaWrite)]
pub struct InnerInstructions {
    /// Transaction instruction index
    pub index: u8,
    /// List of inner instructions
    pub instructions: Vec<InnerInstruction>,
}
```

**File:** transaction-status/src/lib.rs (L130-146)
```rust
pub fn map_inner_instructions(
    inner_instructions: solana_message::inner_instruction::InnerInstructionsList,
) -> impl Iterator<Item = InnerInstructions> {
    inner_instructions
        .into_iter()
        .enumerate()
        .map(|(index, instructions)| InnerInstructions {
            index: index as u8,
            instructions: instructions
                .into_iter()
                .map(|info| InnerInstruction {
                    stack_height: Some(u32::from(info.stack_height)),
                    instruction: info.instruction,
                })
                .collect(),
        })
        .filter(|i| !i.instructions.is_empty())
```

**File:** svm/src/transaction_processor.rs (L1234-1260)
```rust
    /// Extract an ExecutionRecord and an InnerInstructionsList from a TransactionContext
    fn deconstruct_transaction(
        mut transaction_context: TransactionContext,
        record_inner_instructions: bool,
    ) -> (ExecutionRecord, Option<InnerInstructionsList>) {
        let inner_ix = if record_inner_instructions {
            debug_assert!(
                transaction_context
                    .get_instruction_context_at_index_in_trace(0)
                    .map(|instruction_context| instruction_context.get_stack_height()
                        == TRANSACTION_LEVEL_STACK_HEIGHT)
                    .unwrap_or(true)
            );

            let top_level_ixs_num = transaction_context
                .get_instruction_trace_length()
                .saturating_sub(transaction_context.number_of_cpis_in_trace());
            // This vector is a map between CPI number in trace (not counting top level
            // instructions) and the top level caller index.
            // In TransactionContext, caller instructions always precede callee instructions, so
            // we can use it to avoid backtracking on instructions callers to
            // find the top level instruction that started the call chain.
            let mut parent_positions: Vec<usize> =
                vec![usize::MAX; transaction_context.number_of_cpis_in_trace()];
            let (ix_trace, accounts, ix_data_trace) = transaction_context.take_instruction_trace();
            let mut outer_instructions: Vec<Vec<InnerInstruction>> =
                vec![Vec::new(); top_level_ixs_num];
```

**File:** storage-proto/proto/confirmed_block.proto (L84-87)
```text
message InnerInstructions {
    uint32 index = 1;
    repeated InnerInstruction instructions = 2;
}
```

**File:** storage-proto/src/convert.rs (L662-669)
```rust
impl From<generated::InnerInstructions> for InnerInstructions {
    fn from(value: generated::InnerInstructions) -> Self {
        Self {
            index: value.index as u8,
            instructions: value.instructions.into_iter().map(|i| i.into()).collect(),
        }
    }
}
```
