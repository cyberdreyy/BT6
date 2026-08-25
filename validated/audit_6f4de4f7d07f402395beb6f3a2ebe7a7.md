### Title
Griefing DoS via lamport pre-funding makes `SystemInstruction::CreateAccount` fail with `AccountAlreadyInUse` - (File: `programs/system/src/system_processor.rs`)

### Summary
The `create_account` handler in the System builtin program hard-fails any account-creation transaction if the target address already holds a non-zero lamport balance. Because Solana account addresses (keypairs, PDAs, seed-derived addresses) are known ahead of time, an attacker can simply send 1 lamport to a soon-to-be-created address before the legitimate `CreateAccount` transaction lands, causing the legitimate transaction to fail deterministically — the same "assert on a pre-condition an attacker can grief via front-running" pattern described in the Tempus `depositAndFix` report, just enforced with an `InstructionError` instead of an EVM `assert`.

### Finding Description
`create_account` in `programs/system/src/system_processor.rs` unconditionally rejects account creation once the target has any lamports: [1](#0-0) 

Specifically:
```
if to.get_lamports() > 0 {
    ic_msg!(invoke_context, "Create Account: account {:?} already in use", to_address);
    return Err(SystemError::AccountAlreadyInUse.into());
}
``` [2](#0-1) 

This is functionally identical to the reported bug class: a precondition ("the destination account currently holds zero balance") that is meant to protect against re-initializing an existing account, but is trivially violated by any third party sending lamports to the address, since account addresses for PDAs/derived accounts/new keypairs are public and predictable before the creation transaction executes. Any ordinary user (not a validator, not privileged) can submit a `system_instruction::transfer` of 1 lamport to the target address ahead of (or in the same slot/before) the intended `CreateAccount` transaction, and the intended transaction will then be rejected with `SystemError::AccountAlreadyInUse`.

The project's own developers evidently recognized this exact griefing class: they added a parallel opt-in instruction, `SystemInstruction::CreateAccountAllowPrefund`, guarded by the `create_account_allow_prefund` feature, whose explicit purpose is to create an account "without checking for 0 lamports," "intended for use where account has already had rent paid in whole or in part before creation": [3](#0-2) 

The existence of this dedicated bypass instruction is direct confirmation that the standard `CreateAccount` path is griefable by unprivileged lamport transfers, and that this is treated as a real, addressed-but-not-eliminated issue (the fix is opt-in and only helps callers who explicitly switch to the new instruction).

### Impact Explanation
This is a pure griefing/DoS vector reachable by any unprivileged user submitting a standard transaction:
- Any dapp/program/user flow relying on the classic `SystemInstruction::CreateAccount` (e.g. creating a new keypair-based account, a PDA via CPI from a program that hasn't been upgraded to `CreateAccountAllowPrefund`, escrow/vault accounts, token accounts created by legacy flows, etc.) can be permanently blocked by an attacker who repeatedly front-runs the creation with a 1-lamport transfer to the deterministic target address.
- Because the check triggers a hard `InstructionError::from(AccountAlreadyInUse)`, the transaction (and any batched instructions in the same transaction) fails and the address becomes effectively "squatted" until the creator either abandons that address or the program logic is updated to use the new prefund-aware instruction (which requires a feature activation and protocol/client-level opt-in).
- This matches the "Medium" severity classification of the original finding: it does not steal funds or break consensus, but it can permanently deny legitimate account-creation transactions for any user/program still using the standard `CreateAccount` instruction.

### Likelihood Explanation
High for any target whose address is derivable/predictable in advance (PDAs, `create_with_seed` addresses, or any keypair address broadcast prior to the creation transaction being confirmed). The attack requires only a standard `system_instruction::transfer` for 1 lamport plus knowledge of the target pubkey and roughly which slot the create-account transaction will land in — entirely within reach of an ordinary user with no special privileges, matching the "unprivileged ... transaction" requirement.

### Recommendation
- For any code path (client, program CPI, or the CLI) that still issues `SystemInstruction::CreateAccount`, migrate to `SystemInstruction::CreateAccountAllowPrefund` (once its feature is active) so pre-funded target accounts do not cause creation to fail.
- Alternatively/additionally, harden the standard `create_account` check to distinguish "account has data/owner already set" (a real re-initialization risk) from "account merely holds stray lamports" (no re-initialization risk), and allow creation to proceed by absorbing the pre-existing lamports rather than failing outright — mirroring the logic already implemented in `create_account_allow_prefund`.
- Document to downstream integrators (wallets, program authors) that address-squatting via dust-lamport transfers is possible against the legacy `CreateAccount` instruction so they can proactively adopt the prefund-tolerant path.

### Proof of Concept
1. Client/attacker learns the deterministic target address `T` that a victim's transaction will use as the `new_account` in a `system_instruction::create_account(payer, T, lamports, space, owner)` instruction (e.g., a PDA, a `create_with_seed` address, or a freshly generated keypair whose pubkey has been broadcast/observed).
2. Attacker submits an ordinary `system_instruction::transfer(attacker, T, 1)` transaction so it lands in the same or an earlier slot than the victim's `CreateAccount` transaction.
3. When the victim's transaction executes, `create_account` in `system_processor.rs` sees `to.get_lamports() > 0` and returns `SystemError::AccountAlreadyInUse`, causing the entire victim transaction to fail: [2](#0-1) 
4. Attacker repeats step 2 for every retry, indefinitely denying account creation at address `T` unless the victim switches to `CreateAccountAllowPrefund`.

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
