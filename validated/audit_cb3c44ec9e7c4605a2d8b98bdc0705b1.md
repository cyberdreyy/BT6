No vulnerability found for this question.

**Analysis:**

The `is_executable()` check in `translate_accounts_common` at [1](#0-0)  is evaluated against the real, authoritative `callee_account` obtained via `instruction_context.try_borrow_instruction_account(index_in_caller)` — i.e., the actual `TransactionContext` state — not against any attacker-supplied `AccountInfo` bytes in VM memory. An attacker cannot forge this branch decision because it's not derived from the forged `account_infos` entry at all.

Critically, taking the "known account" branch means the function **skips** calling `do_translate`/`update_callee_account` entirely for that account. It never reads or applies the attacker's forged `lamports`/`owner`/`data` fields from the caller's `AccountInfo` struct to the real `callee_account`. Compare this to the non-executable branch at [2](#0-1) , where `update_callee_account` actually copies caller-supplied `lamports`/`owner`/`data` into the callee account — that's the normal, intended CPI account-passing mechanism (separately privilege-gated by signer/writable checks elsewhere in the CPI stack, not within this function).

So the executable branch is a compute-charging optimization only: `callee_account.get_data().len()` (real length) is used to charge compute, and no aliased/forged data is ever propagated into the runtime's authoritative account state. When the callee subsequently executes, its own account view is built by `serialize_parameters` from the real `TransactionContext`/`BorrowedInstructionAccount` state — not from the caller's forged `account_infos` structure. There is no mechanism by which a forged lamports value in the caller's VM memory for a program account influences either the real account state or the callee's serialized view of that account.

Therefore, the premise that this branch "skips validation" in a way that lets forged VM-memory content become authoritative is incorrect — the branch is strictly more conservative, since it applies no caller-controlled data whatsoever to the real account. There is no code path here creating consensus divergence, unmetered execution, or memory/state escape.

### Citations

**File:** program-runtime/src/cpi.rs (L1006-1019)
```rust
        let index_in_caller = instruction_context
            .get_index_of_account_in_instruction(instruction_account.index_in_transaction)?;
        let callee_account = instruction_context.try_borrow_instruction_account(index_in_caller)?;
        let account_key = invoke_context
            .transaction_context
            .get_key_of_account_at_index(instruction_account.index_in_transaction)?;

        #[expect(deprecated)]
        if callee_account.is_executable() {
            // Use the known account
            let amount = (callee_account.get_data().len() as u64)
                .checked_div(invoke_context.get_execution_cost().cpi_bytes_per_unit)
                .unwrap_or(u64::MAX);
            invoke_context.compute_meter.consume_checked(amount)?;
```

**File:** program-runtime/src/cpi.rs (L1059-1076)
```rust
            let update_caller = if syscall_parameter_address_restrictions {
                // update_callee_account() is moved to cpi_common()
                true
            } else {
                // before initiating CPI, the caller may have modified the
                // account (caller_account). We need to update the corresponding
                // BorrowedAccount (callee_account) so the callee can see the
                // changes.
                update_callee_account(
                    memory_mapping,
                    check_aligned,
                    &caller_account,
                    callee_account,
                    syscall_parameter_address_restrictions,
                    virtual_address_space_adjustments,
                    account_data_direct_mapping,
                )?
            };
```
