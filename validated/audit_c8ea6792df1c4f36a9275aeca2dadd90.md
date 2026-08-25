This maps well to a concrete analog. Let me verify the exact signer-check absence in `initialize_nonce_account` and confirm how it's invoked in `system_processor.rs`.### Title
Unauthorized/Front-Runnable `InitializeNonceAccount` Due to Missing Signer Check - (File: `programs/system/src/system_instruction.rs`)

### Summary
The System Program's `InitializeNonceAccount` instruction, implemented by `initialize_nonce_account()`, sets the nonce account's `authority` field to an arbitrary caller-supplied pubkey without requiring any signature from the account being initialized or its intended owner. This mirrors the `CsFeeOracle.initialize()` bug class: an externally reachable "set-up" entry point that establishes privileged/administrative state (an authority) with no access control, making it front-runnable by any party who observes the target account address.

### Finding Description
`initialize_nonce_account()` only validates that the account is writable, in `State::Uninitialized`, and rent-exempt, before writing the caller-supplied `nonce_authority` into the account's `Data`: [1](#0-0) . Unlike its sibling functions `withdraw_nonce_account` and `authorize_nonce_account`, which explicitly call a `check_signer` closure against `signers` before mutating authority-sensitive state [2](#0-1) [3](#0-2) , `initialize_nonce_account` performs no such check at all.

The dispatch path in `system_processor.rs` confirms this: `SystemInstruction::InitializeNonceAccount(authorized)` only checks the number of accounts, fetches the recent-blockhashes and rent sysvars, and calls `initialize_nonce_account(&mut me, &authorized, &rent, invoke_context)` — with no `is_instruction_account_signer` check on account index 0 (the nonce account) anywhere in this branch [4](#0-3) .

Because a nonce account's pubkey is public once it is created and funded (e.g., via a prior `CreateAccount` transaction), and the `InitializeNonceAccount` instruction can be submitted by *any* fee payer targeting that pubkey with an arbitrary `authorized` argument, an attacker who observes an uninitialized, rent-exempt, system-owned account intended to become a nonce account can front-run the legitimate initialization transaction and set themselves (or any other key) as the nonce authority first.

### Impact Explanation
If an attacker front-runs initialization, they gain the `authority` role on the target nonce account. Subsequent `AdvanceNonceAccount`, `AuthorizeNonceAccount`, and (for balances above the rent-exempt minimum) `WithdrawNonceAccount` operations all gate on `data.authority` matching a signer [5](#0-4) [3](#0-2) . This locks the legitimate creator out of using the nonce account as a durable nonce (denial of state/service), and lets the attacker control/withdraw any lamports above the rent-exempt minimum that get deposited later, since only the recorded authority (attacker) can authorize withdrawals once initialized. This is a state-mutation/authority-hijack impact analogous to the referenced report's unauthorized `initialize()` call.

### Likelihood Explanation
Exploitation requires only that account creation (`CreateAccount`, which is separately signed by the new account's key) and `InitializeNonceAccount` occur as two non-atomic operations (e.g., separate transactions), which is possible for any caller who constructs the transaction manually rather than using the atomic helper that bundles both instructions. Any attacker monitoring the mempool/ledger for freshly created, uninitialized, system-owned, rent-exempt accounts intended for nonce use can race an `InitializeNonceAccount` instruction referencing that pubkey with no signature requirement of their own on that account.

### Recommendation
Require that the nonce account itself (or its designated creator/authority) be a signer of the `InitializeNonceAccount` instruction, consistent with the signer checks already present in `withdraw_nonce_account` and `authorize_nonce_account`. At minimum, add a `check_signer(account.get_key())` (or equivalent) call in `initialize_nonce_account()`/the `system_processor.rs` dispatch arm before writing the caller-supplied authority.

### Proof of Concept
1. Party A creates and funds a rent-exempt system-owned account `N` intended to become a nonce account, via `SystemInstruction::CreateAccount`, in one transaction (signed by `N`'s keypair to authorize account creation).
2. Before Party A submits a follow-up transaction containing `SystemInstruction::InitializeNonceAccount(authorized = A)` referencing account `N`, an attacker B observes `N`'s address (public) and submits their own transaction with `SystemInstruction::InitializeNonceAccount(authorized = B)` targeting the same account `N`.
3. `system_processor.rs`'s `InitializeNonceAccount` arm and `initialize_nonce_account()` accept B's transaction because they check only account count, writability, rent-exemption, and `State::Uninitialized` — never a signature from `N` or `A` [1](#0-0) [4](#0-3) .
4. Account `N`'s state becomes `State::Initialized(Data { authority: B, .. })`. Party A's subsequent legitimate `InitializeNonceAccount` transaction now fails with `InstructionError::InvalidAccountData` (already initialized) [6](#0-5) , and A can no longer advance, authorize, or (above rent-exempt minimum) withdraw from `N` without B's cooperation.

### Citations

**File:** programs/system/src/system_instruction.rs (L99-123)
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
```

**File:** programs/system/src/system_instruction.rs (L125-150)
```rust
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
```

**File:** programs/system/src/system_instruction.rs (L163-200)
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
```

**File:** programs/system/src/system_instruction.rs (L202-209)
```rust
        State::Initialized(_) => {
            ic_msg!(
                invoke_context,
                "Initialize nonce account: Account {} state is invalid",
                account.get_key()
            );
            Err(InstructionError::InvalidAccountData)
        }
```

**File:** programs/system/src/system_instruction.rs (L213-231)
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
