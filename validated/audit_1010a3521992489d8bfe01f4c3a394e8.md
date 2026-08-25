### Title
DeployWithMaxDataLen ProgramData account creation is vulnerable to a pre-fund front-running DoS - (File: `programs/bpf_loader/src/lib.rs`)

### Summary
The BPF Loader Upgradeable's `DeployWithMaxDataLen` instruction handler derives the `ProgramData` account address as a PDA from the new program's public key and then creates it using the strict `system_instruction::create_account` call, which requires the target account to hold exactly zero lamports. Any unprivileged user who observes (or predicts) the soon-to-be-deployed program's keypair can pre-fund the derived `ProgramData` address with a trivial lamport transfer before the legitimate deploy transaction lands, causing the deploy to permanently fail with `SystemError::AccountAlreadyInUse` for that program address.

### Finding Description
In `process_loader_upgradeable_instruction`, the `DeployWithMaxDataLen` arm derives the ProgramData PDA and invokes the regular `CreateAccount` system instruction via CPI: [1](#0-0) 

The regular `CreateAccount` path in the System Program strictly requires that the destination account currently holds zero lamports, otherwise it returns `SystemError::AccountAlreadyInUse`: [2](#0-1) 

Because the `ProgramData` address is a deterministic PDA (`find_program_address(&[new_program_id.as_ref()], program_id)`), and `new_program_id` is public as soon as the deployer's transaction is submitted (or even earlier, if the deployer's program keypair/address leaks via other means), any third party can send a trivial `Transfer`/`Allocate`-style pre-funding transaction to that address before the deploy transaction is processed. Once pre-funded with even 1 lamport, the legitimate `DeployWithMaxDataLen` call will hit the `to.get_lamports() > 0` check and unconditionally fail, permanently blocking that specific program address from ever being deployed to.

Agave itself already recognizes this exact attack class: the `CreateAccountAllowPrefund` system instruction (SIMD-0312) was introduced specifically to let callers tolerate a pre-funded destination account: [3](#0-2) [4](#0-3) 

However, `process_loader_upgradeable_instruction`'s `DeployWithMaxDataLen` handler still uses the strict `create_account` (not the prefund-tolerant variant), so it remains exposed to this griefing/DoS pattern — directly analogous to the Morpho Blue report, where a deterministic, front-runnable address combined with a strict "not-already-created" check allows an attacker to block/hijack the legitimate creation flow.

### Impact Explanation
Impact is limited to denial-of-service/griefing: an attacker can permanently block deployment of a specific program address by spending a negligible amount of lamports (as little as 1 lamport, refundable rent aside) to pre-fund the derived `ProgramData` PDA before the legitimate `DeployWithMaxDataLen` transaction executes. The victim's deploy transaction fails, wasting transaction fees and compute, and forcing them to generate an entirely new program keypair and repeat the (potentially expensive) buffer-write process. No funds are stolen and no consensus state is corrupted, so impact is Medium.

### Likelihood Explanation
Likelihood is Medium: the attacker needs visibility into the new program's public key before the `DeployWithMaxDataLen` transaction is confirmed (e.g., by observing the transaction after submission/broadcast, or if the deployer publishes/reuses a predictable keypair). Since Solana transactions are visible on the network prior to finalization, and the pre-funding transaction is extremely cheap, a motivated attacker monitoring pending deploys can reliably execute this griefing.

### Recommendation
Use the prefund-tolerant creation path for the `ProgramData` account in `DeployWithMaxDataLen` (and any other loader instruction that creates a PDA-derived account with a strict zero-lamport precondition), analogous to `create_account_allow_prefund` in `programs/system/src/system_processor.rs`, so that a pre-funded destination account does not cause deployment to fail. Alternatively, have the loader explicitly detect and drain/tolerate a pre-existing lamport balance on the derived address before invoking `CreateAccount`.

### Proof of Concept
1. Attacker observes a `DeployWithMaxDataLen` transaction (or otherwise learns) referencing `program_keypair.pubkey()` before it's finalized.
2. Attacker computes `programdata_key = Pubkey::find_program_address(&[program_keypair.pubkey().as_ref()], &bpf_loader_upgradeable::id())`.
3. Attacker submits a `SystemInstruction::Transfer` sending 1 lamport to `programdata_key`, landing before the victim's deploy transaction.
4. Victim's `DeployWithMaxDataLen` transaction now fails at: [5](#0-4) 
returning `SystemError::AccountAlreadyInUse`, permanently blocking deployment to that program address (as also demonstrated by the "Reopen should fail" test pattern for `AccountAlreadyInitialized`/`AccountAlreadyInUse` semantics in the loader test suite): [6](#0-5)

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

**File:** programs/bpf_loader/src/lib.rs (L3890-3956)
```rust
        // Case: Reopen should fail
        process_instruction(
            &loader_id,
            &bincode::serialize(&UpgradeableLoaderInstruction::DeployWithMaxDataLen {
                max_data_len: 0,
            })
            .unwrap(),
            vec![
                (recipient_address, recipient_account),
                (programdata_address, programdata_account),
                (program_address, program_account),
                (buffer_address, buffer_account),
                (
                    sysvar::rent::id(),
                    create_account_for_test(&Rent::default()),
                ),
                (sysvar::clock::id(), clock_account),
                (
                    system_program::id(),
                    AccountSharedData::new(0, 0, &system_program::id()),
                ),
                (authority_address, authority_account),
            ],
            vec![
                AccountMeta {
                    pubkey: recipient_address,
                    is_signer: true,
                    is_writable: true,
                },
                AccountMeta {
                    pubkey: programdata_address,
                    is_signer: false,
                    is_writable: true,
                },
                AccountMeta {
                    pubkey: program_address,
                    is_signer: false,
                    is_writable: true,
                },
                AccountMeta {
                    pubkey: buffer_address,
                    is_signer: false,
                    is_writable: false,
                },
                AccountMeta {
                    pubkey: sysvar::rent::id(),
                    is_signer: false,
                    is_writable: false,
                },
                AccountMeta {
                    pubkey: sysvar::clock::id(),
                    is_signer: false,
                    is_writable: false,
                },
                AccountMeta {
                    pubkey: system_program::id(),
                    is_signer: false,
                    is_writable: false,
                },
                AccountMeta {
                    pubkey: authority_address,
                    is_signer: false,
                    is_writable: false,
                },
            ],
            Err(InstructionError::AccountAlreadyInitialized),
        );
```

**File:** programs/system/src/system_processor.rs (L149-182)
```rust
#[allow(clippy::too_many_arguments)]
fn create_account(
    from_account_index: IndexOfAccount,
    to_account_index: IndexOfAccount,
    to_address: &Address,
    lamports: u64,
    space: u64,
    owner: &Pubkey,
    signers: &HashSet<Pubkey>,
    invoke_context: &InvokeContext,
    instruction_context: &InstructionContext,
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

**File:** feature-set/src/lib.rs (L2463-2466)
```rust
        (
            create_account_allow_prefund::id(),
            "SIMD-0312: Enable CreateAccountAllowPrefund system program instruction",
        ),
```
