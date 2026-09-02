## Analog Found: Stale confirmations from removed multisig members count toward execution threshold [1](#0-0) 

### Title
Stale confirmations from deleted multisig members still count toward `num_confirmations`, allowing requests to execute below the live-member threshold - (File: `multisig2/src/lib.rs`, also present in `multisig/src/lib.rs`)

### Summary
The rippled `handleLCL` bug is a class of "the code checks a threshold/condition against a snapshot of state that a preceding step should have — but did not — refresh." The NEAR analog here: `confirm()` decides whether a request has reached quorum purely by counting entries in the `confirmations: HashSet<String>` bag versus `num_confirmations` [2](#0-1) . When a member is removed via `delete_member`, the code only purges confirmations for requests that member *originated*, not confirmations that member *cast on other members' pending requests* [3](#0-2) . A removed member's earlier "yes" vote silently remains valid forever, so `confirmations.len()` can reach `num_confirmations` while containing fewer distinct *live* members than the multisig was configured to require.

### Finding Description
The binding that must hold is:
`confirmations counted (used to authorize execute_request) == confirmations from members currently in self.members`

`delete_member` filters only requests keyed by `r.member == member` (i.e., requests the removed member *created*) when clearing state:
```
let request_ids: Vec<u32> = self
    .requests
    .iter()
    .filter_map(|(k, r)| if r.member == member { Some(k) } else { None })
    .collect();
for request_id in request_ids {
    self.confirmations.remove(&request_id);
    self.requests.remove(&request_id);
}
``` [4](#0-3) 

It never scans `self.confirmations` (a `LookupMap<RequestId, HashSet<String>>` keyed by every active request) to strip the removed member's confirmation string from requests that member merely *confirmed but did not create*. `confirm()` later just does:
```
if confirmations.len() as u32 + 1 >= self.num_confirmations {
    let request = self.remove_request(request_id);
    self.execute_request(request)
}
``` [5](#0-4) 

It never re-validates that every string in `confirmations` corresponds to a member still present in `self.members` — so a stale vote is indistinguishable from a live one. The identical pattern exists in the legacy `multisig/src/lib.rs`, where `DeleteKey` only clears confirmations for requests originated by that key (`r.signer_pk == pk`), leaving stale confirmations elsewhere [6](#0-5) [7](#0-6) .

### Impact Explanation
This breaks the K-of-N custody guarantee that `execute_request` (which can transfer funds, deploy code, add/delete keys, or call arbitrary functions on behalf of the account [8](#0-7) ) is only reachable once `num_confirmations` *live* members have approved. With this bug, a request can be executed with fewer live approvals than `num_confirmations` mandates, because a removed member's residual confirmation is still tallied. This directly matches the Critical impact category "a multisig request executed below threshold."

### Likelihood Explanation
This requires only ordinary multisig operation, no attacker privilege beyond being (at some point) one of the N members:
1. A member confirms a pending request (their own or someone else's) that has not yet reached quorum.
2. That member is later removed via `DeleteMember` (e.g., key compromise discovered, employee offboarded, member voted out) — a normal, expected governance action.
3. The pending request the removed member confirmed is still sitting in `requests`/`confirmations`, untouched by the deletion.
4. Remaining members continue confirming it; the stale vote is added to the running total, so the request executes once `live_confirmations + stale_confirmations >= num_confirmations`, even though live approvals are short of the configured threshold.

Since removing a member is precisely the scenario where an operator expects that member's authority to be revoked immediately, this is a realistic and likely-to-be-hit condition, not a contrived edge case.

### Recommendation
When executing `DeleteMember`/`DeleteKey`, iterate all active requests (not just ones authored by the removed member) and remove the deleted member's/key's entry from each request's `confirmations` set. Alternatively, in `confirm()`/`execute_request()`, filter `confirmations` down to entries that are still in `self.members` before comparing against `num_confirmations`.

### Proof of Concept
```
// members: {A, B, C, D}, num_confirmations = 3
1. B calls add_request_and_confirm(X)      // confirmations(X) = {B}
2. A calls confirm(X)                      // confirmations(X) = {B, A}, len=2 < 3, not executed
3. Members submit/confirm DeleteMember{A} via a separate request Y
   -> delete_member(A) only clears requests where r.member == A (i.e., requests A created).
      X was created by B, so confirmations(X) is untouched -> still {B, A}.
   -> self.members now = {B, C, D}
4. C calls confirm(X)                      // confirmations(X).len()+1 = 3 >= num_confirmations(3)
   -> execute_request(X) runs, even though only B and C (2 of the 3 live members) actually approved.
``` [9](#0-8) [1](#0-0)

### Citations

**File:** multisig2/src/lib.rs (L224-290)
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
                MultiSigRequestAction::AddMember { member } => {
                    self.assert_self_request(receiver_id.clone());
                    self.add_member(promise, member)
                }
                MultiSigRequestAction::DeleteMember { member } => {
                    self.assert_self_request(receiver_id.clone());
                    self.delete_member(promise, member)
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
                            permission.method_names.join(","),
                        )
                    } else {
                        // wallet UI should warn user if receiver_id == env::current_account_id(), adding FAK will render multisig useless
                        promise.add_full_access_key(public_key.into())
                    }
                }
                MultiSigRequestAction::FunctionCall {
                    method_name,
                    args,
                    deposit,
                    gas,
                } => promise.function_call(
                    method_name,
                    args.into(),
                    deposit.into(),
                    Gas::from(gas.0),
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

**File:** multisig2/src/lib.rs (L292-315)
```rust
    /// Confirm given request with given signing key.
    /// If with this, there has been enough confirmation, a promise with request will be scheduled.
    pub fn confirm(&mut self, request_id: RequestId) -> PromiseOrValue<bool> {
        self.assert_valid_request(request_id);
        let member = self
            .current_member()
            .unwrap_or_else(|| env::panic_str("Must be validated above"));
        let mut confirmations = self.confirmations.get(&request_id).unwrap();
        assert(
            !confirmations.contains(&member.to_string()),
            "Already confirmed this request with this key",
        );
        if confirmations.len() as u32 + 1 >= self.num_confirmations {
            let request = self.remove_request(request_id);
            /********************************
            NOTE: If the tx execution fails for any reason, the request and confirmations are removed already, so the client has to start all over
            ********************************/
            self.execute_request(request)
        } else {
            confirmations.insert(member.to_string());
            self.confirmations.insert(&request_id, &confirmations);
            PromiseOrValue::Value(true)
        }
    }
```

**File:** multisig2/src/lib.rs (L356-379)
```rust
    fn delete_member(&mut self, promise: Promise, member: MultisigMember) -> Promise {
        assert(
            self.members.len() - 1 >= self.num_confirmations as u64,
            "Removing given member will make total number of members below number of confirmations",
        );
        // delete outstanding requests by public_key
        let request_ids: Vec<u32> = self
            .requests
            .iter()
            .filter_map(|(k, r)| if r.member == member { Some(k) } else { None })
            .collect();
        for request_id in request_ids {
            // remove confirmations for this request
            self.confirmations.remove(&request_id);
            self.requests.remove(&request_id);
        }
        // remove num_requests_pk entry for member
        self.num_requests_pk.remove(&member.to_string());
        self.members.remove(&member);
        match member {
            MultisigMember::AccessKey { public_key } => promise.delete_key(public_key.into()),
            MultisigMember::Account { account_id: _ } => promise,
        }
    }
```

**File:** multisig/src/lib.rs (L198-216)
```rust
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
