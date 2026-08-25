### Title
Deterministic ProgramData PDA can be pre-funded by an attacker to permanently block program deployment via BPF Upgradeable Loader's `DeployWithMaxDataLen` - (File: `programs/bpf_loader/src/lib.rs`)

### Summary
The BPF Upgradeable Loader derives the `ProgramData` account address deterministically from the program's public key using `Pubkey::find_program_address`, and creates it via a plain `system_instruction::create_account` CPI. The System Program's `create_account` handler unconditionally rejects account creation if the destination already holds any lamports. Because the ProgramData address is a program-derived address (PDA) with no private key, once an attacker pre-funds it with even 1 lamport, the account can never be created through this path again — permanently blocking deployment of that specific program ID, exactly analogous to the Nibiru vesting-account preemption attack that blocks a deterministic future contract address.

### Finding Description
When deploying an upgradeable program for the first time, `process_loader_upgradeable_instruction`'s `DeployWithMaxDataLen` handler computes the ProgramData PDA and issues a native CPI `system_instruction::create_account`: [1](#0-0) 

This CPI is handled by the System Program's `create_account`, which bails out permanently if the destination account already has nonzero lamports: [2](#0-1) 

The ProgramData address is fully deterministic and publicly computable as soon as the program's public key (`new_program_id`) is known — which happens as soon as the deployer's transactions referencing that key (e.g., buffer writes, program account creation, or the final deploy transaction itself while in flight) are visible to the network (via QUIC/TPU ingest, or observed on-chain from a partially completed deploy sequence). An attacker can then submit an ordinary, unprivileged System Program `Transfer` instruction sending even 1 lamport to that PDA address — no signature from the PDA itself is required for a plain transfer. Because the ProgramData address has no corresponding private key, the legitimate deployer can never satisfy `create_account`'s "already in use" check for that address again, so `DeployWithMaxDataLen` will fail with `SystemError::AccountAlreadyInUse` forever, permanently orphaning that program ID (existing `Buffer` funds already drained to the payer are refunded, but the intended program ID becomes permanently non-deployable).

