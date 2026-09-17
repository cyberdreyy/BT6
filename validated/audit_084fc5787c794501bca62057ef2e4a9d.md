Now I found a strong analog. The EIP-8130 counterfactual `Create` account uses a deterministic CREATE2-style address derived from `(user_salt, code, initial_actors)`, not from `msg.sender`. This is exactly the "receiver-address-not-caller-controlled" pattern that makes the Intuition triple-deposit griefing possible: an attacker who observes a pending create transaction in the mempool can extract `(user_salt, code, initial_actors)` from the public calldata, and since `AccountChangeApplier::compute_address` is a pure function of these fields (independent of the submitter's identity), the attacker can front-run with the identical entry to seize the address first — either via their own EIP-8130 create using the same public parameters, or by any means that occupies that address with code/nonce. The victim's transaction then hits `AlreadyInitialized`/`already has code` and reverts, permanently denying that specific account creation, mirroring the "front-run the counter vault" DoS in the original report.### Title
Front-running a counterfactual EIP-8130 `Create` transaction permanently DoSes that specific account activation - ([File: crates/execution/eip8130/src/apply.rs])

### Summary
An EIP-8130 counterfactual account is created at a CREATE2-style address that is a pure, public function of `(user_salt, code, initial_actors)` — fields fully visible in the pending transaction's calldata — rather than a function of who submits the transaction. An unprivileged actor who observes a pending `Create` transaction in the mempool can copy this exact tuple into their own transaction and get it included first, permanently occupying the target account before the original transaction lands. The original transaction then reverts (or is rejected pre-inclusion), analogous to the reported Intuition `depositTriple()` bug where an attacker griefs a specific victim-chosen vault state before the victim's own transaction executes.

### Finding Description
`AccountChangeApplier::compute_address(user_salt, code, initial_actors)` derives the counterfactual account's address deterministically from data the sender does not need to keep secret — it is carried in-clear inside the `CreateEntry` of the pending `TxEip8130`. [1](#0-0) 

`apply_create` guards against re-initialization by checking `state.is_initialized()` and returning `ApplyError::AlreadyInitialized { account }` if the derived address already holds EIP-8130 state: [2](#0-1) 

At block inclusion, `install_created_code` enforces that the destination has no code and a zero nonce before installing the created account's runtime, explicitly to stop "an inclusion path that bypasses the pool" from "overwrit[ing] preexisting third-party code": [3](#0-2) 

The txpool additionally runs `validate_eip8130_create_freshness`, rejecting admission of a create transaction whose derived sender already has a non-zero nonce or existing code: [4](#0-3) 

Because the derived address depends only on `(user_salt, code, initial_actors)` — not on any secret held by the original submitter — anyone who sees the pending transaction (via mempool visibility) can extract that tuple and resubmit an identical `Create` entry with a higher fee. Whichever transaction lands first wins the address; the other party's create is rejected at admission (`already has code` / non-zero nonce) or reverts at inclusion (`AlreadyInitialized` / "create destination already has code or a non-zero nonce"). This mirrors the structural root cause of the reported bug: a state-mutating operation gated on a deterministically-addressed, publicly-derivable target (the Intuition triple's counter-vault keyed by `receiver`; here, the CREATE2-style account keyed by `(user_salt, code, initial_actors)`) can be raced by a third party who has no special privilege over that target.

### Impact Explanation
This lets an unprivileged actor deny inclusion of a specific counterfactual account's activation transaction at will and at minimal cost (one transaction with a bumped fee), for as long as they choose to monitor the mempool and repeat the race. Because the guard conditions (`AlreadyInitialized`, code/nonce occupancy) are permanent once tripped for a given `(salt, code, actors)` triple, the targeted submission is unconditionally and repeatably blocked — a persistent, attacker-controlled denial of service against a specific victim-chosen account activation, without requiring any special permission, key, or role. This falls within the "unauthorized operation ... permanent freezing" class insofar as the intended account never activates while griefed, though the account's ownership is not stolen (the attacker cannot forge the sender's signature to redirect `initial_actors`, since `apply_create`'s `CreateAddressMismatch`/signature checks bind the entry to the signer).

### Likelihood Explanation
High for the specific mechanic (front-running a visible pending transaction with a public, unauthenticated tuple is trivial for anyone with mempool visibility), though the practical value to an attacker is limited to griefing rather than fund theft since the copied `initial_actors` still name the original owner. The bug class matches the report's "Medium" severity assignment (targeted DoS via front-run of a deterministically-addressed operation), not a Critical/High fund-theft primitive.

### Recommendation
Bind the counterfactual create's address (or at least its admission/inclusion eligibility) to a value not fully reconstructable by a third party from public calldata alone — e.g., incorporate the signer's authenticated public key/address into the address derivation (as EIP-4337-style factories typically do via `msg.sender`/factory address), or require that the entity submitting the winning transaction match an actor authorized in `initial_actors`, so a copy submitted by an unrelated party is rejected regardless of ordering.

### Proof of Concept
1. Alice signs and broadcasts `TxEip8130` `T_A` containing a `Create` entry with `(user_salt = S, code = C, initial_actors = [Alice's key])`, deriving account address `D = compute_address(S, C, [Alice])`.
2. Attacker Mallory observes `T_A` in the public mempool and extracts `(S, C, [Alice])` (all present in cleartext calldata).
3. Mallory crafts her own `TxEip8130` `T_M` with an identical `Create` entry `(S, C, [Alice])` and a higher gas price, and submits it.
4. `T_M` is included first; `apply_create` succeeds, marking `D` initialized: [5](#0-4) .
5. `T_A` is now rejected at pool admission (`validate_eip8130_create_freshness` — "create sender already has code") or reverts at inclusion (`install_created_code` — "create destination already has code or a non-zero nonce") [6](#0-5) , permanently denying Alice's own submission for that `(S, C, [Alice])` triple, at Mallory's sole cost of one transaction's gas.

### Citations

**File:** crates/execution/eip8130/src/apply.rs (L841-872)
```rust
    pub fn apply_create(
        storage: &mut AccountConfigurationStorage<'_>,
        entry: &CreateEntry,
    ) -> Result<CreatedAccount, ApplyError> {
        // Validate the runtime before deriving the address so every caller (pool
        // admission and block inclusion) rejects the same set of malformed
        // runtimes at the shared choke point, matching the reference contract's
        // CREATE2 deploy (EIP-170 size, EIP-3541 leading-byte).
        Self::validate_create_runtime(&entry.code)?;
        let address = Self::compute_address(entry.user_salt, &entry.code, &entry.initial_actors)?;
        // Block re-initialization of an account that already holds EIP-8130 state.
        // `local_sequence` doubles as the created/imported flag; `local_epoch`
        // covers an account whose sequence was reset to 0 by `IncrementLocalEpoch`;
        // and `multichain_sequence` guards an account that established state via a
        // global (chain_id 0) config change without ever being created/imported.
        // Defer to the shared [`AccountState::is_initialized`] predicate so this
        // guard can't drift from it. This must be explicit now that
        // `authorize_actor` is an upsert and no longer reverts on a duplicate
        // initial actor (mirrors `createAccount`'s guard).
        let mut state = storage.get_account_state(address)?;
        if state.is_initialized() {
            return Err(ApplyError::AlreadyInitialized { account: address });
        }

        // Mark initialized and disable the implicit default-EOA path by default
        // (a created account has contract code, so the recovered==account path is
        // unreachable). Mirrors `createAccount`'s `flags = FLAG_REVOKE_DEFAULT_EOA`.
        // Written before initializing actors so a self-actorId k1 initial actor can
        // re-enable the inline self.
        state.local_sequence = 1;
        state.flags = Eip8130Constants::DEFAULT_EOA_REVOKED;
        storage.set_account_state(address, state)?;
```

**File:** crates/execution/eip8130/src/apply.rs (L957-962)
```rust
            return Ok((Address::ZERO, B256::ZERO));
        }
        if policy_data.len() != Eip8130Constants::POLICY_DATA_LEN {
            return Err(ApplyError::InvalidPolicyData);
        }
        let manager = Address::from_slice(&policy_data[..20]);
```

**File:** crates/common/evm/src/eip8130.rs (L1716-1744)
```rust
    /// Installs a created account's runtime code, enforcing the CREATE2 collision
    /// rule the reference contract gets for free from a real deploy: the
    /// destination must be empty (no code, zero nonce). The account info is read
    /// from the real journal (not the config overlay), so block inclusion
    /// enforces what mempool admission checks separately — an inclusion path that
    /// bypasses the pool cannot overwrite preexisting third-party code.
    ///
    /// The runtime is already validated non-empty, `<= MAX_CODE_SIZE`, and not
    /// `0xEF`-prefixed by [`AccountChangeApplier::apply_create`], so
    /// [`Bytecode::new_raw_checked`] never errors here; the fallible constructor
    /// is used anyway so any future gap surfaces as a validity error rather than
    /// a panic on transaction-controlled bytes.
    fn install_created_code(
        sctx: StorageCtx<'_>,
        address: Address,
        code: &Bytes,
    ) -> Result<(), BaseTransactionError> {
        let occupied = sctx
            .with_account_info(address, |info| Ok(!info.is_empty_code_hash() || info.nonce != 0))
            .map_err(BaseTransactionError::eip8130)?;
        if occupied {
            return Err(BaseTransactionError::eip8130(
                "create destination already has code or a non-zero nonce",
            ));
        }
        let bytecode =
            Bytecode::new_raw_checked(code.clone()).map_err(BaseTransactionError::eip8130)?;
        sctx.set_code(address, bytecode).map_err(BaseTransactionError::eip8130)
    }
```

**File:** crates/execution/txpool/src/validator.rs (L1456-1470)
```rust
    fn validate_eip8130_create_freshness(
        state: &dyn StateProvider,
        sender: Address,
        account: &reth_primitives_traits::Account,
    ) -> Result<(), InvalidPoolTransactionError> {
        if account.nonce != 0 {
            return Err(Self::eip8130_error("create sender nonce is non-zero"));
        }
        if Self::account_has_code(state, sender)
            .map_err(|error| Self::state_read_error(error, "sender code read failed"))?
        {
            return Err(Self::eip8130_error("create sender already has code"));
        }
        Ok(())
    }
```
