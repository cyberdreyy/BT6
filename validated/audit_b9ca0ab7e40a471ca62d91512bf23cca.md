### Title
Frontrunning `InitializeBuffer` on the upgradeable BPF loader allows hijacking a buffer account's authority before deployment - (File: `programs/bpf_loader/src/lib.rs`)

### Summary
The `UpgradeableLoaderInstruction::InitializeBuffer` handler in the upgradeable BPF loader sets a buffer account's `authority_address` from an arbitrary caller-supplied account with no signer requirement on either the buffer account or the authority account, other than the buffer being `Uninitialized`. This mirrors the reported `PublicLock.initialize()` issue: whoever successfully calls the initializer first, wins, regardless of who actually intends to own the account.

### Finding Description
`process_loader_upgradeable_instruction` handles `InitializeBuffer` as follows: it only checks that the buffer account's state is `UpgradeableLoaderState::Uninitialized`, then unconditionally sets the authority to whatever pubkey is passed as instruction account index 1 — with no signer check on the buffer account (index 0) and no signer check on the authority account (index 1). [1](#0-0) 

Buffer accounts are created via `system_instruction::create_account` (which requires the buffer keypair to sign account creation and sets its owner to `bpf_loader_upgradeable`), and the intended flow bundles account creation with `InitializeBuffer` in a single message via `solana_loader_v3_interface::instruction::create_buffer`, exercised in the codebase's own tests and CLI/runtime helpers. [2](#0-1) [3](#0-2) 

Because the two steps (create the buffer account, then call `InitializeBuffer`) are independent instructions rather than being cryptographically tied together, any account creation that is not submitted atomically with its `InitializeBuffer` call in the same transaction is exposed: once the buffer account exists on-chain with `Uninitialized` state and is owned by `bpf_loader_upgradeable`, anyone can submit their own `InitializeBuffer` instruction referencing that buffer pubkey and any authority pubkey they choose (including their own), since there is no ownership/creator check tying the caller to the buffer's creation. Whoever's `InitializeBuffer` transaction lands first sets the authority for that buffer.

### Impact Explanation
If a deployer's buffer-account creation and `InitializeBuffer` call are not atomically bundled (e.g., split across transactions due to tooling, retries, or transaction-size constraints), an attacker observing the buffer account's creation can race to call `InitializeBuffer` first with their own authority key. This locks the deployer out of writing to (or later using) the buffer they paid rent to create via `AccountAlreadyInitialized` on their own subsequent `InitializeBuffer` attempt, forcing them to abandon the account and pay to create a new one — the same "griefing/forced redeploy" impact class described in the referenced report. This is a state-mutation/griefing issue reachable by an ordinary attacker from a standard transaction, not a consensus-breaking bug.

### Likelihood Explanation
Exploitability requires only that an attacker observe a pending or just-landed `create_account` instruction targeting a soon-to-be buffer account (owned by `bpf_loader_upgradeable`, state `Uninitialized`) before the legitimate `InitializeBuffer` call for that same account is confirmed, and then submit a competing `InitializeBuffer` instruction. Since the standard `create_buffer` helper bundles both instructions atomically in one transaction/message, exploitation is limited to callers or tooling that split the two steps across separate transactions. [4](#0-3) 

### Recommendation
Require a signer check on the buffer account itself (or otherwise cryptographically bind account creation to initialization, e.g. via a PDA derivation tied to the intended authority) in the `InitializeBuffer` handler, so that only the party who created/controls the buffer account can determine its authority, closing the same-style front-running window present in the original `PublicLock.initialize()` finding.

### Proof of Concept
1. Deployer submits `system_instruction::create_account` to create buffer account `B` with owner `bpf_loader_upgradeable`, in a transaction separate from `InitializeBuffer` (e.g., due to a two-step CLI flow or bundling failure).
2. Attacker observes `B` is created (`Uninitialized`, owned by loader) before the deployer's `InitializeBuffer` transaction confirms.
3. Attacker submits `InitializeBuffer` with account 0 = `B`, account 1 = attacker's own pubkey. Since no signer check exists on either account in the handler at `programs/bpf_loader/src/lib.rs:158-172`, this succeeds and sets `authority_address = attacker`.
4. Deployer's original `InitializeBuffer` transaction now fails with `AccountAlreadyInitialized`, and the deployer cannot write to or use `B` for deployment, having wasted the rent-exempt lamports funding it.

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

**File:** program-test/tests/builtins.rs (L24-31)
```rust
    let create_buffer_instructions = solana_loader_v3_interface::instruction::create_buffer(
        &payer.pubkey(),
        &buffer_keypair.pubkey(),
        &upgrade_authority_keypair.pubkey(),
        buffer_rent,
        1,
    )
    .unwrap();
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

**File:** transaction-status/src/parse_bpf_loader.rs (L326-333)
```rust
        let instructions = bpf_loader_upgradeable::create_buffer(
            &payer_address,
            &buffer_address,
            &authority_address,
            55,
            max_data_len,
        )
        .unwrap();
```
