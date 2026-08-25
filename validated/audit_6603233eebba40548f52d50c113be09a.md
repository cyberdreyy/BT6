### Title
Prefunding a not-yet-created PDA permanently locks funds when `SystemInstruction::CreateAccount` rejects a nonzero-balance target - ([File: programs/system/src/system_processor.rs])

### Summary
The analog of the reported EVM bug ("funds sent to a not-yet-deployed contract address become unrecoverable once the code check blocks restoration") exists in agave's System Program account-creation path. A Program Derived Address (PDA) is the Solana equivalent of a CREATE2 precomputed address: its address is known ahead of time, but it has no private key, so any lamports sent to it before the owning program initializes it via `SystemInstruction::CreateAccount` can become permanently stuck if that creation is blocked.

### Finding Description
`create_account()` in `programs/system/src/system_processor.rs` explicitly refuses to initialize a "to" account if it already holds lamports: [1](#0-0) 

This mirrors the reported root cause: a check (`to.get_lamports() > 0`, analogous to the EVM's `CodeHash != EmptyCodeHash` restoration check) unconditionally blocks the normal path once funds have arrived at the target address ahead of initialization. Because a PDA has no keypair, the funds sitting at that address cannot be moved out via a signed `SystemInstruction::Transfer`; the only way to reclaim them is for the *owning program* to CPI `invoke_signed` with the PDA's seeds and issue a transfer/allocate itself — something most programs do not implement, since the standard initialization pattern is simply `create_account`.

Agave's own developers recognized this exact bug class and added a dedicated instruction, `SystemInstruction::CreateAccountAllowPrefund`, gated by the `create_account_allow_prefund` feature, whose processor deliberately skips the "already in use" balance check and instead only checks that the account is unallocated (`allocate_and_assign`), allowing prefunded balances to be preserved and the account to still be initialized: [2](#0-1) [3](#0-2) 

This is functionally the fix that the original report recommends ("add support for restoring old balances to a contract"). However, the underlying risk remains wherever code paths still use the legacy `create_account` (not `create_account_allow_prefund`), including:
- Direct `SystemInstruction::CreateAccount` / `CreateAccountWithSeed` dispatch, still unconditionally rejecting prefunded targets: [4](#0-3) 
- Any third-party or builtin program that CPIs `create_account` for PDA initialization (e.g., token/associated-token-style flows), which inherits the same "AccountAlreadyInUse" failure mode whenever the PDA is prefunded before the CPI runs.

### Impact Explanation
If an attacker (or an innocent user) sends lamports to a not-yet-created PDA before the owning program's initialization instruction executes, that initialization permanently fails with `SystemError::AccountAlreadyInUse` for any code path still using plain `create_account`. Since the PDA has no private key, the lamports cannot be reclaimed by the depositor, and unless the owning program specifically implements a `CreateAccountAllowPrefund`-based (or manual `invoke_signed` drain/allocate) initialization path, those funds — and the intended account/PDA itself — are effectively bricked. This is a state/DoS/fund-loss risk reachable purely from an ordinary user's transaction (a simple `Transfer` to the PDA), consistent with the "unauthorized... state mutation" / "fund loss" bar in scope.

### Likelihood Explanation
Likelihood is high for any protocol/program that still relies on the legacy `create_account` path for PDA initialization instead of migrating to `create_account_allow_prefund`: an attacker only needs to know the deterministic PDA address (derivable by anyone via `find_program_address`) and send it a minimal amount of lamports before the legitimate initializer transaction lands — a classic "PDA griefing/front-running" pattern well known in the Solana ecosystem, which is precisely why `create_account_allow_prefund` was introduced as a mitigation.

### Recommendation
- Ensure `create_account_allow_prefund` is broadly activated and that all first-party/builtin programs (and guidance for third-party programs) migrate PDA-initialization CPIs from `SystemInstruction::CreateAccount`/`CreateAccountWithSeed` to `SystemInstruction::CreateAccountAllowPrefund` wherever the "to" account is a PDA whose address can be prefunded by third parties.
- Document clearly (as the original report also recommends) that PDAs prefunded prior to `CreateAccount` will fail permanently unless the calling program uses the prefund-allowing variant, and provide guidance/tooling for programs to detect and recover such stuck PDAs by CPI’ing with the correct seeds.

### Proof of Concept
1. Compute a PDA address `P` off-chain via `Pubkey::find_program_address(seeds, program_id)`, exactly as a target program would before creating it.
2. Before the program's initialization transaction executes, submit an ordinary `SystemInstruction::Transfer` sending lamports to `P` (no signature from `P` needed since it's just a receiving account).
3. Submit the program's normal initialization transaction, which CPIs `system_instruction::create_account(payer, P, lamports, space, owner)` signed via `invoke_signed` with `P`'s seeds.
4. Observe the CPI fails in `create_account()` at the `to.get_lamports() > 0` check with `SystemError::AccountAlreadyInUse`: [5](#0-4) 
5. Because `P` has no private key, no `SystemInstruction::Transfer` signed by `P` can be constructed to drain it, and unless the target program has an alternate `CreateAccountAllowPrefund`-based or `invoke_signed` recovery path, the lamports at `P` and the intended account are permanently unusable.

### Citations

**File:** programs/system/src/system_processor.rs (L160-174)
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

**File:** programs/system/src/system_processor.rs (L330-378)
```rust
        SystemInstruction::CreateAccount {
            lamports,
            space,
            owner,
        } => {
            instruction_context.check_number_of_instruction_accounts(2)?;
            let to_address = Address::create(
                instruction_context.get_key_of_instruction_account(1)?,
                None,
                invoke_context,
            )?;
            create_account(
                0,
                1,
                &to_address,
                lamports,
                space,
                &owner,
                &signers,
                invoke_context,
                &instruction_context,
            )
        }

        SystemInstruction::CreateAccountWithSeed {
            base,
            seed,
            lamports,
            space,
            owner,
        } => {
            instruction_context.check_number_of_instruction_accounts(2)?;
            let to_address = Address::create(
                instruction_context.get_key_of_instruction_account(1)?,
                Some((&base, &seed, &owner)),
                invoke_context,
            )?;
            create_account(
                0,
                1,
                &to_address,
                lamports,
                space,
                &owner,
                &signers,
                invoke_context,
                &instruction_context,
            )
        }
```

**File:** programs/system/src/system_processor.rs (L530-563)
```rust
        SystemInstruction::CreateAccountAllowPrefund {
            lamports,
            space,
            owner,
        } => {
            if !invoke_context
                .get_feature_set()
                .create_account_allow_prefund
            {
                return Err(InstructionError::InvalidInstructionData);
            }
            let from_and_lamports = if lamports > 0 {
                instruction_context.check_number_of_instruction_accounts(2)?;
                Some((1, lamports))
            } else {
                instruction_context.check_number_of_instruction_accounts(1)?;
                None
            };
            let to_address = Address::create(
                instruction_context.get_key_of_instruction_account(0)?,
                None,
                invoke_context,
            )?;
            create_account_allow_prefund(
                0,
                &to_address,
                from_and_lamports,
                space,
                &owner,
                &signers,
                invoke_context,
                &instruction_context,
            )
        }
```
