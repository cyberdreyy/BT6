### Title
Unauthenticated `InitializeNonceAccount` instruction allows front-running to seize nonce account authority - (File: `programs/system/src/system_instruction.rs`)

### Summary
The System Program's `InitializeNonceAccount` instruction transitions a nonce account from `State::Uninitialized` to `State::Initialized` and sets the nonce authority to whatever pubkey is supplied in the instruction data. The handler performs no check that the caller is the account's creator, holds any relationship to the account, or has signed with the account's own key — it only checks that the target account is writable and rent-exempt. Because account creation (`CreateAccount`, which does require the new account to sign) and initialization (`InitializeNonceAccount`, which does not) are two independent System Program instructions, a user who submits them as separate transactions exposes a front-runnable window in which any third party can initialize the account first and become its nonce authority.

### Finding Description
`initialize_nonce_account` in [1](#0-0)  only validates that the account is writable, that its state is `Uninitialized`, and that it holds enough lamports to be rent-exempt. It then unconditionally writes the caller-supplied `nonce_authority` pubkey into the account state — with no signature check tying that authority (or the transaction signer) to the account's original creator.

The dispatch site in `system_processor.rs` confirms no additional signer validation is applied before calling this function: [2](#0-1)  simply borrows the account and forwards to `initialize_nonce_account` with the sysvars.

This is architecturally different from `allocate`/`assign`, which explicitly require the target address to be a signer via `Address::is_signer` before mutating account state: [3](#0-2) . `InitializeNonceAccount` has no analogous protection, relying entirely on the convention that `CreateAccount` and `InitializeNonceAccount` are bundled atomically in one transaction (as done by the `create_nonce_account` helper). If a wallet, exchange, or dApp instead issues `CreateAccount`/`Allocate`/`Assign` in one transaction and defers `InitializeNonceAccount` to a later transaction (e.g., due to multi-step UX, retries, or fee-payer separation), the freshly created, rent-exempt, system-owned, uninitialized account sits in a state where anyone observing the mempool/leader schedule can submit `InitializeNonceAccount(attacker_pubkey)` targeting that exact address before the legitimate initialize transaction lands.

The vote program exhibits a related but narrower issue: `initialize_account` in [4](#0-3)  only verifies that `vote_init.node_pubkey` is present in the transaction's signer set — it does not bind that pubkey to anything determined at account-creation time, so an attacker who wins the race can simply supply their own keys as `node_pubkey`, `authorized_voter`, and `authorized_withdrawer`.

### Impact Explanation
An attacker who front-runs `InitializeNonceAccount` becomes the durable-nonce authority for a rent-exempt account funded by the victim. As authority, they can call `AuthorizeNonceAccount`, `AdvanceNonceAccount`, and — critically — `WithdrawNonceAccount` to drain the account's lamports once the nonce state is later reset to uninitialized (see `withdraw_nonce_account` at [5](#0-4) , which permits full withdrawal by the authority, zeroing the account and reclaiming rent). This is a concrete unauthorized state-mutation and fund-theft primitive reachable from an ordinary user transaction against a builtin program.

### Likelihood Explanation
Exploitation requires that account creation and initialization be split across transactions and that an attacker observe and race the intervening window — this does not happen with the standard `system_instruction::create_nonce_account` helper (which bundles both instructions atomically), so likelihood depends on client/integration behavior rather than protocol-level guarantee. It is nonetheless a real gap: the protocol itself provides no defense-in-depth against this front-running pattern, unlike `Allocate`/`Assign`, which do enforce a signer check on the target address.

### Recommendation
Require the nonce account itself (or a designated creator-bound signer) to sign `InitializeNonceAccount`, mirroring the `Address::is_signer` check used in `allocate`/`assign`. Alternatively, document and enforce (at the client/tooling level) that `CreateAccount` and `InitializeNonceAccount` must always be submitted atomically in the same transaction, and consider adding a protocol-level check in `initialize_nonce_account` that requires the account's own key to be a transaction signer.

### Proof of Concept
1. Victim submits transaction A: `SystemInstruction::CreateAccount` funding and assigning a new pubkey `N` to the System Program, sized/rent-exempt for nonce state, signed by `N` and the payer.
2. Before victim's transaction B (`SystemInstruction::InitializeNonceAccount(victim_authority)` targeting `N`) lands, an attacker observes `N` in a pending/confirmed state (uninitialized, system-owned, rent-exempt) and submits `SystemInstruction::InitializeNonceAccount(attacker_pubkey)` targeting `N`.
3. Per `initialize_nonce_account` ( [6](#0-5) ), since state is `Uninitialized` and lamports are sufficient, the account is set to `State::Initialized` with `authority = attacker_pubkey`, with no check that the attacker created or owns `N`.
4. Victim's transaction B now fails (`AccountAlreadyInitialized`... actually returns `InvalidAccountData` per line 202-209), and the attacker, as nonce authority, can call `AuthorizeNonceAccount`/`WithdrawNonceAccount` to control or drain the account's lamports.

### Citations

**File:** programs/system/src/system_instruction.rs (L80-161)
```rust
pub(crate) fn withdraw_nonce_account(
    from_account_index: IndexOfAccount,
    lamports: u64,
    to_account_index: IndexOfAccount,
    rent: &Rent,
    signers: &HashSet<Pubkey>,
    invoke_context: &InvokeContext,
    instruction_context: &InstructionContext,
) -> Result<(), InstructionError> {
    let mut from = instruction_context.try_borrow_instruction_account(from_account_index)?;
    if !from.is_writable() {
        ic_msg!(
            invoke_context,
            "Withdraw nonce account: Account {} must be writeable",
            from.get_key()
        );
        return Err(InstructionError::InvalidArgument);
    }

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
        }
    };

    from.checked_sub_lamports(lamports)?;
    drop(from);
    let mut to = instruction_context.try_borrow_instruction_account(to_account_index)?;
    to.checked_add_lamports(lamports)?;

    Ok(())
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

**File:** programs/system/src/system_processor.rs (L75-100)
```rust
fn allocate(
    account: &mut BorrowedInstructionAccount,
    address: &Address,
    space: u64,
    signers: &HashSet<Pubkey>,
    invoke_context: &InvokeContext,
) -> Result<(), InstructionError> {
    if !address.is_signer(signers) {
        ic_msg!(
            invoke_context,
            "Allocate: 'to' account {:?} must sign",
            address
        );
        return Err(InstructionError::MissingRequiredSignature);
    }

    // if it looks like the `to` account is already in use, bail
    //   (note that the id check is also enforced by message_processor)
    if !account.get_data().is_empty() || !system_program::check_id(account.get_owner()) {
        ic_msg!(
            invoke_context,
            "Allocate: account {:?} already in use",
            address
        );
        return Err(SystemError::AccountAlreadyInUse.into());
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

**File:** programs/vote/src/vote_state/mod.rs (L1188-1209)
```rust
/// Initialize the vote_state for a vote account
/// Assumes that the account is being init as part of a account creation or balance transfer and
/// that the transaction must be signed by the staker's keys
pub fn initialize_account<S: std::hash::BuildHasher>(
    vote_account: &mut BorrowedInstructionAccount,
    target_version: VoteStateTargetVersion,
    vote_init: &VoteInit,
    signers: &HashSet<Pubkey, S>,
    clock: &Clock,
) -> Result<(), InstructionError> {
    VoteStateHandler::check_vote_account_length(vote_account, target_version)?;
    let versioned = vote_account.get_state::<VoteStateVersions>()?;

    if !versioned.is_uninitialized() {
        return Err(InstructionError::AccountAlreadyInitialized);
    }

    // node must agree to accept this vote account
    verify_authorized_signer(&vote_init.node_pubkey, signers)?;

    VoteStateHandler::init_vote_account_state(vote_account, vote_init, clock, target_version)
}
```
