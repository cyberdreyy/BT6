[1](#0-0) [2](#0-1)

### Citations

**File:** program-runtime/src/cpi.rs (L1-26)
```rust
//! Cross-Program Invocation (CPI) error types

use {
    crate::{
        invoke_context::InvokeContext,
        memory::{translate_slice, translate_type, translate_type_mut_for_cpi, translate_vm_slice},
        memory_context::SerializedAccountMetadata,
        serialization::{create_memory_region_of_account, modify_memory_region_of_account},
    },
    solana_account_info::AccountInfo,
    solana_instruction::{AccountMeta, Instruction, error::InstructionError},
    solana_loader_v3_interface::instruction as bpf_loader_upgradeable,
    solana_program_entrypoint::MAX_PERMITTED_DATA_INCREASE,
    solana_pubkey::{MAX_SEEDS, Pubkey, PubkeyError},
    solana_sbpf::{ebpf, memory_region::MemoryMapping},
    solana_sdk_ids::{bpf_loader, bpf_loader_deprecated, native_loader},
    solana_stable_layout::stable_instruction::StableInstruction,
    solana_svm_log_collector::ic_msg,
    solana_svm_timings::ExecuteTimings,
    solana_transaction_context::{
        IndexOfAccount, MAX_ACCOUNTS_PER_INSTRUCTION, MAX_INSTRUCTION_DATA_LEN,
        instruction_accounts::BorrowedInstructionAccount, vm_slice::VmSlice,
    },
    std::mem,
    thiserror::Error,
};
```

**File:** program-runtime/src/cpi.rs (L108-123)
```rust
/// Check that an account info pointer field points to the expected address
fn check_account_info_pointer(
    invoke_context: &InvokeContext,
    vm_addr: u64,
    expected_vm_addr: u64,
    field: &str,
) -> Result<(), Error> {
    if vm_addr != expected_vm_addr {
        ic_msg!(
            invoke_context,
            "Invalid account info pointer `{}': {:#x} != {:#x}",
            field,
            vm_addr,
            expected_vm_addr
        );
        return Err(Box::new(CpiError::InvalidPointer));
```