Notably, the codebase already has a purpose-built fix for exactly this griefing pattern — `create_account_allow_prefund` / `SystemInstruction::CreateAccountAllowPrefund`, which permits account creation even when the destination is already pre-funded with lamports: [3](#0-2) 

However, the BPF Upgradeable Loader's `DeployWithMaxDataLen` path still uses the vulnerable plain `create_account` instruction rather than the prefund-tolerant variant, leaving the deployment flow exposed.

### Impact Explanation
An attacker can permanently deny deployment of any specific, not-yet-deployed program ID by front-running with a trivial, cheap transfer to its deterministic ProgramData PDA. For ecosystem-critical or high-value program IDs (e.g., ones committed to publicly ahead of time, or observed mid-deployment via mempool/QUIC ingest), this results in permanent denial of service against that address: the program can never be finalized at that ID, and any SOL/state tied to the aborted deployment flow is effectively wasted. This mirrors Impact 1 and 2 of the original Nibiru finding (orphaned/undeployable target and wasted resources), though the "locked funds" impact (Impact 3) does not directly apply since the ProgramData account never actually holds meaningful funds beyond rent.

### Likelihood Explanation
The precondition is knowledge of the target program's public key before the `DeployWithMaxDataLen` transaction is finalized. In common deployment workflows the key is disclosed slightly ahead of time (buffer creation/writes reference it, or the transaction is visible during TPU/QUIC ingestion before confirmation), and the griefing transaction itself is a single cheap, unprivileged system transfer requiring no special permissions. This makes the attack practical and low-cost against announced or observed deployments, though it requires the attacker to win a front-running race.

### Recommendation
Change `DeployWithMaxDataLen` (and any other loader code paths that create PDAs whose owning private key cannot exist) to use `system_instruction::create_account_allow_prefund` instead of `system_instruction::create_account`, mirroring the mitigation mechanism already implemented in `programs/system/src/system_processor.rs`. This allows legitimate deployment to proceed even if the PDA has been pre-funded by a third party, eliminating the permanent-DoS griefing vector.

### Proof of Concept
1. Attacker observes (via QUIC/TPU ingest or partially-completed on-chain buffer setup) the public key `new_program_id` that a victim intends to deploy an upgradeable program to.
2. Attacker computes `programdata_key = Pubkey::find_program_address(&[new_program_id.as_ref()], &bpf_loader_upgradeable::id())` (same derivation used at [4](#0-3) ).
3. Attacker submits an ordinary `system_instruction::transfer` sending 1 lamport to `programdata_key`, landing before the victim's `DeployWithMaxDataLen` transaction.
4. Victim's `DeployWithMaxDataLen` invokes `system_instruction::create_account` for `programdata_key`; because `to.get_lamports() > 0`, the System Program returns `SystemError::AccountAlreadyInUse` ( [5](#0-4) ), aborting the deployment.
5. Since `programdata_key` is a PDA with no signing key, no future transaction can ever satisfy the "not already in use" requirement for that address — the program ID is permanently blocked from `DeployWithMaxDataLen`.

### Citations

**File:** programs/bpf_loader/src/lib.rs (L279-310)
```rust
            // Create ProgramData account
            let (derived_address, bump_seed) =
                Pubkey::find_program_address(&[new_program_id.as_ref()], program_id);
            if derived_address != programdata_key {
                ic_logger_msg!(log_collector, "ProgramData address is not derived");
                return Err(InstructionError::InvalidArgument);
            }

            // Drain the Buffer account to payer before paying for programdata account
            {
                let mut buffer = instruction_context.try_borrow_instruction_account(3)?;
                let mut payer = instruction_context.try_borrow_instruction_account(0)?;
                payer.checked_add_lamports(buffer.get_lamports())?;
                buffer.set_lamports(0)?;
            }

            let owner_id = *program_id;
            let mut instruction = system_instruction::create_account(
                &payer_key,
                &programdata_key,
                1.max(rent.minimum_balance(programdata_len)),
                programdata_len as u64,
                program_id,
            );

            // pass an extra account to avoid the overly strict UnbalancedInstruction error
            instruction
                .accounts
                .push(AccountMeta::new(buffer_key, false));

            invoke_context
                .native_invoke_signed(instruction, &[&[new_program_id.as_ref(), &[bump_seed]]])?;
```

**File:** programs/system/src/system_processor.rs (L160-182)
```rust
) -> Result<(), InstructionError> {
    // if it looks like the `to` account is already in use, bail
    {
        let mut to = instruction_context.try_borrow_instruction_account(to_account_index)?;
        if to.get_lamports() > 0 {
            ic_msg!(
                invoke_context,
                "Create Account: account {:?} already in use",
                to_address
            );
            return Err(SystemError::AccountAlreadyInUse.into());
        }

        allocate_and_assign(&mut to, to_address, space, owner, signers, invoke_context)?;
    }
    transfer(
        from_account_index,
        to_account_index,
        lamports,
        invoke_context,
        instruction_context,
    )
}
```

**File:** programs/system/src/system_processor.rs (L184-214)
```rust
/// Create a new account without checking for 0 lamports. All other checks remain.
/// Intended for use where account has already had rent paid in whole or in part
/// before creation.
#[allow(clippy::too_many_arguments)]
fn create_account_allow_prefund(
    to_account_index: IndexOfAccount,
    to_address: &Address,
    from_and_lamports: Option<(IndexOfAccount, u64)>,
    space: u64,
    owner: &Pubkey,
    signers: &HashSet<Pubkey>,
    invoke_context: &InvokeContext,
    instruction_context: &InstructionContext,
) -> Result<(), InstructionError> {
    {
        let mut to = instruction_context.try_borrow_instruction_account(to_account_index)?;
        allocate_and_assign(&mut to, to_address, space, owner, signers, invoke_context)?;
    }
    if let Some((from_account_index, lamports)) = from_and_lamports
        && lamports > 0
    {
        transfer(
            from_account_index,
            to_account_index,
            lamports,
            invoke_context,
            instruction_context,
        )?;
    }
    Ok(())
}
```
