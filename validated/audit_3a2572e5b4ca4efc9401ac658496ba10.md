### Title
Front-running griefing of deterministic `CreateAccount`/`CreateAccountWithSeed` addresses via lamport prefunding causes permanent transaction failure - (File: `programs/system/src/system_processor.rs`)

### Summary
The System Program's `create_account` path treats any nonzero lamport balance on the target address as proof the account is "already in use" and unconditionally rejects the instruction. Because the target address for `CreateAccount`/`CreateAccountWithSeed` (and for PDA-derived accounts created via CPI, e.g. `bpf_loader_upgradeable`'s ProgramData account) is fully deterministic and publicly known before the creating transaction lands, an attacker can front-run the legitimate transaction with a trivial `SystemInstruction::Transfer` of 1 lamport to that address. Any subsequent attempt to create the account fails with `SystemError::AccountAlreadyInUse`, and — for addresses that cannot be re-derived with a different seed/base (e.g. a PDA fixed by an immutable program pubkey) — this failure is permanent, since nothing ever clears the "no lamports" precondition once it is violated.

### Finding Description
`create_account` in `system_processor.rs` bails out as soon as the destination account has `lamports > 0`, before any ownership/signer checks matter: [1](#0-0) 

This function backs both `SystemInstruction::CreateAccount` and `SystemInstruction::CreateAccountWithSeed`: [2](#0-1) 

The destination address for `CreateAccountWithSeed` is derived deterministically from `(base, seed, owner)` via `Pubkey::create_with_seed`, and is checked for exact equality with the supplied address in `Address::create`: [3](#0-2) 

Because this address is a pure function of publicly known inputs (the transaction itself reveals `base`, `seed`, `owner`), any observer of the mempool/gossip can compute the same address and race a plain `Transfer` instruction — which requires no signature or special permission from the destination — to fund it with 1 lamport before the legitimate `CreateAccount(WithSeed)` transaction lands: [4](#0-3) 

Once the target has any lamports, `create_account` will always return `AccountAlreadyInUse` for every future attempt, because nothing in this code path ever drains or resets `lamports` for that address independent of a caller-controlled operation. The mitigation the codebase itself later added — `SystemInstruction::CreateAccountAllowPrefund`, which explicitly skips the `lamports > 0` check — proves this exact griefing pattern is understood and treated as a distinct bug class, yet the fix is opt-in and feature-gated, leaving `CreateAccount`/`CreateAccountWithSeed` unprotected by default: [5](#0-4) [6](#0-5) 

The impact is amplified for the case of a program-derived (PDA) account whose address is *immutably* tied to a fixed pubkey with no alternative seed to retry with. The upgradeable BPF loader's `DeployWithMaxDataLen` handler creates the `ProgramData` account at an address derived solely from the (immutable) program pubkey via `find_program_address`, using this same vulnerable `create_account` path internally through `native_invoke_signed`: [7](#0-6) 

If an attacker observes a pending `DeployWithMaxDataLen` transaction (which necessarily reveals the new program's pubkey) and front-runs it with a 1-lamport `Transfer` to the derived `programdata_key`, the deploy transaction will permanently fail with `AccountAlreadyInUse`, and — unlike the `CreateAccountWithSeed` case where a user can simply pick a new `seed` — the developer cannot re-derive a different `programdata_key` for the same `program_pubkey`; they must abandon that keypair entirely and lose their intended program address.

### Impact Explanation
This is a state-mutation griefing/DoS vulnerability, not a fund-theft bug: it causes transaction reverts and, in the PDA case, a permanent, unrecoverable loss of the ability to deploy a program to a chosen address. It matches the accepted impact class of "concrete ... state mutation ... or replay-path panic or exhaustion" because a single 1-lamport transfer permanently locks a deterministic account address out of ever being validly created by the standard `CreateAccount`/`CreateAccountWithSeed` instructions. Severity is medium: no funds are stolen, and the attacker has little direct financial incentive, but it produces reliable, low-cost denial of service against any protocol or user relying on deterministic account creation (vanity/PDA program deploys, escrow/vault accounts derived with a seed, etc.).

### Likelihood Explanation
Likelihood is medium-to-low: the attack is trivial to execute (one `Transfer` instruction, 1 lamport, no special privileges) and only requires observing the destination address before the legitimate creation transaction is confirmed (visible in the transaction itself, or in the gossip/QUIC-ingested transaction before inclusion). It requires no signature forgery, no bug in transaction sanitization, and no elevated access — any user submitting ordinary transactions can carry it out. The main deterrent is the attacker's lack of direct profit motive, matching the same medium-likelihood rating as the referenced report.

### Recommendation
For `CreateAccount`/`CreateAccountWithSeed`, avoid rejecting based solely on `lamports > 0`; instead check only the state that actually indicates prior initialization (non-system owner and/or non-empty account `data`), and allow `create_account` to accumulate the prefunded lamports rather than aborting — effectively making the default `CreateAccount` behavior consistent with the already-implemented `CreateAccountAllowPrefund`/`create_account_allow_prefund` semantics. For the `bpf_loader_upgradeable` deploy path specifically, use the prefund-tolerant creation primitive for the `ProgramData` account so that a griefer sending stray lamports to a not-yet-existent PDA cannot permanently block deployment to that program address.

### Proof of Concept
1. Alice (or any protocol) prepares a `SystemInstruction::CreateAccountWithSeed { base, seed, owner, lamports, space }` transaction; the resulting `to_address = Pubkey::create_with_seed(&base, &seed, &owner)` is fully determined by publicly visible transaction fields.
2. Attacker observes the pending transaction (mempool/gossip) and computes `to_address` identically.
3. Attacker submits a `SystemInstruction::Transfer { lamports: 1 }` to `to_address` and gets it confirmed before Alice's transaction.
4. Alice's `CreateAccountWithSeed` transaction now executes `create_account`, which observes `to.get_lamports() > 0` at `programs/system/src/system_processor.rs:164` and returns `SystemError::AccountAlreadyInUse`, reverting Alice's transaction. Alice must choose a new `seed`/`base` to retry — for the `bpf_loader_upgradeable` `DeployWithMaxDataLen` case (`programs/bpf_loader/src/lib.rs:279-311`), where the `programdata_key` is fixed to the immutable `program_pubkey`, no retry with a different derived address is possible, permanently blocking deployment to that program id.

### Citations

**File:** programs/system/src/system_processor.rs (L43-72)
```rust
    fn create(
        address: &Pubkey,
        with_seed: Option<(&Pubkey, &str, &Pubkey)>,
        invoke_context: &InvokeContext,
    ) -> Result<Self, InstructionError> {
        let base = if let Some((base, seed, owner)) = with_seed {
            // The conversion from `PubkeyError` to `InstructionError` through
            // num-traits is incorrect, but it's the existing behavior.
            let address_with_seed =
                Pubkey::create_with_seed(base, seed, owner).map_err(|e| e as u64)?;
            // re-derive the address, must match the supplied address
            if *address != address_with_seed {
                ic_msg!(
                    invoke_context,
                    "Create: address {} does not match derived address {}",
                    address,
                    address_with_seed
                );
                return Err(SystemError::AddressWithSeedMismatch.into());
            }
            Some(*base)
        } else {
            None
        };

        Ok(Self {
            address: *address,
            base,
        })
    }
```

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

**File:** programs/system/src/system_processor.rs (L389-392)
```rust
        SystemInstruction::Transfer { lamports } => {
            instruction_context.check_number_of_instruction_accounts(2)?;
            transfer(0, 1, lamports, invoke_context, &instruction_context)
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

**File:** programs/bpf_loader/src/lib.rs (L279-311)
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
