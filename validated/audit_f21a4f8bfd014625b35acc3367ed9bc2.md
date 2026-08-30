### Title
Unauthorized permanent fund-freezing via `CreateAccount` action on NEAR-implicit-looking top-level IDs - ([File: runtime/runtime/src/actions.rs])

### Finding Description
`action_create_account` (`runtime/runtime/src/actions.rs:167-210`) only validates a new top-level account ID against `is_top_level()` and the registrar-length rule at lines 176-190 [1](#0-0) . It never calls `AccountId::get_account_type()` (defined in `near-account-id`, used elsewhere at `AccountType::NearImplicitAccount` handling in `action_implicit_account_creation_transfer`, `runtime/runtime/src/actions.rs:225-244` [2](#0-1) ), so it cannot distinguish a normal >=32-char top-level name from a 64-lowercase-hex string that is syntactically indistinguishable from a NEAR-implicit account address. Because the registrar restriction is only enforced for names shorter than `min_allowed_top_level_account_length`, any unprivileged account can submit a `CreateAccount` action for an arbitrary 64-hex-char ID and it succeeds, installing `Account::new(ZERO, ZERO, AccountContract::None, num_bytes_account)` with **no access key**. This is corroborated by `integration-tests/src/tests/features/restrict_tla.rs`, whose test `test_create_top_level_accounts` asserts `CreateAccountOnlyByRegistrar` failures only for short/non-hex names (`"alice"`, `"0x0000...","0x0601..."`, etc.) — a 64-hex-char ID is conspicuously absent from that failing list, confirming such IDs pass the check and get created normally [3](#0-2) .

Once such an account exists, the runtime's implicit-account-creation path (`action_implicit_account_creation_transfer`, which always seeds a `FullAccess` key derived via `PublicKey::from_near_implicit_account`, lines 225-231) is only invoked when the destination account does **not yet exist**. Any later `Transfer` to the now-existing, keyless account instead goes through plain `action_transfer` (`runtime/runtime/src/actions.rs:160-165`), which merely increments the balance and never touches access keys [4](#0-3) . The resulting account is a valid-looking NEAR-implicit address that is permanently keyless and controllable by nobody — any funds sent to it are irrecoverably frozen.

### Impact Explanation
This breaks the authorization-exactness invariant of NEAR-implicit accounts: an address that looks like `hex(ed25519_pubkey)` should always be controllable exactly by the matching private key once it holds funds. An attacker can pre-create (front-run) any specific 64-hex-char address of interest — e.g., a victim's known deposit/implicit address — as a keyless account before the victim's incoming transfer lands. Subsequent transfers into that address (by the victim or by any exchange/bridge/faucet auto-generating implicit accounts) permanently freeze funds with no possible recovery, since no private key can ever authorize spending from a keyless `Account`. This matches the "permanent freezing of user funds" bounty category.

### Likelihood Explanation
The attack requires only an ordinary funded account submitting a single `CreateAccount` action for a target 64-hex-char string (cost: standard account-creation storage staking/gas, no special privilege). The attacker must know/guess the target implicit address before the victim's real deposit transaction executes (a front-running race), which is the main practical constraint, but the address is often public ahead of time (e.g., a wallet showing its intended deposit address, or a service publishing implicit addresses before crediting funds). The primitive itself is fully repeatable against any chosen 64-hex string.

### Recommendation
In `action_create_account`, reject the action (return an `ActionErrorKind`, e.g. a new "reserved account type" error) when `account_id.get_account_type()` is `NearImplicitAccount` or `EthImplicitAccount`, regardless of length or predecessor, so that IDs reserved for implicit-account semantics can only ever be created via the implicit-creation-transfer path that seeds the corresponding access key.

### Proof of Concept
Runtime/integration test:
1. Fund an ordinary account (not `registrar`) with sufficient balance.
2. Submit a `CreateAccount` action targeting a 64-lowercase-hex `AccountId` (e.g. `"a".repeat(64)` — a valid hex-looking string) from that funded predecessor.
3. Assert the transaction succeeds (mirroring `restrict_tla.rs`'s pattern but expecting success, not `CreateAccountOnlyByRegistrar`).
4. Query state and assert the account exists with `Account::amount() == 0` and no `AccessKey` present at `TrieKey::AccessKey { account_id, public_key }` for the public key `PublicKey::from_near_implicit_account(&account_id)`.
5. Submit a subsequent `Transfer` action to the same account ID with a nonzero deposit.
6. Assert the transfer succeeds (balance increases) but still no `AccessKey` exists for that account, contrasting with the normal implicit-account flow in `action_implicit_account_creation_transfer` (`runtime/runtime/src/actions.rs:213-244`) which always installs a `FullAccess` key when the account did not previously exist.

### Citations

**File:** runtime/runtime/src/actions.rs (L160-165)
```rust
pub(crate) fn action_transfer(account: &mut Account, deposit: Balance) -> Result<(), StorageError> {
    account.set_amount(account.amount().checked_add(deposit).ok_or_else(|| {
        StorageError::StorageInconsistentState("Account balance integer overflow".to_string())
    })?);
    Ok(())
}
```

**File:** runtime/runtime/src/actions.rs (L167-210)
```rust
pub(crate) fn action_create_account(
    fee_config: &RuntimeFeesConfig,
    account_creation_config: &AccountCreationConfig,
    account: &mut Option<Account>,
    actor_id: &mut AccountId,
    account_id: &AccountId,
    predecessor_id: &AccountId,
    result: &mut ActionResult,
) {
    if account_id.is_top_level() {
        if account_id.len() < account_creation_config.min_allowed_top_level_account_length as usize
            && predecessor_id != &account_creation_config.registrar_account_id
        {
            // A short top-level account ID can only be created registrar account.
            result.result = Err(ActionErrorKind::CreateAccountOnlyByRegistrar {
                account_id: account_id.clone(),
                registrar_account_id: account_creation_config.registrar_account_id.clone(),
                predecessor_id: predecessor_id.clone(),
            }
            .into());
            return;
        } else {
            // OK: Valid top-level Account ID
        }
    } else if !account_id.is_sub_account_of(predecessor_id) {
        // The sub-account can only be created by its root account. E.g. `alice.near` only by `near`
        result.result = Err(ActionErrorKind::CreateAccountNotAllowed {
            account_id: account_id.clone(),
            predecessor_id: predecessor_id.clone(),
        }
        .into());
        return;
    } else {
        // OK: Valid sub-account ID by proper predecessor.
    }

    *actor_id = account_id.clone();
    *account = Some(Account::new(
        Balance::ZERO,
        Balance::ZERO,
        AccountContract::None,
        fee_config.storage_usage_config.num_bytes_account,
    ));
}
```

**File:** runtime/runtime/src/actions.rs (L224-244)
```rust
    *actor_id = account_id.clone();
    match account_id.get_account_type() {
        AccountType::NearImplicitAccount => {
            let mut access_key = AccessKey::full_access();
            access_key.nonce = initial_nonce_value(block_height);

            // unwrap: here it's safe because the `account_id` has already been determined to be implicit by `get_account_type`
            let public_key = PublicKey::from_near_implicit_account(account_id).unwrap();

            *account = Some(Account::new(
                deposit,
                Balance::ZERO,
                AccountContract::None,
                fee_config.storage_usage_config.num_bytes_account
                    + public_key.trie_id_len() as u64
                    + borsh::object_length(&access_key).unwrap() as u64
                    + fee_config.storage_usage_config.num_extra_bytes_record,
            ));

            set_access_key(state_update, account_id.clone(), public_key, &access_key);
        }
```

**File:** integration-tests/src/tests/features/restrict_tla.rs (L10-59)
```rust
#[test]
fn test_create_top_level_accounts() {
    let epoch_length: BlockHeight = 5;
    let account: AccountId = "test0".parse().unwrap();
    let mut genesis = Genesis::test(vec![account.clone()], 1);
    genesis.config.epoch_length = epoch_length;
    genesis.config.transaction_validity_period = epoch_length * 2;
    genesis.config.protocol_version = PROTOCOL_VERSION;
    let runtime_config = near_parameters::RuntimeConfigStore::new(None);
    let mut env = TestEnv::builder(&genesis.config)
        .nightshade_runtimes_with_runtime_config_store(&genesis, vec![runtime_config])
        .build();

    // These accounts cannot be created because they are top level accounts that are not implicit.
    // Note that implicit accounts have to be 64 or 42 (if starts with '0x') characters long.
    let top_level_accounts = [
        "0x06012c8cf97bead5deae237070f9587f8e7a266da",
        "0a5e97870f263700f46aa00d967821199b9bc5a120",
        "0x000000000000000000000000000000000000000",
        "alice",
        // cspell:disable-next-line
        "thisisaveryverylongtoplevelaccount",
    ];
    for (index, id) in top_level_accounts.iter().enumerate() {
        let new_account_id = id.parse::<AccountId>().unwrap();
        let tx_hash = create_account(
            &mut env,
            account.clone(),
            new_account_id.clone(),
            epoch_length,
            1 + index as u64 * epoch_length,
            PROTOCOL_VERSION,
        );
        let transaction_result =
            env.clients[0].chain.get_final_transaction_result(&tx_hash).unwrap();
        assert_eq!(
            transaction_result.status,
            FinalExecutionStatus::Failure(
                ActionError {
                    index: Some(0),
                    kind: ActionErrorKind::CreateAccountOnlyByRegistrar {
                        account_id: new_account_id,
                        registrar_account_id: "registrar".parse().unwrap(),
                        predecessor_id: account.clone()
                    }
                }
                .into()
            )
        );
    }
```
