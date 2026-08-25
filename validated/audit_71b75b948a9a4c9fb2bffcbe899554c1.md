I have enough information to write the final report.

### Title
Denial-of-service of `SystemInstruction::CreateAccount` via attacker-controlled lamport pre-funding of the target address - (File: `programs/system/src/system_processor.rs`)

### Summary
The `create_account` handler in the System Program enforces an `AccountAlreadyInUse` guard that requires the target account to hold **exactly zero lamports** before creation is allowed. Because Solana addresses (especially PDAs and seed-derived addresses) are deterministic and publicly computable off-chain, an unprivileged attacker can front-run a legitimate user by sending a trivial `Transfer` (as little as 1 lamport) to the not-yet-created target address. This permanently trips the `to.get_lamports() > 0` check for every subsequent `CreateAccount` instruction issued against that address, denying the legitimate owner the ability to ever create the account through the standard instruction, mirroring the reported bug class of "an attacker manipulates a balance to trip a strict revert condition and permanently block a legitimate operation."

### Finding Description
`create_account` in [1](#0-0)  executes:

```rust
let mut to = instruction_context.try_borrow_instruction_account(to_account_index)?;
if to.get_lamports() > 0 {
    ic_msg!(invoke_context, "Create Account: account {:?} already in use", to_address);
    return Err(SystemError::AccountAlreadyInUse.into());
}
allocate_and_assign(&mut to, to_address, space, owner, signers, invoke_context)?;
``` [2](#0-1) 

This check is analogous to the reported `amountOutMinimum` guard: it is a hardcoded, non-zero threshold (`lamports > 0`) whose satisfaction depends entirely on account state that any user can mutate via an ordinary, unprivileged system transfer, before the victim's transaction lands. The confirmed test `test_create_already_in_use` explicitly documents that "an account that already has lamports" causes `CreateAccount` to fail with `SystemError::AccountAlreadyInUse`, even though the account has no data and is owned by the default (uninitialized) program: [3](#0-2) .

Because PDAs and `create_with_seed` addresses are deterministic and computable by anyone from public inputs (base pubkey, seed, program id), an attacker can precompute the address a dApp/user is about to create and submit `SystemInstruction::Transfer{ lamports: 1 }` to that address ahead of the legitimate `CreateAccount` transaction. There is no way for the legitimate creator to "undo" this via `CreateAccount` — the instruction unconditionally rejects any pre-funded target. The codebase's own `create_account_allow_prefund` path, added specifically to support pre-funded targets, confirms this is a recognized limitation of the classic `create_account` instruction: [4](#0-3) .

### Impact Explanation
This is a low-cost, reliable griefing/DoS vector against any protocol that relies on `system_instruction::create_account` to materialize deterministic addresses on demand (e.g., program-derived state accounts, buffer/config accounts, escrow accounts, or any workflow that computes an address off-chain and later calls `CreateAccount` for it). An attacker paying a single transfer's fee (and 1 lamport) can permanently block account creation at that specific address for the legitimate party, forcing the application to either fail, derive a new address (breaking any protocol logic tied to the original deterministic address), or fall back to `CreateAccountAllowPrefund`/manual `Allocate`+`Assign`+`Transfer` sequencing — none of which is guaranteed to be what the calling program actually implements. This is a genuine state-mutation/availability impact reachable from an ordinary user's transaction with no privileged access required.

### Likelihood Explanation
Likelihood is high in principle (any address is public and derivable, the griefing transaction is a single cheap system transfer, and the attacker does not need to guess timing precisely — the block is permanent once triggered), but practical exploitation requires the attacker to know the target address before the victim's `CreateAccount` transaction is confirmed, which is generally true for any deterministic/PDA-style address computed off-chain. This mirrors the judge's assessment in the referenced report ("Medium" — real but requiring some setup/preconditions), since the attacker gains nothing financially and only griefs by paying gas plus a dust amount.

### Recommendation
Callers that need resilience against pre-funding griefing should use `SystemInstruction::CreateAccountAllowPrefund` (already present in the codebase) instead of the plain `CreateAccount` instruction wherever the target address is deterministic/public before creation, or perform `Allocate` + `Assign` + `Transfer` sequentially rather than relying on the atomic `CreateAccount` zero-balance precondition. Higher-level program frameworks (e.g., Anchor's `init` constraint equivalents) that wrap `create_account` should document or default to the prefund-tolerant path for PDAs whose addresses are publicly derivable prior to creation.

### Proof of Concept
1. Compute a deterministic address `A` (PDA or `create_with_seed` address) that a victim program/user intends to create via `system_instruction::create_account` in a future transaction.
2. Attacker submits an ordinary `SystemInstruction::Transfer` of 1 lamport from any funded account to `A`, landing before the victim's transaction.
3. Victim submits `SystemInstruction::CreateAccount{ lamports, space, owner }` targeting `A`.
4. `create_account` in `system_processor.rs` observes `to.get_lamports() == 1 > 0` and returns `SystemError::AccountAlreadyInUse`, exactly as reproduced by the existing test case at [3](#0-2) , permanently denying creation of the account at address `A` through the standard `CreateAccount` path.

### Citations

**File:** programs/system/src/system_processor.rs (L150-182)
```rust
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

**File:** programs/system/src/system_processor.rs (L1014-1041)
```rust
        // Attempt to create an account that already has lamports
        let owned_account = AccountSharedData::new(1, 0, &Pubkey::default());
        let unchanged_account = owned_account.clone();
        let accounts = process_instruction(
            &bincode::serialize(&SystemInstruction::CreateAccount {
                lamports: 50,
                space: 2,
                owner: new_owner,
            })
            .unwrap(),
            vec![(from, from_account), (owned_key, owned_account)],
            vec![
                AccountMeta {
                    pubkey: from,
                    is_signer: true,
                    is_writable: false,
                },
                AccountMeta {
                    pubkey: owned_key,
                    is_signer: true,
                    is_writable: false,
                },
            ],
            Err(SystemError::AccountAlreadyInUse.into()),
        );
        assert_eq!(accounts[0].lamports(), 100);
        assert_eq!(accounts[1], unchanged_account);
    }
```
