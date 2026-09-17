### Title
Residual EIP-8130 actor grants survive a self-key ownership handoff, letting a former admin retain control - (File: `crates/execution/eip8130/src/apply.rs`)

### Summary
The EIP-8130 `AccountConfiguration`/`AccountChangeApplier` model, exactly like LUKSO's LSP6 `KeyManager`, stores each delegated permission (`actor_config[actor_id][account]`) independently of who currently controls the account's "self" identity. An account owner can authorize a non-self actor with unrestricted (admin, `scope == 0`) or otherwise broad scope, then later "transfer" the account by revoking/rotating only the self actor (its own signing key). Because the non-self actor's `ActorRecord` is never automatically invalidated by a self-key change, the previously authorized actor's permissions survive the handoff verbatim — mirroring the exact "universal, owner-independent data key" root cause described in the LUKSO M-06 report.

### Finding Description
`AccountConfigurationStorage` keys every actor's permissions solely by `(actor_id, account)`, entirely decoupled from any notion of "current owner" or transfer epoch: [1](#0-0) 

`authorize_actor_with_account_state` installs or revokes only the targeted `actor_id`. A non-self actor branch is handled by `authorize_non_self_actor`/`revoke_actor`, entirely independent of the self actor's lifecycle: [2](#0-1) 

