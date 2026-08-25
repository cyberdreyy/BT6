### Title
CreateAccount can be permanently front-run/blocked by unsolicited dust lamport transfers to the target address - (File: `programs/system/src/system_processor.rs`)

### Summary
The System Program's `create_account` instruction handler rejects account creation whenever the destination address already holds a nonzero lamport balance, treating this as `AccountAlreadyInUse`. Because `SystemInstruction::Transfer` allows any signer to send lamports to *any* pubkey without any cooperation or signature from the recipient, an attacker can pre-fund a target address (a PDA, an associated token account, a to-be-created program account, etc.) with a single dust lamport before the legitimate owner's transaction lands. This permanently blocks the legitimate `CreateAccount` call for that address — there is no "update"-style reset that clears the balance back to zero the way the reported Solidity bug's `update()` did, so the block is not just temporary but effectively permanent for that specific address, forcing users onto a different derivation or a different instruction entirely.

### Finding Description
In `create_account`, the check is a strict "lamports must be exactly zero" gate: [1](#0-0) 

```
fn create_account(...) -> Result<(), InstructionError> {
    // if it looks like the `to` account is already in use, bail
    {
        let mut to = instruction_context.try_borrow_instruction_account(to_account_index)?;
        if to.get_lamports() > 0 {
            ic_msg!(invoke_context, "Create Account: account {:?} already in use", to_address);
            return Err(SystemError::AccountAlreadyInUse.into());
        }
        allocate_and_assign(&mut to, to_address, space, owner, signers, invoke_context)?;
    }
    transfer(from_account_index, to_account_index, lamports, invoke_context, instruction_context)
}
``` [2](#0-1) 

The companion `transfer` instruction only requires the *sender* to sign — the destination account has no say in whether it receives lamports: [3](#0-2) 

This is the direct analog of the reported bug class: just as a `selfdestruct`-forced ETH transfer can push a contract's balance away from the value expected by a strict `==` check (and that balance can't be reliably zeroed again except by the intended flow), an attacker can push a Solana account's lamport balance from `0` to `>0` via an ordinary, unprivileged `Transfer` instruction, defeating the `to.get_lamports() > 0` gate in `create_account`. Since PDAs and associated-token-account addresses are deterministically derivable from public inputs (wallet pubkey, mint, program ID, seeds), an attacker does not need to guess anything — they can compute the target address ahead of time and dust-fund it before the intended owner ever transacts.

Agave's own codebase implicitly acknowledges this exact problem: it later introduced `SystemInstruction::CreateAccountAllowPrefund` specifically to route around the "must have zero lamports" restriction: [4](#0-3) 

That comment — "Intended for use where account has already had rent paid in whole or in part before creation" — is a direct acknowledgment that the strict-zero-lamports gate in the original `create_account` path is broken by pre-funding, exactly mirroring the report's root cause (a fixed equality/threshold check on balance that an unprivileged party can invalidate by directly moving value into the account, with no general recovery path back to the state the check expects).

### Impact Explanation
Any program or client-side flow that relies on the classic `SystemInstruction::CreateAccount` (rather than the newer `CreateAccountAllowPrefund`) to initialize a PDA, an associated token account, or any deterministically-derived account can be denial-of-serviced by a griefer who front-runs with a 1-lamport transfer to that address. Because there is no way to "un-fund" (i.e., zero) an account other than closing it (which requires signer authority the attacker doesn't have and typically doesn't apply to not-yet-created accounts), the block is durable, not merely transient like the original ETH report — the account creation flow will fail with `AccountAlreadyInUse` on every retry unless the caller switches to `CreateAccountAllowPrefund` or a manual allocate/assign/transfer sequence.

### Likelihood Explanation
The attack requires only an ordinary, unprivileged `SystemInstruction::Transfer` from an attacker-controlled funded account to a publicly-computable target address — no special access, no leaked keys, and no interaction with consensus-critical or privileged code paths. Any protocol that still issues `CreateAccount` (instead of `CreateAccountAllowPrefund`) against deterministically-derived addresses is exposed, and the target address is trivially known ahead of time by anyone who knows the derivation inputs.

### Recommendation
For any account-creation flow targeting deterministic/derivable addresses, use `CreateAccountAllowPrefund` (or equivalent allocate+assign+transfer sequencing that does not condition on the account's lamport balance being exactly zero) instead of `CreateAccount`. Document this guidance prominently for downstream program authors, since `CreateAccount`'s "lamports must be zero" invariant is trivially violable by any unprivileged transfer to the target address.

### Proof of Concept
1. Compute the deterministic target address `T` for a future account (e.g., an ATA or PDA) that a victim intends to create via `SystemInstruction::CreateAccount`.
2. Attacker submits `SystemInstruction::Transfer { lamports: 1 }` from an attacker-owned funded account to `T` — this requires no signature or consent from `T` (see `transfer` at `programs/system/src/system_processor.rs:245-267`, which only checks `from_account`'s signature).
3. Victim's subsequent `SystemInstruction::CreateAccount` targeting `T` now fails with `SystemError::AccountAlreadyInUse` because `to.get_lamports() > 0` (`programs/system/src/system_processor.rs:164-171`), regardless of the fact that `T` has no data and no owner assignment yet.
4. Victim's flow is durably blocked unless they detect the situation and switch to `CreateAccountAllowPrefund`.

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

**File:** programs/system/src/system_processor.rs (L245-267)
```rust
fn transfer(
    from_account_index: IndexOfAccount,
    to_account_index: IndexOfAccount,
    lamports: u64,
    invoke_context: &InvokeContext,
    instruction_context: &InstructionContext,
) -> Result<(), InstructionError> {
    if !instruction_context.is_instruction_account_signer(from_account_index)? {
        ic_msg!(
            invoke_context,
            "Transfer: `from` account {} must sign",
            instruction_context.get_key_of_instruction_account(from_account_index)?,
        );
        return Err(InstructionError::MissingRequiredSignature);
    }

    transfer_verified(
        from_account_index,
        to_account_index,
        lamports,
        invoke_context,
        instruction_context,
    )
```
