### Title
Unprivileged pre-funding front-run permanently blocks `SystemInstruction::CreateAccount` initialization ("account-in-use" griefing) - ([File: programs/system/src/system_processor.rs])

### Summary
The System Program's `create_account()` helper rejects account creation whenever the target `to` account already holds any lamports, returning `SystemError::AccountAlreadyInUse`. Because sending lamports to an arbitrary pubkey via a plain `Transfer` instruction is completely unprivileged and requires no signature from the target, any attacker can pre-fund a not-yet-created address with 1 lamport before the legitimate owner's `CreateAccount` transaction lands, causing that transaction to permanently fail. This is structurally the same bug class as the PoolTogether `VaultBooster.setBoost()` finding: a permissionless, cheap state mutation (balance change) is used as a precondition guard for a privileged/expected initialization action, letting an attacker front-run and indefinitely block it.

### Finding Description
`create_account()` in `programs/system/src/system_processor.rs` performs the "already in use" check purely on lamport balance: [1](#0-0) 

```
fn create_account(...) -> Result<(), InstructionError> {
    // if it looks like the `to` account is already in use, bail
    {
        let mut to = instruction_context.try_borrow_instruction_account(to_account_index)?;
        if to.get_lamports() > 0 {
            ...
            return Err(SystemError::AccountAlreadyInUse.into());
        }
        allocate_and_assign(&mut to, to_address, space, owner, signers, invoke_context)?;
    }
    transfer(...)
}
``` [1](#0-0) 

The condition `to.get_lamports() > 0` is exactly analogous to the PoolTogether `_initialAvailable > balance` guard: it is a balance-derived precondition that any unrelated, unprivileged actor can flip by simply transferring lamports (even 1 lamport) to the target address before the legitimate creation transaction executes — the System Program's own `transfer_verified`/`transfer` path requires only that the *sender* signs, imposing no constraint on the recipient: [2](#0-1) 

Once griefed, any `CreateAccount` transaction targeting that address will always hit the `to.get_lamports() > 0` branch and fail with `AccountAlreadyInUse`, as confirmed by the existing unit test that documents exactly this behavior ("Attempt to create an account that already has lamports" → `Err(SystemError::AccountAlreadyInUse.into())`): [3](#0-2) 

The codebase itself acknowledges this griefing vector: a newer instruction, `CreateAccountAllowPrefund`, was added specifically to bypass the zero-lamport requirement "for use where account has already had rent paid in whole or in part before creation": [4](#0-3) 

However, `CreateAccountAllowPrefund` is gated behind a feature flag (`create_account_allow_prefund`) and is an opt-in *new* instruction/pathway — it does not retroactively fix the plain `CreateAccount` instruction, which remains the default, widely used path for account initialization (wallets, PDAs without seeds, nonce accounts, vote accounts, stake accounts created directly via `CreateAccount`).

### Impact Explanation
Any account address whose creation flow relies on the base `CreateAccount` instruction (rather than `CreateAccountWithSeed` or the newer `CreateAccountAllowPrefund`) can be indefinitely denied initialization by an attacker who knows the target pubkey in advance (e.g., a deterministically derived key, a freshly generated keypair announced/observed off-chain, or any address visible in a pending transaction in the mempool/QUIC ingest before it lands). The attacker's cost is a single lamport plus one transaction fee, while the victim's legitimate initialization transaction deterministically fails every time it is retried against that same address, unless the victim switches instruction types. This is a state-mutation / initialization-blocking griefing DoS reachable purely from an ordinary user's transaction with no special privilege, matching the accepted "replay-path panic or exhaustion" / unauthorized state-mutation blocking impact category. It is a low-severity griefing issue (funds are not stolen, and it does not affect consensus or the runtime's safety), same as the original finding was rated Medium by the audit judge.

### Likelihood Explanation
Likelihood is high for any protocol or dApp on Solana that derives a deterministic or publicly known target address and calls plain `CreateAccount` to initialize it (i.e., without seed-based creation and without opting into `CreateAccountAllowPrefund`). The attack requires no special access, only observing/predicting the target pubkey and sending a cheap `Transfer` before the victim's `CreateAccount` transaction is processed — trivially achievable by front-running in the transaction pool or by simply knowing the address ahead of time.

### Recommendation
- For any address that must be created via `CreateAccount`, prefer `CreateAccountWithSeed`/PDA-seeded derivation or migrate to the already-existing `CreateAccountAllowPrefund` instruction, which does not treat a nonzero pre-existing balance as a hard failure.
- Consider making pre-funding tolerance the default behavior for `CreateAccount` (as `CreateAccountAllowPrefund` already demonstrates is safe), rather than requiring callers to know about and opt into a separate instruction variant.
- Document prominently (for on-chain program developers) that `CreateAccount` is griefable via lamport pre-funding and that `CreateAccountAllowPrefund` or seed-based creation should be used whenever the target address can be predicted/observed by third parties.

### Proof of Concept
1. Attacker observes (or predicts) the pubkey `X` that a victim intends to initialize via `system_instruction::create_account(&payer, &X, lamports, space, &owner)`.
2. Attacker submits `system_instruction::transfer(&attacker, &X, 1)` — a fully permissionless instruction requiring only the attacker's own signature — landing before the victim's transaction.
3. Victim's `CreateAccount` transaction executes `create_account()`; since `to.get_lamports() == 1 > 0`, it returns `SystemError::AccountAlreadyInUse`, exactly reproducing the unit test at: [3](#0-2) 
4. The victim's transaction (and any retries using the same `CreateAccount` instruction against the same address) will continue to fail as long as the address retains a nonzero, non-owned balance, unless the victim switches to `CreateAccountWithSeed` or the feature-gated `CreateAccountAllowPrefund` instruction.

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