`revoke_actor` / `revoke_actor_with_account_state` only clear the single `actor_id` passed in; there is no mechanism that walks or invalidates other actors when the self key changes: [3](#0-2) 

The only global invalidation primitive in the system, `IncrementLocalEpoch`, is documented to invalidate merely "every unlanded local signature at a prior epoch" — it resets the local sequence/epoch counters for replay protection, not the set of currently-installed non-self `actor_config` entries: [4](#0-3) 

So, exactly as in the LUKSO finding: an "owner" (self actor) can `AuthorizeActor` a second, independent admin-scope actor (`scope == 0`, unrestricted) before handing off control (e.g., selling/transferring the account by revoking/rotating their own self key or transferring by giving the receiver only the "new self" credential). Nothing in `apply_config_change_with_account_state`'s dispatch of `AuthorizeActor`/`RevokeActor`/`IncrementLocalEpoch` forces enumeration or invalidation of previously granted non-self actors on self-key rotation: [5](#0-4) 

### Impact Explanation
If a new controller receives what they believe is exclusive control of an EIP-8130 smart account (via a fresh self-signing key), any admin-scope (`scope == 0`) or otherwise broadly-scoped actor previously authorized by the prior owner remains fully live and unauthenticated-by-self, i.e., it can still sign `SignedAccountChanges` batches that pass `ConfigChangeAuthorizer::authorize_with_account_state`'s admin-scope gate: [6](#0-5) 

Such a surviving admin actor can re-authorize itself or a new colluding actor with unrestricted scope, revoke the new owner's self actor, install a malicious delegation, or otherwise seize control of the account and its assets — an unauthorized operation / theft outcome consistent with a "rug pull" against the new controller, the same class of impact the LUKSO judges accepted at Medium severity.

### Likelihood Explanation
This requires the same conditional trust assumption the LUKSO judges weighed: a receiving party must trust that "ownership" (self-key control) implies exclusive control, without independently auditing/enumerating and revoking every previously-authorized non-self `actor_config` entry via an indexer or off-chain tooling. Given EIP-8130 accounts are explicitly designed to support delegated actors, session keys, and account transfers/imports, this scenario (a malicious or careless prior owner leaving a stale admin actor behind) is realistic, matching the LUKSO sponsor's own acknowledgment that "the usage of account transfers is not as unlikely as initially seems."

### Recommendation
Mirror the LUKSO team's proposed mitigation: introduce an explicit, atomic "ownership transfer"/reset primitive (e.g., extend `IncrementLocalEpoch` or add a dedicated `ChangeType` such as `ResetActors`) that mass-invalidates all non-self `actor_config` entries — or salts/scopes each entry's storage key to a per-account transfer nonce/epoch, similar to LUKSO's proposed `AddressPermissions:Permissions:<controller>+salt` — so a new controller can be certain no residual actor persists after taking over self control. At minimum, expose a trustless on-chain view enumerating all live actors so the new owner can verify and clean up before relying on exclusive control.

### Proof of Concept
1. Owner `A` creates or controls an EIP-8130 account and signs a `SignedAccountChanges` batch containing `AuthorizeActor(actor_id = B, config = { authenticator: K1_or_other, scope: 0 /* unrestricted */ })`, installed via `AccountChangeApplier::authorize_non_self_actor` per `apply_config_change_with_account_state`.
2. `A` "transfers" the account to `C` by revoking/rotating only the self actor (`RevokeActor(self_actor_id)` followed by `AuthorizeActor(self_actor_id, new_key_of_C)`), per `revoke_actor`/`authorize_actor_with_account_state`.
3. `C` believes they now exclusively control the account since their new self key is installed and `A`'s old self key is revoked.
4. `B` (still colluding with or controlled by `A`) signs a new `SignedAccountChanges` batch; because `actor_config[B][account]` was never touched by step 2, `ConfigChangeAuthorizer::authorize_with_account_state` still resolves `B` as an admin (unrestricted, `scope == 0`) actor and grants it authority to revoke `C`'s self actor, install a delegation, or authorize a new colluding actor — seizing the account back from `C`.

### Citations

**File:** crates/execution/eip8130/src/account_config.rs (L14-22)
```rust
/// mapping(bytes32 actorId => mapping(address account => ActorRecord)) _actors;       // slot 0
/// mapping(address account => AccountState)                           _accountState;  // slot 1
/// ```
///
/// where `ActorRecord` is `{ ActorConfig config; address policyManager; bytes32
/// policyCommitment; }` — the packed `config` word followed by the optional
/// policy (`manager`, `commitment`) on the two consecutive slots, so a Verkle
/// witness can cover an actor's config and policy together (base/eip-8130 #95).
/// The [`Self::actors`] mapping is keyed to the `config` slot (offset 0); the
```

**File:** crates/execution/eip8130/src/account_config.rs (L444-451)
```rust
    /// Local-channel sequence (`uint32`). Set to 1 at bootstrap (create/import);
    /// a non-zero value also marks the account initialized. Reset to 0 by an
    /// `IncrementLocalEpoch` op (which bumps `local_epoch`).
    pub local_sequence: u64,
    /// Local-channel epoch (`uint32`). Incremented by `IncrementLocalEpoch`,
    /// invalidating every unlanded local signature at a prior epoch. A non-zero
    /// value also marks the account initialized.
    pub local_epoch: u64,
```

**File:** crates/execution/eip8130/src/apply.rs (L408-457)
```rust
        for change in changes {
            match change.change_type {
                ChangeType::AuthorizeActor => {
                    // Applies one `AuthorizeActor` op: JIT expiry skip, locked-account
                    // policy, then `_authorizeActor`. Mirrors `Keystore._applyAuthorize`.
                    let (actor_id, config, policy_data) = Self::decode_authorize(&change.payload)?;
                    // Replayable JIT path: drop an already-lapsed grant without
                    // reverting. Uses the canonical `_isExpired` boundary (`now >
                    // expiry`), so a grant with `expiry == now` is still live for
                    // that second and installs rather than being dropped here.
                    if is_unsequenced && config.is_expired(now) {
                        continue;
                    }
                    if locked {
                        Self::enforce_locked_authorize_rules(
                            storage,
                            account,
                            actor_id,
                            &config,
                            &policy_data,
                            state,
                            now,
                        )?;
                    }
                    Self::authorize_actor_with_account_state(
                        storage,
                        account,
                        actor_id,
                        config,
                        &policy_data,
                        state,
                    )?;
                }
                ChangeType::RevokeActor => {
                    if locked {
                        return Err(ApplyError::AccountIsLocked);
                    }
                    let actor_id = Self::decode_revoke(&change.payload)?;
                    revoke_discount_slots = revoke_discount_slots.saturating_add(
                        Self::revoke_actor_with_account_state(storage, account, actor_id, state)?,
                    );
                }
                ChangeType::IncrementLocalEpoch => {
                    Self::apply_increment_local_epoch(&change.payload, state)?;
                }
                // Lock / Unlock apply handlers are not yet enshrined.
                ChangeType::Lock | ChangeType::Unlock => {
                    return Err(ApplyError::UnknownChangeType);
                }
            }
```

**File:** crates/execution/eip8130/src/apply.rs (L621-638)
```rust
    pub fn authorize_actor_with_account_state(
        storage: &mut AccountConfigurationStorage<'_>,
        account: Address,
        actor_id: B256,
        config: ActorConfig,
        policy_data: &[u8],
        state: &mut AccountState,
    ) -> Result<(), ApplyError> {
        // Authenticator namespace: address(0) is the empty-slot sentinel, never a
        // valid selector (`require(config.authenticator >= K1_AUTHENTICATOR)`).
        if config.authenticator.is_zero() {
            return Err(ApplyError::InvalidAuthenticator);
        }

        let self_id = AccountConfigurationStorage::self_actor_id(account);
        if actor_id != self_id {
            return Self::authorize_non_self_actor(storage, account, actor_id, config, policy_data);
        }
```

**File:** crates/execution/eip8130/src/apply.rs (L713-726)
```rust
    pub fn revoke_actor(
        storage: &mut AccountConfigurationStorage<'_>,
        account: Address,
        actor_id: B256,
    ) -> Result<(), ApplyError> {
        if actor_id == AccountConfigurationStorage::self_actor_id(account) {
            let mut state = storage.get_account_state(account)?;
            Self::revoke_actor_with_account_state(storage, account, actor_id, &mut state)?;
            storage.set_account_state(account, state)?;
            return Ok(());
        }
        let config = storage.actor_config_slot(account, actor_id)?;
        Self::revoke_explicit_actor(storage, account, actor_id, config)
    }
```

**File:** crates/execution/eip8130/src/config.rs (L74-97)
```rust
    pub fn authorize_with_account_state(
        storage: &AccountConfigurationStorage<'_>,
        account: Address,
        local_chain_id: u64,
        change: &SignedAccountChanges,
        now: u64,
        state: &AccountState,
    ) -> Result<ResolvedActor, TxAuthError> {
        Self::check_channel_sequence(change, state)?;

        // Reconstruct the digest, authenticate the signature, and require admin scope.
        let digest = Self::changes_digest(account, local_chain_id, change);
        let resolved = ActorAuthorizer::authenticate_actor_with_account_state(
            storage,
            account,
            digest,
            &change.signature,
            now,
            Some(state),
        )?;
        if !Operation::Config.is_granted(&resolved) {
            return Err(TxAuthError::Scope { operation: Operation::Config, scope: resolved.scope });
        }
        Ok(resolved)
```
