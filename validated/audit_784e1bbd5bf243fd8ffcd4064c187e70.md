### Title
Permanent DoS of `bpf_loader_upgradeable` program deployment via front-run pre-funding of the deterministic ProgramData PDA - (File: `programs/bpf_loader/src/lib.rs`)

### Summary
`UpgradeableLoaderInstruction::DeployWithMaxDataLen` derives the `ProgramData` account address deterministically from the new program's pubkey via `Pubkey::find_program_address(&[new_program_id.as_ref()], program_id)` and then CPIs into the System Program's `create_account` to initialize it [1](#0-0) . `system_processor::create_account` unconditionally rejects the call if the target account already has any lamports [2](#0-1) . Because the derived address is fully known once the deploy transaction (or the program keypair) is public, an attacker can front-run the deployment with a trivial `SystemInstruction::Transfer` of 1 lamport to that PDA, permanently blocking `create_account` for that specific `new_program_id` — the PDA has no controlling private key, so the griefing lamports (and the "in use" state) can never be cleared.

### Finding Description
In `process_loader_upgradeable_instruction`, the handler for `DeployWithMaxDataLen`:
1. Reads the `new_program_id` from the Program account being deployed.
2. Derives `programdata_key` deterministically: `Pubkey::find_program_address(&[new_program_id.as_ref()], program_id)` [3](#0-2) .
3. Builds a `system_instruction::create_account` instruction for that address and invokes it via `invoke_context.native_invoke_signed(...)` [4](#0-3) .

The System Program's `create_account` implementation bails out with `SystemError::AccountAlreadyInUse` as soon as the target account has any lamports at all, regardless of amount or owner:
```
if to.get_lamports() > 0 {
    ...
    return Err(SystemError::AccountAlreadyInUse.into());
}
``` [2](#0-1) 

This is exactly analogous to the reported LamboFactory issue: an address is deterministically computable off-chain before the "real" creation transaction lands, and any unprivileged party can pre-occupy that address with a cheap, ordinary instruction, causing the legitimate creation instruction to revert permanently. Here, since a PDA (an address off the Ed25519 curve) has no corresponding keypair, there is no way to reclaim or reset it once lamports are deposited — the block is truly permanent for that specific `new_program_id`/`programdata_key` pair, unlike a normal account which could at least be emptied by its owner. The codebase does have a `create_account_allow_prefund` variant of `create_account`, invoked via `SystemInstruction::CreateAccountAllowPrefund`, which tolerates pre-existing lamports [5](#0-4) , but the BPF loader's deploy path uses the strict `system_instruction::create_account` builder instead, so it does not benefit from this mitigation.

### Impact Explanation
Any unprivileged actor observing a pending `DeployWithMaxDataLen` transaction (or simply pre-emptively targeting a known/soon-to-be-used `program_keypair`) can send a single ordinary `Transfer` of 1 lamport to the derived `programdata_key` before the deploy transaction lands. Because the PDA is off-curve and has no private key, this state is irreversible: the specific program address can never be deployed, forcing the deployer to discard the address and retry with a new program keypair — and the same front-running bot can repeat the attack against every subsequent deployment attempt it observes, at negligible cost (1 lamport per griefing attempt, no signature required for the target since transfers don't need the recipient to sign). At scale, this allows an attacker to indiscriminately deny program deployment across the cluster, which is a transaction-triggered denial-of-service impacting the availability of a core, unprivileged, permissionless capability (deploying BPF programs).

### Likelihood Explanation
The attack requires only knowledge of `new_program_id` before the `create_account` CPI executes, and Solana transactions are broadcast in the clear over gossip/QUIC prior to inclusion, making the program address (and hence the derived PDA) visible to any network observer running a simple bot. No special privileges, precompiles, or validator collusion are needed — a single ordinary System Program `Transfer` instruction submitted by any wallet is sufficient. This makes the likelihood high for any bot willing to monitor pending deploy transactions.

### Recommendation
Use a variant of `create_account` for the ProgramData account that tolerates a pre-funded (but otherwise empty/uninitialized, system-owned) target account — i.e., route the CPI through `SystemInstruction::CreateAccountAllowPrefund`/`create_account_allow_prefund` (already present in `programs/system/src/system_processor.rs`) instead of the strict `create_account`, so that lamports sitting at the PDA prior to deployment do not cause `AccountAlreadyInUse`. Any additional lamports beyond rent-exemption should simply remain as excess balance on the new ProgramData account rather than blocking creation.

### Proof of Concept
1. Attacker (or a scanning bot) observes a broadcast `DeployWithMaxDataLen` transaction (or otherwise learns the `program_keypair.pubkey()` a deployer intends to use).
2. Attacker computes `programdata_key = Pubkey::find_program_address(&[new_program_id.as_ref()], &bpf_loader_upgradeable::id())`, matching the derivation at [6](#0-5) .
3. Attacker submits (with higher priority fee) a plain `system_instruction::transfer(attacker, programdata_key, 1)` that lands before the victim's deploy transaction.
4. When the victim's `DeployWithMaxDataLen` transaction executes, the CPI-invoked `create_account` at [7](#0-6)  hits the lamports check in `system_processor::create_account` at [8](#0-7)  and fails with `SystemError::AccountAlreadyInUse`, and this failure is permanent for that `new_program_id` since the PDA has no keypair to reclaim/close it.

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

**File:** programs/system/src/system_processor.rs (L160-171)
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
```

**File:** programs/system/src/system_processor.rs (L188-214)
```rust
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
