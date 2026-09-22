### Title
Missing Signer Check on `InitializeBuffer` Allows an Unprivileged Attacker to Race and Hijack a Buffer Account's Authority - (File: `programs/bpf_loader/src/lib.rs`)

### Summary
The `bpf_loader_upgradeable`'s `InitializeBuffer` instruction handler sets the authority of a program-buffer account to whatever pubkey is passed as instruction account 1, but never requires the buffer account itself (instruction account 0) to be a signer, and never checks that the caller has any prior relationship to that account. Any unprivileged transaction sender who observes a newly created, still-`Uninitialized` buffer account (owned by `bpf_loader_upgradeable` but not yet initialized) can submit their own `InitializeBuffer` instruction naming themselves as authority before the legitimate initializer's transaction lands, taking ownership of the account and any lamports funded into it.

### Finding Description
`process_loader_upgradeable_instruction` handles `UpgradeableLoaderInstruction::InitializeBuffer` as follows: [1](#0-0) 

The only checks performed are: (1) two accounts present, and (2) the buffer's current state is `Uninitialized`. There is no check that:
- instruction account 0 (the buffer) is a signer, or
- the caller has any relationship to the entity that originally created/funded the buffer account.

This means whoever wins the race to submit `InitializeBuffer` for a given (already system-created, still `Uninitialized`) `bpf_loader_upgradeable`-owned account becomes its permanent authority, recorded via `buffer.set_state(&UpgradeableLoaderState::Buffer { authority_address: authority_key })`.

The intended safe usage bundles `system_instruction::create_account` (creating the account with `owner = bpf_loader_upgradeable`) and `InitializeBuffer` into a single transaction via the client-side `create_buffer` helper, as shown in the test fixtures: [2](#0-1) [3](#0-2) 

However, this atomicity is purely a client-side convention — it is not enforced anywhere in the runtime. Nothing in `programs/bpf_loader/src/lib.rs` prevents `InitializeBuffer` from being called in a separate transaction on an account that was created in an earlier one. Once a buffer account exists on-chain in the `Uninitialized` state (which is visible to any observer, since the account's owner and state are public once the `CreateAccount` transaction lands), any other transaction sender can submit `InitializeBuffer` naming themselves as `authority_address` — exactly the access-control gap described in the external report, but manifesting in agave's builtin loader program rather than an upgradeable-proxy `initialize()`.

Once an attacker controls the `authority_address`, they can subsequently call `UpgradeableLoaderInstruction::Close`, which for a `Buffer` state moves all lamports to a recipient chosen by the authority via `common_close_account`: [4](#0-3) 

This lets the attacker drain the rent-exempt lamports that the legitimate payer funded into the buffer account.

### Impact Explanation
This results in concrete unsigned fund movement: an attacker who never held the account's lamports or authority can redirect them to themselves by hijacking the buffer's authority and then closing it. It also blocks the legitimate deployer, since the account is no longer `Uninitialized` (`AccountAlreadyInitialized` will be returned to the rightful owner's follow-up `InitializeBuffer`/`Write` calls), disrupting intended program deployment/upgrade flows. This matches the severity class of the analogous report (loss of value due to missing initialization access control), scoped to a builtin Solana program reachable by any transaction sender.

### Likelihood Explanation
Exploitability depends on the buffer-creation and initialization instructions being submitted in separate transactions rather than the atomic client helper `create_buffer`. Any tooling, wallet flow, or manual CLI usage that does not bundle `CreateAccount` and `InitializeBuffer` in one transaction leaves a race window that any unprivileged watcher of the ledger/mempool can win by submitting `InitializeBuffer` first. This does not require a malicious leader or validator — a normal unprivileged user submitting a single transaction targeting the discovered buffer pubkey suffices.

### Recommendation
Require that the buffer account (instruction account 0) be a signer on `InitializeBuffer`, ensuring only the entity that controls (and, in the standard flow, just created) the account can set its authority. Alternatively/additionally, always require creation and initialization of the buffer account to occur atomically within a single instruction (e.g., merge `CreateAccount` + `InitializeBuffer` semantics at the runtime level, or enforce a compatible check similar to the signer/authority checks already present on `Write`, `SetAuthority`, and `Close`).

### Proof of Concept
1. Victim submits transaction A: `system_instruction::create_account(payer, buffer_pubkey, lamports, size, owner = bpf_loader_upgradeable)` — creating an `Uninitialized` buffer account.
2. Before victim's transaction B (`InitializeBuffer` naming `victim_authority`) lands, attacker submits transaction C containing only:
   `UpgradeableLoaderInstruction::InitializeBuffer` with:
   - account 0 = `buffer_pubkey` (writable, `is_signer = false`)
   - account 1 = `attacker_pubkey` (not required to sign, per `programs/bpf_loader/src/lib.rs:167`)
3. Per `programs/bpf_loader/src/lib.rs:158-172`, since state is still `Uninitialized`, the instruction succeeds and sets `authority_address = Some(attacker_pubkey)`.
4. Victim's transaction B now fails with `AccountAlreadyInitialized`.
5. Attacker submits `UpgradeableLoaderInstruction::Close` with themselves as authority and their own account as recipient, draining `buffer_pubkey`'s lamports via `common_close_account` (`programs/bpf_loader/src/lib.rs:1003-1028`).

### Citations

**File:** programs/bpf_loader/src/lib.rs (L158-172)
```rust
        UpgradeableLoaderInstruction::InitializeBuffer => {
            instruction_context.check_number_of_instruction_accounts(2)?;
            let mut buffer = instruction_context.try_borrow_instruction_account(0)?;

            if UpgradeableLoaderState::Uninitialized != buffer.get_state()? {
                ic_logger_msg!(log_collector, "Buffer account already initialized");
                return Err(InstructionError::AccountAlreadyInitialized);
            }

            let authority_key = Some(*instruction_context.get_key_of_instruction_account(1)?);

            buffer.set_state(&UpgradeableLoaderState::Buffer {
                authority_address: authority_key,
            })?;
        }
```

**File:** programs/bpf_loader/src/lib.rs (L1003-1028)
```rust
fn common_close_account(
    authority_address: &Option<Pubkey>,
    instruction_context: &InstructionContext,
    log_collector: &Option<Rc<RefCell<LogCollector>>>,
) -> Result<(), InstructionError> {
    if authority_address.is_none() {
        ic_logger_msg!(log_collector, "Account is immutable");
        return Err(InstructionError::Immutable);
    }
    if *authority_address != Some(*instruction_context.get_key_of_instruction_account(2)?) {
        ic_logger_msg!(log_collector, "Incorrect authority provided");
        return Err(InstructionError::IncorrectAuthority);
    }
    if !instruction_context.is_instruction_account_signer(2)? {
        ic_logger_msg!(log_collector, "Authority did not sign");
        return Err(InstructionError::MissingRequiredSignature);
    }

    let mut close_account = instruction_context.try_borrow_instruction_account(0)?;
    let mut recipient_account = instruction_context.try_borrow_instruction_account(1)?;

    recipient_account.checked_add_lamports(close_account.get_lamports())?;
    close_account.set_lamports(0)?;
    close_account.set_state(&UpgradeableLoaderState::Uninitialized)?;
    Ok(())
}
```

**File:** program-test/tests/builtins.rs (L24-35)
```rust
    let create_buffer_instructions = solana_loader_v3_interface::instruction::create_buffer(
        &payer.pubkey(),
        &buffer_keypair.pubkey(),
        &upgrade_authority_keypair.pubkey(),
        buffer_rent,
        1,
    )
    .unwrap();

    let mut transaction =
        Transaction::new_with_payer(&create_buffer_instructions[..], Some(&payer.pubkey()));
    transaction.sign(&[&payer, &buffer_keypair], recent_blockhash);
```

**File:** runtime/src/loader_utils.rs (L88-107)
```rust
    bank_client
        .send_and_confirm_message(
            &[from_keypair, buffer_keypair],
            Message::new(
                &solana_loader_v3_interface::instruction::create_buffer(
                    &from_keypair.pubkey(),
                    &buffer_pubkey,
                    &buffer_authority_pubkey,
                    1.max(
                        bank_client
                            .get_minimum_balance_for_rent_exemption(program_buffer_bytes)
                            .unwrap(),
                    ),
                    program.len(),
                )
                .unwrap(),
                Some(&from_keypair.pubkey()),
            ),
        )
        .unwrap();
```
