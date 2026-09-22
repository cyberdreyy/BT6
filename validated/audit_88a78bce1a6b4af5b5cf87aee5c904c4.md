## Title
Unprivileged lamport pre-funding griefs `SystemInstruction::CreateAccount`, permanently blocking account creation (DoS) — ([File: programs/system/src/system_processor.rs])

### Summary
The external report describes a griefing pattern: an attacker can send an unsolicited amount of the underlying asset to a target address, which causes a downstream invariant check (`assert(balance == amount)`) to fail forever, permanently breaking a legitimate operation. Agave has a directly analogous, reachable pattern in the System Program's `create_account` instruction handler: it rejects account creation if the destination account already holds any lamports (`> 0`), and since any unprivileged transaction sender can transfer lamports to *any* pubkey without that account's signature, an attacker can pre-fund a not-yet-created address with a single lamport to permanently block its creation via the standard `CreateAccount` instruction.

### Finding Description
`create_account()` in [1](#0-0)  performs this check before allowing account creation:
```
if to.get_lamports() > 0 {
    ...
    return Err(SystemError::AccountAlreadyInUse.into());
}
```
This is the exact analog of the WETHGateway `assert(_weth.balanceOf(address(this)) == amount)` line: a strict, pre-existing-balance invariant that any unprivileged party can violate ahead of time because Solana's `SystemInstruction::Transfer` lets anyone move lamports into an arbitrary destination pubkey without any signature/authorization from that destination [2](#0-1) . Once even 1 lamport lands in the target address, any subsequent `CreateAccount` instruction targeting that address will hit `SystemError::AccountAlreadyInUse` and fail, exactly mirroring the "revert forever" griefing described in the report.

Agave engineers were aware of this exact class of issue and added a mitigation instruction, `CreateAccountAllowPrefund` / `create_account_allow_prefund()`, which skips the zero-lamport check and instead only fails if the account has data or a non-system owner [3](#0-2) . However, this new instruction is feature-gated (`invoke_context.get_feature_set().create_account_allow_prefund`) and must be *explicitly* chosen by the calling program/instruction builder [4](#0-3) . The legacy `CreateAccount` path used pervasively today (e.g., by wallets, the Associated Token Account program, PDA-based account creation flows invoked via CPI) still uses the vulnerable `create_account()` check.

### Impact Explanation
Any unprivileged actor who can predict a to-be-created address (deterministic PDAs, associated-token-account addresses derived from `(wallet, mint)`, or any address whose pubkey is known in advance, e.g. before its keypair is even used) can send it 1 lamport ahead of time. This permanently blocks any program or user relying on the standard `CreateAccount` instruction from initializing that account, since `to.get_lamports() > 0` will always be true. This is a denial-of-service against account creation flows reachable by a single, cheap, unprivileged transaction — matching the "concrete... cluster/transaction-triggered" impact bar via unauthorized griefing of a specific account's initialization, not just a UX inconvenience.

### Likelihood Explanation
High. No signature or special privilege is required beyond funding a `SystemInstruction::Transfer` transaction fee — any address (including PDAs and ATAs, whose derivation is public) can be targeted before the legitimate creation transaction lands. This is a well-known, low-cost, front-runnable griefing vector, and the codebase's own introduction of `create_account_allow_prefund` as an explicit remediation instruction confirms the vulnerability class is recognized, but the fix is opt-in per instruction, leaving all legacy `CreateAccount` call sites exposed.

### Recommendation
For any protocol-level or SDK-level account-creation flow that is reachable via a predictable address (PDAs, ATAs, or otherwise), prefer/require the `CreateAccountAllowPrefund` instruction (once its feature is active) instead of legacy `CreateAccount`, or have `create_account()` tolerate pre-funded balances by only failing when the account has non-empty data or a non-system owner, transferring/topping-up lamports rather than erroring out — mirroring the report's recommendation to remove the strict equality/zero check and let the flow simply account for any pre-existing balance instead of reverting.

### Proof of Concept
1. Attacker computes/derives the target address `to` in advance (e.g., a PDA or an ATA for `(wallet, mint)`).
2. Attacker submits `SystemInstruction::Transfer { lamports: 1 }` from any funded account to `to` — no signature from `to` required.
3. Victim later submits `SystemInstruction::CreateAccount { lamports, space, owner }` with `to` as the destination.
4. `create_account()` observes `to.get_lamports() > 0` and returns `SystemError::AccountAlreadyInUse`, permanently failing account creation for that address [5](#0-4) , confirmed by the existing test `test_create_already_in_use` which asserts this exact failure mode when the account "already has lamports" [6](#0-5) .

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

**File:** programs/system/src/system_processor.rs (L216-243)
```rust
fn transfer_verified(
    from_account_index: IndexOfAccount,
    to_account_index: IndexOfAccount,
    lamports: u64,
    invoke_context: &InvokeContext,
    instruction_context: &InstructionContext,
) -> Result<(), InstructionError> {
    let mut from = instruction_context.try_borrow_instruction_account(from_account_index)?;
    if !from.get_data().is_empty() {
        ic_msg!(invoke_context, "Transfer: `from` must not carry data");
        return Err(InstructionError::InvalidArgument);
    }
    if lamports > from.get_lamports() {
        ic_msg!(
            invoke_context,
            "Transfer: insufficient lamports {}, need {}",
            from.get_lamports(),
            lamports
        );
        return Err(SystemError::ResultWithNegativeLamports.into());
    }

    from.checked_sub_lamports(lamports)?;
    drop(from);
    let mut to = instruction_context.try_borrow_instruction_account(to_account_index)?;
    to.checked_add_lamports(lamports)?;
    Ok(())
}
```

**File:** programs/system/src/system_processor.rs (L530-547)
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
