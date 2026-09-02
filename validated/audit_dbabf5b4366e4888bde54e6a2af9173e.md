### Title
Stale confirmations from deleted multisig keys let a request execute below the configured signer threshold - (File: `multisig/src/lib.rs`)

### Summary
`MultiSigContract::confirm` authorizes execution purely by counting entries in the `confirmations` set for a request and comparing that count to `num_confirmations`. When a key is removed via a `DeleteKey` action, the contract only purges pending *requests originally added by* that key — it never purges confirmations that key had already cast on other, still-pending requests. As a result, a revoked key's confirmation keeps counting toward the threshold on requests it is no longer entitled to approve, letting a request execute with fewer *currently live* signers than `num_confirmations` requires.

### Finding Description
The invariant the contract is supposed to enforce is:

```
confirmations(request).len() >= num_confirmations  ⇒  at least num_confirmations distinct, currently-authorized keys approved this request
```

`confirm()` only checks the raw cardinality of the `confirmations` set: [1](#0-0) 

The only cleanup of stale state happens inside the `DeleteKey` action handler in `execute_request`, and it is scoped incorrectly — it filters `self.requests` by `r.signer_pk == pk` (i.e., the key that *originally created* the request), not by whether `pk` is present in the `confirmations` set of some other pending request: [2](#0-1) 

So if key `K2` confirmed request `A` (created by `K1`), and `K2` is later deleted via a separate, fully-confirmed `DeleteKey{K2}` request, request `A`'s confirmation set still contains `K2`. `K2`'s revoked signature keeps counting toward `A`'s threshold. This exactly parallels the report's bug class: a tracked/recorded value (`confirmations` count) is not kept in sync with the real, current state (live keys on the account) after a mutating action (`DeleteKey`), so a later operation (`confirm`/execute) settles on the stale, incorrect value.

### Impact Explanation
This allows a multisig request to be executed with fewer than `num_confirmations` *currently valid* signers — explicitly listed as a Critical impact ("a multisig request executed below threshold"). Concretely:
- A compromised or since-revoked key's earlier vote on a still-pending malicious/unwanted request remains valid forever.
- Trusted members rotating out a key (the exact scenario the multisig is meant to defend against, e.g. a suspected-compromised key) do not actually invalidate that key's prior confirmations on other pending requests.
- Funds can be transferred, keys added, or contract code deployed via `execute_request` (`Transfer`, `AddKey`, `DeployContract`, etc.) using a mix of live and stale/revoked confirmations that never reaches real `num_confirmations` live-signer consensus.

### Likelihood Explanation
This requires no external exploit beyond normal multisig lifecycle operations: adding a request, confirming it partially, and later performing a legitimate key-rotation (`DeleteKey`) on the *same account* while the first request is still pending. This is a routine operational sequence (e.g., revoking a suspected-compromised member) rather than a contrived attack, making it readily reachable in practice.

### Recommendation
When executing `DeleteKey`, also scan and prune the deleted public key from the `confirmations` set of every other pending request (not just requests it originally created), or alternatively re-validate at `confirm()`/execution time that every public key present in a request's `confirmations` set still corresponds to a currently valid access key/member on the account before counting it toward `num_confirmations`.

### Proof of Concept
Assume a 3-of-3 multisig (`num_confirmations = 3`) with keys `K1`, `K2`, `K3`:
1. Using `K1`, call `add_request` for request `A` (e.g., `Transfer` funds to an attacker-controlled account). `confirmations(A) = {}`.
2. `K1` calls `confirm(A)` → `confirmations(A) = {K1}`.
3. `K2` calls `confirm(A)` → `confirmations(A) = {K1, K2}` (below threshold, not yet executed).
4. Separately, create and fully confirm request `B = DeleteKey{public_key: K2}` (targeting the multisig's own account, satisfying `assert_self_request`). `B` executes via `execute_request`, deleting `K2` from the account's access keys.
   - The `DeleteKey` handler only removes requests where `r.signer_pk == K2`; request `A` was created by `K1`, so it is untouched, and `confirmations(A)` still contains `K2`.
5. Now only `K1` and `K3` are live keys on the account, yet `confirmations(A)` still shows `{K1, K2}`.
6. `K3` calls `confirm(A)` → `confirmations(A).len() + 1 = 3 >= num_confirmations (3)` → `execute_request(A)` runs and the transfer/action executes, even though only `K1` and `K3` are actually live, authorized signers — one full "confirmation" (`K2`'s) was stale/revoked. [3](#0-2)

### Citations

**File:** multisig/src/lib.rs (L167-244)
```rust
    fn execute_request(&mut self, request: MultiSigRequest) -> PromiseOrValue<bool> {
        let mut promise = Promise::new(request.receiver_id.clone());
        let receiver_id = request.receiver_id.clone();
        let num_actions = request.actions.len();
        for action in request.actions {
            promise = match action {
                MultiSigRequestAction::Transfer { amount } => promise.transfer(amount.into()),
                MultiSigRequestAction::CreateAccount => promise.create_account(),
                MultiSigRequestAction::DeployContract { code } => {
                    promise.deploy_contract(code.into())
                }
                MultiSigRequestAction::AddKey {
                    public_key,
                    permission,
                } => {
                    self.assert_self_request(receiver_id.clone());
                    if let Some(permission) = permission {
                        promise.add_access_key(
                            public_key.into(),
                            permission
                                .allowance
                                .map(|x| x.into())
                                .unwrap_or(DEFAULT_ALLOWANCE),
                            permission.receiver_id,
                            permission.method_names.join(",").into_bytes(),
                        )
                    } else {
                        // wallet UI should warn user if receiver_id == env::current_account_id(), adding FAK will render multisig useless
                        promise.add_full_access_key(public_key.into())
                    }
                }
                MultiSigRequestAction::DeleteKey { public_key } => {
                    self.assert_self_request(receiver_id.clone());
                    let pk: PublicKey = public_key.into();
                    // delete outstanding requests by public_key
                    let request_ids: Vec<u32> = self
                        .requests
                        .iter()
                        .filter(|(_k, r)| r.signer_pk == pk)
                        .map(|(k, _r)| k)
                        .collect();
                    for request_id in request_ids {
                        // remove confirmations for this request
                        self.confirmations.remove(&request_id);
                        self.requests.remove(&request_id);
                    }
                    // remove num_requests_pk entry for public_key
                    self.num_requests_pk.remove(&pk);
                    promise.delete_key(pk)
                }
                MultiSigRequestAction::FunctionCall {
                    method_name,
                    args,
                    deposit,
                    gas,
                } => promise.function_call(
                    method_name.into_bytes(),
                    args.into(),
                    deposit.into(),
                    gas.into(),
                ),
                // the following methods must be a single action
                MultiSigRequestAction::SetNumConfirmations { num_confirmations } => {
                    self.assert_one_action_only(receiver_id, num_actions);
                    self.num_confirmations = num_confirmations;
                    return PromiseOrValue::Value(true);
                }
                MultiSigRequestAction::SetActiveRequestsLimit {
                    active_requests_limit,
                } => {
                    self.assert_one_action_only(receiver_id, num_actions);
                    self.active_requests_limit = active_requests_limit;
                    return PromiseOrValue::Value(true);
                }
            };
        }
        promise.into()
    }
```

**File:** multisig/src/lib.rs (L246-266)
```rust
    /// Confirm given request with given signing key.
    /// If with this, there has been enough confirmation, a promise with request will be scheduled.
    pub fn confirm(&mut self, request_id: RequestId) -> PromiseOrValue<bool> {
        self.assert_valid_request(request_id);
        let mut confirmations = self.confirmations.get(&request_id).unwrap();
        assert!(
            !confirmations.contains(&env::signer_account_pk()),
            "Already confirmed this request with this key"
        );
        if confirmations.len() as u32 + 1 >= self.num_confirmations {
            let request = self.remove_request(request_id);
            /********************************
            NOTE: If the tx execution fails for any reason, the request and confirmations are removed already, so the client has to start all over
            ********************************/
            self.execute_request(request)
        } else {
            confirmations.insert(env::signer_account_pk());
            self.confirmations.insert(&request_id, &confirmations);
            PromiseOrValue::Value(true)
        }
    }
```
