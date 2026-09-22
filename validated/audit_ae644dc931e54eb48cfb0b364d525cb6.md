### Title
`InitializeBuffer` in bpf_loader_upgradeable performs no signer check, allowing frontrunning to hijack buffer authority - (File: `programs/bpf_loader/src/lib.rs`)

### Summary
The `UpgradeableLoaderInstruction::InitializeBuffer` handler in `process_loader_upgradeable_instruction` sets `authority_address` on an uninitialized buffer account without requiring any signature from the buffer account, its future authority, or any other privileged party.

### Finding Description
In `process_loader_upgradeable_instruction`, the `InitializeBuffer` arm only checks that the account is currently `UpgradeableLoaderState::Uninitialized`, then unconditionally sets the authority to whatever pubkey is passed as instruction account index 1 — with no `is_instruction_account_signer` check on either account 0 (the buffer) or account 1 (the claimed authority): [1](#0-0) 

This is structurally identical to the reported `RubiconFeeController.initialize()` bug: an "init-once" function guarded only by a `state == Uninitialized` check, with no ownership/signature gate, meaning whoever's transaction lands first wins the initialization race.

A buffer account must first be created and assigned to `bpf_loader_upgradeable` (typically via `SystemInstruction::CreateAccount`) before `InitializeBuffer` can be called on it. If account creation and buffer initialization are ever submitted as separate transactions (rather than atomically bundled, as the CLI's `create_buffer` helper happens to do today via `loader_v3_instruction::create_buffer` combining both instructions in one message, per `cli/src/program.rs`'s `do_process_program_deploy`/`do_process_write_buffer` logic), there exists a window where the buffer account exists, is owned by `bpf_loader_upgradeable`, and is `Uninitialized`, but not yet initialized. Any unprivileged transaction sender who observes this account (e.g., via mempool/gossip or by polling account state) can submit `InitializeBuffer` with themselves as the `authority_address` before the legitimate initializer's transaction lands, since no signature is required from anyone.

### Impact Explanation
Once an attacker wins this race, they become the sole `authority_address` on the buffer account, which the real depositor has already funded with rent-exempt lamports. Because the buffer account's authority controls `Write`, `SetAuthority`, `SetAuthorityChecked`, and `Close` (which sends the account's lamports to a recipient the authority signs off on) — all of which require only the authority's signature and no other check — the attacker can drain the buffer account's lamports via `Close`, or inject malicious bytecode via `Write` and later have it deployed if the victim proceeds trusting the buffer. This is analogous to the reported issue's "adversary can set feeRecipient to steal funds": here the adversary can set themselves as buffer authority to steal the rent-exempt lamports funding the account or to poison program bytecode prior to deployment.

### Likelihood Explanation
Exploitability strictly depends on account creation and `InitializeBuffer` being observable/executable as separate transactions rather than atomically bundled. The Agave CLI's default program-deploy/write-buffer flows bundle `create_buffer` (CreateAccount + InitializeBuffer) into a single message signed together by the buffer keypair, closing this window for that specific code path — verified in `cli/src/program.rs` (`do_process_program_deploy`, `do_process_write_buffer`) and `cli/tests/program.rs`/`program-test/tests/builtins.rs`, all of which submit `create_buffer`'s instructions as one atomic transaction. However, the underlying program instruction handler itself, `programs/bpf_loader/src/lib.rs`, enforces no such atomicity or signer requirement — any other tooling, custom client, or manual instruction composition that creates the buffer account and calls `InitializeBuffer` in separate transactions is exposed to this race. I was unable to fully verify within this session whether any other in-repo caller (besides the CLI) ever splits these two instructions across transactions, so the likelihood should be treated as tooling-dependent rather than proven to be exploitable against the reference CLI itself.

### Recommendation
Require `account 1` (the intended `authority_address`) to be a signer of the `InitializeBuffer` instruction, mirroring the signer checks already present in `Write`, `SetAuthority`, and `SetAuthorityChecked` in the same handler [2](#0-1) . This would prevent an unprivileged third party from claiming buffer authority over an account it does not control, eliminating the frontrunning window regardless of whether account creation and initialization are bundled atomically.

### Proof of Concept
1. Victim submits `SystemInstruction::CreateAccount` creating account `B` with owner `bpf_loader_upgradeable::id()`, funded with rent-exempt lamports, intending to follow up with `InitializeBuffer(authority=victim)`.
2. Before the victim's `InitializeBuffer` transaction lands, an attacker observes account `B` in the mempool/gossip (owned by the loader, state `Uninitialized`) and submits their own `InitializeBuffer` instruction referencing account `B` with `account 1 = attacker_pubkey`, using a higher priority fee.
3. Per `process_loader_upgradeable_instruction`'s `InitializeBuffer` arm [1](#0-0) , this succeeds with no signature check, setting `authority_address = Some(attacker_pubkey)`.
4. The victim's original `InitializeBuffer` transaction now fails with `InstructionError::AccountAlreadyInitialized` (confirmed by the existing test `test_bpf_loader_upgradeable_initialize_buffer` in `programs/bpf_loader/src/lib.rs` lines 1450-1460, which shows the second `InitializeBuffer` call on an already-initialized buffer fails).
5. The attacker, now the sole authority, submits `UpgradeableLoaderInstruction::Close` (or `Write`/`SetAuthority`) against account `B`, taking control of/draining the victim-funded buffer account.

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

**File:** programs/bpf_loader/src/lib.rs (L182-190)
```rust
                let authority_key = Some(*instruction_context.get_key_of_instruction_account(1)?);
                if authority_address != authority_key {
                    ic_logger_msg!(log_collector, "Incorrect buffer authority provided");
                    return Err(InstructionError::IncorrectAuthority);
                }
                if !instruction_context.is_instruction_account_signer(1)? {
                    ic_logger_msg!(log_collector, "Buffer authority did not sign");
                    return Err(InstructionError::MissingRequiredSignature);
                }
```
