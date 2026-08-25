### Title
Griefing of `SystemInstruction::CreateAccount` via front-run dust-lamport transfer causes legitimate account-creation transactions to fail - (File: `programs/system/src/system_processor.rs`)

### Summary
The `CreateAccount` handler in Agave's builtin System Program rejects account creation whenever the target address already holds any lamports, regardless of who put them there. Because destination addresses for `CreateAccount` (and `CreateAccountWithSeed`) are public/derivable ahead of time, an attacker can front-run a user's pending `CreateAccount` transaction with a trivial, cheap `Transfer` of 1 lamport to that same target address. When the victim's transaction lands afterward, the `to.get_lamports() > 0` guard fires and the whole transaction fails with `SystemError::AccountAlreadyInUse`, forcing retries and wasting the fee payer's compute/fee budget. This mirrors the reported smart-contract bug class: a cheap, front-run state mutation (funding a shared/derivable slot with a dust amount) that flips a guard condition (`position.amount != 0` there vs. `to.get_lamports() > 0` here) and breaks the legitimate transaction that assumed a pristine state.

### Finding Description
`create_account()` first checks whether the destination account already has a nonzero lamport balance before allowing allocation/assignment/transfer to proceed: [1](#0-0) 

This check is reached from the `SystemInstruction::CreateAccount` and `SystemInstruction::CreateAccountWithSeed` dispatch arms in the System Program entrypoint, using account index 1 (the "to" account) supplied by the caller's transaction: [2](#0-1) 

Because the destination public key for a plain `CreateAccount` instruction is chosen off-chain by the client (often a freshly generated keypair, or in composite flows a PDA/derived address that is publicly computable, e.g. associated-token-style addresses derived by higher-level programs), any third party observing the pending transaction in the mempool/QUIC ingest path can submit a competing `SystemInstruction::Transfer` of a single lamport to that exact address with a higher priority fee. If it lands first, the account now has `lamports > 0`, and the original `CreateAccount` transaction subsequently fails deterministically with `AccountAlreadyInUse`.

Agave's own codebase corroborates that this exact failure mode is a recognized problem: a new instruction, `SystemInstruction::CreateAccountAllowPrefund`, and its handler `create_account_allow_prefund()`, were added specifically to tolerate a pre-funded destination account and skip the `lamports > 0` rejection, gated behind the `create_account_allow_prefund` feature: [3](#0-2) [4](#0-3) 

This confirms the root cause (funding-before-creation breaks `CreateAccount`) is an acknowledged issue class in Agave, but the fix is opt-in: any caller (wallet, program, or CPI path) that still issues the legacy `SystemInstruction::CreateAccount`/`CreateAccountWithSeed` instructions remains exposed, since the vulnerable code path (`create_account`) is unconditionally reachable and still the default instruction used throughout the ecosystem.

### Impact Explanation
This is a griefing/availability issue rather than a fund-theft issue: the attacker cannot steal the victim's lamports (the dust lamports sent to the target address ultimately belong to whichever party ends up controlling that account), but they can reliably and cheaply cause a targeted user's account-creation transaction to fail on-chain, consuming the victim's transaction fee and requiring manual remediation (the address is now considered "in use" and the standard `CreateAccount` instruction can no longer be used against it; the caller must switch to `CreateAccountWithSeed`/`CreateAccountAllowPrefund`/`Allocate`+`Assign` workarounds). At scale this enables selective denial-of-service against specific users, protocols, or bots whose destination addresses are predictable (e.g., deterministic account creation flows, faucets, or airdrop scripts), degrading transaction-processing reliability for a targeted victim without requiring any special privilege from the attacker beyond front-running with a higher fee.

### Likelihood Explanation
Likelihood is high for any workflow where the destination pubkey is knowable before the `CreateAccount` transaction confirms (e.g., visible in the mempool/QUIC packet, or derivable off-chain). The attack requires only a single `Transfer` instruction of 1 lamport plus a competitive priority fee — well within reach of any unprivileged user submitting ordinary transactions through the standard RPC/QUIC ingest path, with no special validator or protocol privileges needed.

### Recommendation
- Prefer/standardize on `SystemInstruction::CreateAccountAllowPrefund` (already implemented) for account-creation flows where a pre-existing balance should not block creation, and enable/ship the `create_account_allow_prefund` feature broadly so downstream tooling (CLI, SDKs, associated-token-style programs) migrate off the griefable `CreateAccount` path.
- For flows still using classic `CreateAccount`, document/encourage checking-and-tolerating a nonzero pre-existing balance (i.e., treat "already funded, still uninitialized" as valid) rather than treating any lamports as proof of prior use, consistent with the semantics already implemented in `create_account_allow_prefund`.

### Proof of Concept
1. Victim signs and broadcasts `Transaction A`: `SystemInstruction::CreateAccount { lamports, space, owner }` targeting a freshly generated (but publicly visible in the pending transaction) pubkey `T`.
2. Attacker observes `Transaction A` prior to confirmation (e.g., via QUIC/mempool visibility) and immediately submits `Transaction B`: `SystemInstruction::Transfer { lamports: 1 }` from an attacker-controlled account to `T`, with a higher compute-unit price so it lands first in the same or an earlier slot.
3. `Transaction B` executes successfully; account `T` now has `lamports == 1`.
4. `Transaction A` executes; `create_account()` at `programs/system/src/system_processor.rs:164` sees `to.get_lamports() > 0` and returns `SystemError::AccountAlreadyInUse`, causing `Transaction A` to fail deterministically.
5. The victim's account-creation flow is broken and must be manually retried using a different instruction path (`CreateAccountWithSeed`/`CreateAccountAllowPrefund`).

### Citations

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

**File:** programs/system/src/system_processor.rs (L330-352)
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
