## Analog Found

### Title
Account Pre-funding Griefing Enables Front-Running/Blocking of Deterministic Account Addresses via `SystemInstruction::CreateAccount` - (File: `programs/system/src/system_processor.rs`)

### Summary
The Nayms report describes a class of bug where a deterministically-derived identifier (`objectId`) can be pre-claimed/front-run by an attacker to permanently block a legitimate entity from using that address. Agave's System Program has a directly analogous, long-standing issue: any unprivileged actor can permanently block a specific deterministic account address (e.g. a PDA, associated-token-account, or `create_with_seed` address) from ever being initialized via the standard `SystemInstruction::CreateAccount` path, simply by transferring lamports to that address ahead of time.

### Finding Description
`create_account()` only refuses to proceed if the destination account already has non-zero lamports: [1](#0-0) 

Because any signer can call the System Program's `Transfer` instruction to send lamports to *any* pubkey — including a PDA or a deterministically derivable address that hasn't been created yet — an attacker can "pre-fund" that address before the legitimate program/user attempts `CreateAccount`. When the legitimate creation transaction later executes `create_account`, it observes `to.get_lamports() > 0` and unconditionally returns `SystemError::AccountAlreadyInUse`, permanently denying initialization of that specific address under the normal `CreateAccount` instruction (an object cannot be "renamed" any more than the Nayms `objectId` can be reused, since the address is fixed by derivation, e.g. `Pubkey::create_with_seed` or `find_program_address`).

This is the same root-cause pattern as the report: a deterministic identifier (address) that is derived off-chain/algorithmically (via seed or PDA derivation) can be squatted by a low-privilege attacker before the legitimate creator submits their transaction, blocking that identifier from ever being validly used — exactly mirroring case 5 of the Nayms report where "an object cannot be removed once it is added to the system, so the id will be permanently associated" with the attacker's pre-emptive claim.

Agave's own developers have acknowledged this exact issue: `programs/system/src/system_processor.rs` recently added a new instruction, `CreateAccountAllowPrefund`, specifically to work around it, gated by the `create_account_allow_prefund` feature (SIMD-0312): [2](#0-1) [3](#0-2) 

However, this is only an *opt-in* alternative instruction — the original `CreateAccount` instruction (used pervasively by wallets, the Associated Token Account program, nonce-account creation, and virtually all existing on-chain programs) remains vulnerable to the griefing/front-running pattern, since it still hard-fails on any non-zero lamport balance rather than verifying ownership/emptiness in a way that tolerates pre-funding.

### Impact Explanation
Any program or user flow that relies on deriving a deterministic address (PDA, `create_with_seed` address, associated token account, nonce account) and later creating it via the standard `CreateAccount` path can be permanently denied service by a third party who has no relationship to, and no privileges over, the intended owner. This is a state-availability/DoS impact: a specific expected account (equivalent to the Nayms `objectId`) can never be created, blocking token support, nonce account setup, PDA-based protocol accounts, etc. It does not directly steal funds, but it can permanently disable specific on-chain state paths, matching the "block a specific token/feature from being supported" impact called out in the source report.

### Likelihood Explanation
Likelihood is high for the same reason case 5 was flagged as highest-risk in the original report: the attacker requires no elevated privilege whatsoever — merely the ability to send a `Transfer` instruction to a known/derivable pubkey (e.g. an associated token account address or a program-derived nonce/seed address) before the legitimate creator's transaction lands. This is a purely permissionless, front-runnable action available to any ordinary transaction sender.

### Recommendation
For addresses that are deterministically derivable (PDAs, `create_with_seed`), broaden use of the new `CreateAccountAllowPrefund`/prefund-tolerant semantics (already added for `SIMD-0312`) as the default/primary path rather than an opt-in instruction, so that pre-funding an address with lamports cannot block its legitimate initialization. Downstream programs (e.g. the associated-token-account program) should likewise be encouraged/required to use idempotent or prefund-tolerant creation flows instead of the classic `CreateAccount`, mirroring the report's suggestion of using deterministic-but-collision-resistant strategies instead of unconditionally rejecting any account with an existing balance.

### Proof of Concept
1. Compute the deterministic destination address that a victim program will later create (e.g., `Pubkey::create_with_seed(base, seed, &system_program::id())` as used in `cli/src/nonce.rs`, or an ATA/PDA address for a to-be-supported token). [4](#0-3) 
2. As an attacker with no special privilege, submit `SystemInstruction::Transfer` sending any nonzero lamports (even 1) to that address.
3. When the legitimate transaction later submits `SystemInstruction::CreateAccount` targeting the same address, `create_account()` sees `to.get_lamports() > 0` and returns `SystemError::AccountAlreadyInUse`: [5](#0-4) 
4. The legitimate creation transaction fails and the intended account/PDA can never be initialized through the standard `CreateAccount` instruction, permanently denying that specific deterministic object.

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

**File:** feature-set/src/lib.rs (L2463-2466)
```rust
        (
            create_account_allow_prefund::id(),
            "SIMD-0312: Enable CreateAccountAllowPrefund system program instruction",
        ),
```

**File:** cli/src/nonce.rs (L463-467)
```rust
    let nonce_account_address = if let Some(ref seed) = seed {
        Pubkey::create_with_seed(&nonce_account_pubkey, seed, &system_program::id())?
    } else {
        nonce_account_pubkey
    };
```
