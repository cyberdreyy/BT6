## Finding: InitializeNonceAccount sets nonce authority without requiring the nonce account to sign — front-runnable authority takeover

### Title
Unsigned `InitializeNonceAccount` allows front-running/hijacking of nonce account authority - (File: `programs/system/src/system_instruction.rs`)

### Summary
The reported Increment Protocol bug is a classic "unauthenticated one-time initializer" pattern: a function meant to be called once by the deployer to set an owner/authority field performs no signature/permission check, so anyone can race the legitimate caller and claim the authority slot. The Agave analog is `initialize_nonce_account` in `programs/system/src/system_instruction.rs`, which sets the durable-nonce `authority` field on a System-owned account without ever verifying that the account (or any other pre-existing authority) signed the transaction.

### Finding Description
`initialize_nonce_account` only checks that the target account is writable and that its state is `State::Uninitialized`; it never calls anything like `check_signer` on the account before writing the caller-supplied `nonce_authority` into the account's state: [1](#0-0) 

Contrast this with `withdraw_nonce_account` and `authorize_nonce_account` in the same file, both of which explicitly require a signature (`check_signer`) tied to the account or its existing `data.authority` before mutating state: [2](#0-1) [3](#0-2) 

The dispatcher in `system_processor.rs` confirms no additional signer enforcement is applied for `InitializeNonceAccount` beyond what `initialize_nonce_account` itself checks: [4](#0-3) 

Because `nonce_authority` is an arbitrary `Pubkey` argument taken directly from instruction data (not derived from a signer), any writable, system-owned, correctly-sized, rent-exempt, `Uninitialized` account can be claimed by whoever's `InitializeNonceAccount` instruction lands first — exactly the "assigns caller-controlled address to an owner/authority field, callable by anyone, meant to run once" pattern described in the external report.

### Impact Explanation
Once an attacker's `InitializeNonceAccount` transaction lands first, `data.authority` is set to the attacker's own pubkey. The attacker can subsequently call `WithdrawNonceAccount` and pass the full account balance as `lamports`; since the state is `Initialized` with attacker-controlled `authority`, `withdraw_nonce_account`'s `check_signer(&data.authority)` succeeds for the attacker, allowing them to drain the entire nonce account balance: [5](#0-4) 

This is a concrete unsigned fund-movement primitive: the legitimate owner never authorized the attacker, yet the attacker becomes able to move/drain lamports from an account they do not control, purely by winning a race on `InitializeNonceAccount`.

### Likelihood Explanation
This requires the target account to be created (funded, sized for `nonce::state::Data`, owned by the system program) but not yet initialized as a nonce account in the same atomic transaction as its creation. In the common CLI/SDK flow, `CreateAccount` and `InitializeNonceAccount` are typically bundled into a single transaction, closing the race window. However, nothing in the protocol enforces this atomicity — any caller who splits account creation and nonce initialization into separate transactions (or any tooling/integration that does so) is exposed, and a mempool-watching attacker can front-run the second transaction with their own `InitializeNonceAccount` instruction referencing the same account, at higher priority fee. This mirrors the report's exploit scenario precisely (attacker monitors mempool, races the legitimate initialize call).

### Recommendation
Require that `initialize_nonce_account` (and the `SystemInstruction::InitializeNonceAccount` handler) verify that the nonce account itself is a signer of the transaction, consistent with how `advance_nonce_account`/`withdraw_nonce_account`/`authorize_nonce_account` already require an authorized signer. This closes the front-runnable window by binding initialization to proof of key ownership rather than to transaction ordering alone. Document, as the report's long-term recommendation suggests, all account-initialization instructions across the codebase (stake, vote, nonce) that could be susceptible to this "callable-once-by-anyone" front-running pattern.

### Proof of Concept
1. Alice creates account `N` via `SystemInstruction::CreateAccount` (owner = system program, space = `nonce::state::State::size()`, lamports ≥ rent-exempt minimum), in a transaction separate from initialization.
2. Alice broadcasts a second transaction containing `SystemInstruction::InitializeNonceAccount(alice_authority)` referencing `N`.
3. Eve observes the mempool (or otherwise learns `N`'s address after step 1 lands) and submits her own `SystemInstruction::InitializeNonceAccount(eve_pubkey)` referencing `N`, with `N` marked non-signer (Eve does not hold `N`'s key) and a higher priority fee.
4. Because `initialize_nonce_account` (`programs/system/src/system_instruction.rs:163-211`) never checks that `N` is a signer, Eve's transaction succeeds first, setting `data.authority = eve_pubkey`.
5. Alice's later `InitializeNonceAccount` transaction now fails (`State::Initialized(_)` branch returns `InstructionError::InvalidAccountData`), and `N` is permanently under Eve's control.
6. Eve calls `SystemInstruction::WithdrawNonceAccount(full_balance)` with herself as signer for the authority field; `withdraw_nonce_account`'s `check_signer(&data.authority)` (`programs/system/src/system_instruction.rs:136`) passes, and Eve drains all lamports from `N`.

### Citations

**File:** programs/system/src/system_instruction.rs (L99-151)
```rust
    let check_signer = |signer: &Pubkey| {
        if !signers.contains(signer) {
            ic_msg!(
                invoke_context,
                "Withdraw nonce account: Account {} must sign",
                signer
            );
            return Err(InstructionError::MissingRequiredSignature);
        }
        Ok(())
    };

    let state: Versions = from.get_state()?;
    match state.state() {
        State::Uninitialized => {
            if lamports > from.get_lamports() {
                ic_msg!(
                    invoke_context,
                    "Withdraw nonce account: insufficient lamports {}, need {}",
                    from.get_lamports(),
                    lamports,
                );
                return Err(InstructionError::InsufficientFunds);
            }
            check_signer(from.get_key())?;
        }
        State::Initialized(data) => {
            if lamports == from.get_lamports() {
                let durable_nonce =
                    DurableNonce::from_blockhash(&invoke_context.environment_config.blockhash);
                if data.durable_nonce == durable_nonce {
                    ic_msg!(
                        invoke_context,
                        "Withdraw nonce account: nonce can only advance once per slot"
                    );
                    return Err(SystemError::NonceBlockhashNotExpired.into());
                }
                check_signer(&data.authority)?;
                from.set_state(&Versions::new(State::Uninitialized))?;
            } else {
                let min_balance = rent.minimum_balance(from.get_data().len());
                let amount = checked_add(lamports, min_balance)?;
                if amount > from.get_lamports() {
                    ic_msg!(
                        invoke_context,
                        "Withdraw nonce account: insufficient lamports {}, need {}",
                        from.get_lamports(),
                        amount,
                    );
                    return Err(InstructionError::InsufficientFunds);
                }
                check_signer(&data.authority)?;
            }
```

**File:** programs/system/src/system_instruction.rs (L163-211)
```rust
pub(crate) fn initialize_nonce_account(
    account: &mut BorrowedInstructionAccount,
    nonce_authority: &Pubkey,
    rent: &Rent,
    invoke_context: &InvokeContext,
) -> Result<(), InstructionError> {
    if !account.is_writable() {
        ic_msg!(
            invoke_context,
            "Initialize nonce account: Account {} must be writeable",
            account.get_key()
        );
        return Err(InstructionError::InvalidArgument);
    }

    match account.get_state::<Versions>()?.state() {
        State::Uninitialized => {
            let min_balance = rent.minimum_balance(account.get_data().len());
            if account.get_lamports() < min_balance {
                ic_msg!(
                    invoke_context,
                    "Initialize nonce account: insufficient lamports {}, need {}",
                    account.get_lamports(),
                    min_balance
                );
                return Err(InstructionError::InsufficientFunds);
            }
            let durable_nonce =
                DurableNonce::from_blockhash(&invoke_context.environment_config.blockhash);
            let data = nonce::state::Data::new(
                *nonce_authority,
                durable_nonce,
                invoke_context
                    .environment_config
                    .blockhash_lamports_per_signature,
            );
            let state = State::Initialized(data);
            account.set_state(&Versions::new(state))
        }
        State::Initialized(_) => {
            ic_msg!(
                invoke_context,
                "Initialize nonce account: Account {} state is invalid",
                account.get_key()
            );
            Err(InstructionError::InvalidAccountData)
        }
    }
}
```

**File:** programs/system/src/system_instruction.rs (L213-249)
```rust
pub(crate) fn authorize_nonce_account(
    account: &mut BorrowedInstructionAccount,
    nonce_authority: &Pubkey,
    signers: &HashSet<Pubkey>,
    invoke_context: &InvokeContext,
) -> Result<(), InstructionError> {
    if !account.is_writable() {
        ic_msg!(
            invoke_context,
            "Authorize nonce account: Account {} must be writeable",
            account.get_key()
        );
        return Err(InstructionError::InvalidArgument);
    }
    match account
        .get_state::<Versions>()?
        .authorize(signers, *nonce_authority)
    {
        Ok(versions) => account.set_state(&versions),
        Err(AuthorizeNonceError::Uninitialized) => {
            ic_msg!(
                invoke_context,
                "Authorize nonce account: Account {} state is invalid",
                account.get_key()
            );
            Err(InstructionError::InvalidAccountData)
        }
        Err(AuthorizeNonceError::MissingRequiredSignature(account_authority)) => {
            ic_msg!(
                invoke_context,
                "Authorize nonce account: Account {} must sign",
                account_authority
            );
            Err(InstructionError::MissingRequiredSignature)
        }
    }
}
```

**File:** programs/system/src/system_processor.rs (L448-467)
```rust
        SystemInstruction::InitializeNonceAccount(authorized) => {
            instruction_context.check_number_of_instruction_accounts(1)?;
            let mut me = instruction_context.try_borrow_instruction_account(0)?;
            #[allow(deprecated)]
            let recent_blockhashes = get_sysvar_with_account_check::recent_blockhashes(
                invoke_context,
                &instruction_context,
                1,
            )?;
            if recent_blockhashes.is_empty() {
                ic_msg!(
                    invoke_context,
                    "Initialize nonce account: recent blockhash list is empty",
                );
                return Err(SystemError::NonceNoRecentBlockhashes.into());
            }
            let rent =
                get_sysvar_with_account_check::rent(invoke_context, &instruction_context, 2)?;
            initialize_nonce_account(&mut me, &authorized, &rent, invoke_context)
        }
```
