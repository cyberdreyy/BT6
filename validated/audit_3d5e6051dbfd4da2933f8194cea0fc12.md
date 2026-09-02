Confirmed: `DeleteKey` only purges requests *originated by* the removed key (`r.signer_pk == pk`) and drops that key's `num_requests_pk` counter; it never scans other still-open requests' `confirmations` sets for a lingering vote cast by that same key before it was deleted.

### Title
Confirmations from a deleted multisig key remain counted toward the threshold, allowing execution below the live-member requirement - (File: multisig/src/lib.rs)

### Summary
`MultiSigContract::confirm` counts entries in `confirmations: UnorderedMap<RequestId, HashSet<PublicKey>>` against `num_confirmations` [1](#0-0) . When a `DeleteKey` action executes, it only removes requests that were *created* by the removed key and clears that key's own `num_requests_pk` entry; it never removes the deleted key's public key from the `confirmations` set of any *other* still-pending request it had previously confirmed [2](#0-1) . This is the same root-cause pattern as the Wormhole `TransceiverRegistry::_setTransceiver` bug: a stale piece of per-entity state (there, `registered`/`enabled` flags; here, a confirmation vote) is never reconciled when the entity's authorization is removed, and the counted state (confirmations) diverges from the true entitlement (currently-live signing keys).

### Finding Description
The equality that must hold is: `confirmations.len()` for an executed request `==` number of *currently live* multisig keys that actually confirmed it. Once a key is deleted via `MultiSigRequestAction::DeleteKey`, that binding breaks for any other pending request the deleted key had already confirmed, because its vote remains inside `self.confirmations.get(&request_id)` forever [3](#0-2) . The only cleanup performed on `DeleteKey` is scoped to `requests.iter().filter(|(_k, r)| r.signer_pk == pk)`, i.e. requests the deleted key itself created, not requests it merely confirmed [4](#0-3) .

### Impact Explanation
A pending request can reach and pass the `num_confirmations` threshold in `confirm` using a vote from a key that is no longer part of the multisig, i.e., a request is executed with fewer *live* confirmations than `num_confirmations` actually requires [5](#0-4) . Since `execute_request` can perform `Transfer`, `AddKey`, `FunctionCall`, etc. [6](#0-5) , this allows a multisig request to be executed below the intended threshold — matching the "Critical: a multisig request executed below threshold" impact category.

### Likelihood Explanation
This requires only ordinary multisig operation: (1) a request is confirmed by key A but not yet reaching threshold, (2) a subsequent, unrelated `DeleteKey{A}` request is separately confirmed and executed (a normal governance action, not requiring any victim/attacker collusion), (3) the remaining live keys continue confirming the original pending request. Because `confirm`/`execute_request` never re-validates that all counted confirmers are still live keys, the stale confirmation silently counts toward the threshold. This is a realistic sequence for any multisig with key rotation, which is an expected and encouraged operational practice.

### Recommendation
When executing `DeleteKey`, scan `self.confirmations` for all requests (not just those originated by the deleted key) and remove the deleted public key from every confirmation set; alternatively, validate at `confirm`/execution time that every public key present in a request's confirmation set still corresponds to a currently valid access key/member before treating the vote as counted.

### Proof of Concept
1. Multisig deployed with `num_confirmations = 3` and keys `K1, K2, K3, K4`.
2. `K1` calls `add_request` for `Transfer{amount}` → `request_id = 0`.
3. `K2` calls `confirm(0)` → confirmations = `{K2}` (1 < 3, not executed).
4. Separately, `K3` and `K4` (plus one more) create and confirm a `DeleteKey{public_key: K2}` request, which executes, removing `K2`'s access key from the account and clearing `K2`'s own originated requests/`num_requests_pk`, but leaving `confirmations[0] = {K2}` untouched [2](#0-1) .
5. `K3` calls `confirm(0)` → confirmations becomes `{K2, K3}`, length 2, still below 3.
6. `K4` calls `confirm(0)` → confirmations length reaches 3 (`K2, K3, K4`) even though `K2` is no longer a valid key; `execute_request` fires the `Transfer` [5](#0-4) .
7. The transfer executes with only 2 truly live confirming keys (`K3`, `K4`) against a configured threshold of 3 — a request executed below the effective threshold of live members.

Note: `multisig2/src/lib.rs` has the analogous `DeleteMember` path with the same structure (`confirm`/`execute_request`) and should be checked for the identical gap, though I did not fully trace its member-removal cleanup code in this session.

### Citations

**File:** multisig/src/lib.rs (L167-227)
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
