## Analysis

The ESD bug pattern is: a privileged actor registers/updates accounting state assuming a certain precondition; an unprivileged actor can permissionlessly touch the same shared resource first, causing the privileged operation to fail unexpectedly when it lands. The closest reachable analog in Agave is the **permissionless `ExtendProgram` instruction** of the upgradeable BPF loader.

### Title
Permissionless `ExtendProgram` instruction can be frontrun to grief program upgrade/deploy flows - (File: programs/bpf_loader/src/lib.rs)

### Summary
`UpgradeableLoaderInstruction::ExtendProgram` is dispatched to `common_extend_program` with `check_authority = false`, meaning **any unprivileged account** can extend a `ProgramData` account's size without being the upgrade authority. The instruction also enforces a "once per slot" guard keyed on the `ProgramData` account's stored `slot` field. Because the instruction is permissionless, an attacker can call `ExtendProgram` on a target program's `ProgramData` account first in a slot, which trips the guard and causes the legitimate `ExtendProgram` call issued by the upgrade authority (as part of a deploy/upgrade sequence) to fail when it lands in the same slot.

### Finding Description
`common_extend_program` is invoked for the unchecked `ExtendProgram` variant with `check_authority=false`: [1](#0-0) 

Inside `common_extend_program`, authority verification is entirely gated behind the `check_authority` flag — when `false`, no signer/authority check is performed at all, so any account can trigger the extension and pay for it: [2](#0-1) 

The function also enforces a single-extension-per-slot guard based on the on-chain `slot` field stored in `ProgramData`, comparing it to the current clock slot: [3](#0-2) 

This exactly mirrors the ReserveSwapper bug class: a privileged/expected update (the upgrade authority extending program data as a precondition to an upgrade, computed off-chain via `extend_program_data_if_needed` in the CLI, which reads a stale `ProgramData` account size via RPC) can be pre-empted by an unprivileged actor calling the same permissionless instruction on the same account, changing the account's `slot`-guarded accounting state before the legitimate transaction lands: [4](#0-3) 

Because `ExtendProgram` has no authority check, any address can call it against a targeted program's `ProgramData` account each slot, keeping the "extended in this block already" flag effectively adversarially controlled and causing subsequent legitimate extend/upgrade transactions targeting that slot to fail with `InstructionError::InvalidArgument`.

### Impact Explanation
This does not directly steal funds, but it does allow an unprivileged actor to force **unauthorized state mutation** (growing a target program's `ProgramData` account size and slot field) and to **repeatedly and predictably grief/DoS legitimate program upgrade or initial-deploy workflows** for any program, by making the "extend" step of the atomic deploy sequence fail whenever an attacker races it in the same slot. Since deploy/upgrade tooling (`extend_program_data_if_needed`) computes the needed `additional_bytes` from a stale RPC read and assumes its own `ExtendProgram` call will land cleanly, an attacker can reliably disrupt program upgrades network-wide by monitoring the mempool and frontrunning `ExtendProgram` calls, and can also force programs to pay unwanted rent for size growth they didn't request.

### Likelihood Explanation
High. `ExtendProgram` requires no signature from the program's upgrade authority (`check_authority=false`), so any account with minimal SOL to pay the incremental rent (or paying with lamports transferred by itself as `payer`) can call it on any target `ProgramData` account at will, at any time, including from a bot monitoring the mempool for upgrade-related transactions from a target authority.

### Recommendation
Require the `ProgramData` account's `slot`/extension guard to also validate against the actual authority or intended caller, or make the "extended-in-this-block" bookkeeping resistant to third-party interference — e.g., track per-authorized-caller state rather than a single shared per-slot flag, or require the extend instruction to be authority-gated (or bundled atomically) so that an unrelated unprivileged party cannot consume the one-extension-per-slot allowance for a program it does not control.

### Proof of Concept
1. Attacker watches the mempool/RPC for a legitimate deploy/upgrade transaction sequence that includes `system_instruction::extend_program` (built via `extend_program_data_if_needed`) targeting `program_data_address` for a victim program. [5](#0-4) 
2. Attacker submits its own `ExtendProgram { additional_bytes: 1 }` instruction against the same `programdata_key`, naming itself as payer, with higher priority fee so it lands first in the slot.
3. `common_extend_program` executes, setting `ProgramData.slot = clock_slot`. [6](#0-5) 
4. When the victim's own `ExtendProgram` instruction lands later in the same slot, the `clock_slot == slot` check fails the instruction with `InstructionError::InvalidArgument` ("Program was extended in this block already"), aborting the victim's deploy/upgrade transaction. [3](#0-2)

### Citations

**File:** programs/bpf_loader/src/lib.rs (L790-792)
```rust
        UpgradeableLoaderInstruction::ExtendProgram { additional_bytes } => {
            common_extend_program(invoke_context, additional_bytes, false)?;
        }
```

**File:** programs/bpf_loader/src/lib.rs (L898-912)
```rust
    let clock_slot = invoke_context
        .environment_config
        .sysvar_cache()
        .get_clock()
        .map(|clock| clock.slot)?;

    let upgrade_authority_address = if let UpgradeableLoaderState::ProgramData {
        slot,
        upgrade_authority_address,
    } = programdata_account.get_state()?
    {
        if clock_slot == slot {
            ic_logger_msg!(log_collector, "Program was extended in this block already");
            return Err(InstructionError::InvalidArgument);
        }
```

**File:** programs/bpf_loader/src/lib.rs (L920-933)
```rust
        }

        if check_authority {
            let authority_key =
                Some(*instruction_context.get_key_of_instruction_account(AUTHORITY_ACCOUNT_INDEX)?);
            if upgrade_authority_address != authority_key {
                ic_logger_msg!(log_collector, "Incorrect upgrade authority provided");
                return Err(InstructionError::IncorrectAuthority);
            }
            if !instruction_context.is_instruction_account_signer(AUTHORITY_ACCOUNT_INDEX)? {
                ic_logger_msg!(log_collector, "Upgrade authority did not sign");
                return Err(InstructionError::MissingRequiredSignature);
            }
        }
```

**File:** programs/bpf_loader/src/lib.rs (L987-992)
```rust
    let mut programdata_account =
        instruction_context.try_borrow_instruction_account(PROGRAM_DATA_ACCOUNT_INDEX)?;
    programdata_account.set_state(&UpgradeableLoaderState::ProgramData {
        slot: clock_slot,
        upgrade_authority_address,
    })?;
```

**File:** cli/src/program.rs (L2996-3020)
```rust
    let current_len = program_data_account.data.len();
    let additional_bytes = required_len.saturating_sub(current_len);
    if additional_bytes == 0 {
        // Current allocation is sufficient.
        return Ok(());
    }

    let mut additional_bytes =
        u32::try_from(additional_bytes).expect("`u32` is big enough to hold an account size");

    let feature_set = fetch_feature_set(rpc_client).await?;
    let feature_snapshot = feature_set.snapshot();

    if feature_snapshot.loader_v3_minimum_extend_program_size {
        // SIMD-0431: Have to bump `additional_bytes` to satisfy either the
        // minimum size requirement or the remaining headroom to
        // MAX_PERMITTED_DATA_SIZE.
        let headroom =
            u32::try_from(max_permitted_data_length.saturating_sub(current_len)).unwrap();
        additional_bytes = additional_bytes.max(MINIMUM_EXTEND_PROGRAM_BYTES.min(headroom));
    }

    let instruction =
        loader_v3_instruction::extend_program(program_id, Some(fee_payer), additional_bytes);
    initial_instructions.push(instruction);
```
