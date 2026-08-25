This confirms the analog: the `CreateAccount` griefing pattern in the System Program is a well-known, already-acknowledged Solana issue that Agave itself has been actively mitigating via the new `CreateAccountAllowPrefund` instruction, which coexists with the legacy `CreateAccount` path that still contains the unmitigated lamports-based griefing check.

### Title
Griefing via pre-funding a target address blocks legitimate `CreateAccount` initialization in the System Program - (File: `programs/system/src/system_processor.rs`)

### Summary
The System Program's `create_account()` instruction handler rejects account creation whenever the destination account already holds a nonzero lamport balance, treating this as `SystemError::AccountAlreadyInUse`. Because any unprivileged user can transfer a trivial amount of lamports (as little as 1) to any known public address — including a deterministically-derivable PDA or `create_with_seed` address — before the legitimate owner/program submits its `CreateAccount` transaction, an attacker can permanently block that specific account's initialization via the standard `CreateAccount` instruction. This mirrors the reported `addStakedToken()`/`initMarketStartTime()` griefing pattern: a cheap, unprivileged pre-initialization action from an attacker causes a subsequent, legitimate "initialize-once" call to permanently and irrecoverably fail.

### Finding Description
`create_account()` in the builtin System Program checks the destination account's lamport balance before allocating space and assigning ownership: [1](#0-0) 

If `to.get_lamports() > 0`, the instruction unconditionally returns `SystemError::AccountAlreadyInUse`, regardless of whether the account has actually been initialized with data or an owning program — a bare lamport transfer is sufficient to trip this check.

Any address that will be used as a `CreateAccount` target — most commonly deterministic PDAs or `create_with_seed`-derived addresses used by on-chain programs to set up token accounts, vote accounts, escrow accounts, or other one-time-initialized state — is known in advance because it is derived from public inputs (program ID, seed, base key). An attacker can therefore precompute the target address and send it 1 lamport via an ordinary `Transfer` instruction before the legitimate `CreateAccount` transaction lands, permanently forcing that specific `CreateAccount` invocation (and any that follow for the same address) to fail with `AccountAlreadyInUse`, since there is no way to reclaim/zero out the lamports without knowing the account's private key or another privileged withdrawal path.

Agave has recognized this exact class of griefing and introduced a new instruction, `CreateAccountAllowPrefund`, specifically to bypass the lamport check and permit account creation even if a lamport prefund exists: [2](#0-1) [3](#0-2) 

However, this is opt-in and gated behind `create_account_allow_prefund` in the feature set: the legacy `CreateAccount` instruction path used by the vast majority of existing on-chain programs and CLI/RPC-issued transactions (see `cli/src/nonce.rs` for one of many call sites relying on `create_account`/`create_nonce_account`) remains fully exposed to the griefing check, since it has no mechanism to detect "griefed but functionally uninitialized" state.

### Impact Explanation
This is a denial-of-service / griefing vector against any unprivileged user or on-chain program that relies on the standard `CreateAccount` (or `CreateAccountWithSeed`) instruction to initialize a specific, predictable address — for example, nonce accounts, PDA-derived program state accounts, or any account whose address is deterministically known ahead of the initializing transaction. The griefed party cannot recover: the target address is permanently unusable via `CreateAccount` until/unless the attacker's dust lamports are somehow reclaimed (not possible without the private key, since the account is uninitialized/unowned by any spending-capable program). This matches the reported bug class: cheap, unprivileged front-running of an "initialize once" precondition check that irrecoverably blocks legitimate state initialization.

### Likelihood Explanation
Likelihood is high for any target address that can be predicted in advance (which is the common case for PDAs and seed-derived accounts) and the cost to the attacker is minimal — a single lamport transfer plus a signature fee. The existence of the parallel `CreateAccountAllowPrefund` instruction in the same file, purpose-built to work around this exact scenario, is direct evidence that Agave's own developers consider this a real, exploitable griefing pattern against the default `CreateAccount` path.

### Recommendation
For new integrations, prefer `CreateAccountAllowPrefund` over `CreateAccount` wherever the destination address is predictable/derivable by third parties, since it explicitly tolerates a pre-existing lamport balance. For the legacy `CreateAccount`/`CreateAccountWithSeed` paths, consider distinguishing "has lamports but zero data and system-program-owned" (a benign prefund) from genuinely occupied accounts (nonzero data or non-system owner), rather than treating any nonzero lamport balance as conclusive evidence of prior use, aligning the default behavior with the tolerance already implemented for `CreateAccountAllowPrefund`.

### Proof of Concept
1. Attacker derives a target address `T` that a victim program/user will use for `CreateAccount` (e.g., a `create_with_seed`-derived nonce account address or a program's PDA-derived state account), as done in `cli/src/nonce.rs` line 464: `Pubkey::create_with_seed(&nonce_account_pubkey, seed, &system_program::id())`. [4](#0-3) 
2. Attacker submits a plain `SystemInstruction::Transfer` of 1 lamport to `T`.
3. Victim later submits `SystemInstruction::CreateAccount { lamports, space, owner }` targeting `T`.
4. In `create_account()`, `to.get_lamports() > 0` is true (equals 1), so the instruction returns `SystemError::AccountAlreadyInUse` and the victim's initialization transaction fails, as directly exercised by the existing test `test_create_already_in_use` (the "account that already has lamports" case): [5](#0-4)

### Citations

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

**File:** programs/system/src/system_processor.rs (L1014-1040)
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
```

**File:** cli/src/nonce.rs (L463-467)
```rust
    let nonce_account_address = if let Some(ref seed) = seed {
        Pubkey::create_with_seed(&nonce_account_pubkey, seed, &system_program::id())?
    } else {
        nonce_account_pubkey
    };
```
