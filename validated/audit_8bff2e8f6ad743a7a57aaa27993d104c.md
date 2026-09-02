### Title
Stale confirmations from deleted members are still counted toward the multisig execution threshold - (File: `multisig2/src/lib.rs`)

### Summary
`MultiSigContract::confirm` in [1](#0-0)  decides whether a request executes purely by counting entries in the `confirmations: LookupMap<RequestId, HashSet<String>>` set and comparing `confirmations.len() + 1 >= self.num_confirmations`. It never re-checks that every account/key already present in that `HashSet` is still a current member of `self.members`. `delete_member` at [2](#0-1)  only purges confirmations for requests that the deleted member itself *originally submitted* (`r.member == member`), not confirmations the deleted member cast on *other* pending requests as an approver. This is exactly the "confirmations counted versus live members" binding described in the analog rules: the number the contract trusts (`confirmations.len()`) can silently diverge from the number of confirmations actually cast by accounts still entitled to confirm.

### Finding Description
The struct stores confirmations as an unordered set keyed by a member's serialized identity (`member.to_string()`), independent of the `members: UnorderedSet<MultisigMember>` collection that defines who is currently authorized: [3](#0-2) .

When a member is removed via `DeleteMember`, `delete_member` filters and deletes only the *requests originally added by that member* (`self.requests.iter().filter_map(|(k,r)| if r.member == member ...)`), then removes that member from `self.members`: [4](#0-3) . It does not walk `self.confirmations` for other still-pending requests and strip out the removed member's prior confirmation string.

Consequently, for any request `R` that member `B` confirmed (but did not originate) before `B` was removed, `R`'s `confirmations` set retains `B`'s entry forever. When `confirm` is later called by a *different, still-valid* member, the threshold check `confirmations.len() as u32 + 1 >= self.num_confirmations` counts `B`'s stale confirmation as if `B` were still live, allowing the request to execute with fewer genuinely authorized confirmations than `num_confirmations` requires: [5](#0-4) .

### Impact Explanation
This crosses the threshold-authorization boundary explicitly called Critical in the rules ("a multisig request executed below threshold"). Because `execute_request` can perform `Transfer`, `FunctionCall`, `AddKey`/full-access-key grants, `DeployContract`, or further `AddMember`/`DeleteMember` changes on the multisig's own account ( [6](#0-5) ), an execution that slips through with one fewer live confirmation than intended can transfer NEAR out of the account, deploy attacker/erroneous code, or grant a full-access key — all while the on-chain state claims `num_confirmations` was met.

### Likelihood Explanation
The scenario requires: (a) a legitimate confirmation from a member who is later removed, on a request that was not yet fully confirmed, and (b) the remaining live members later supplying the rest of the confirmations. This is a normal, expected sequence of multisig operations (membership churn plus concurrent pending requests) rather than a contrived edge case, so it can occur without any privileged bypass — it only requires ordinary members acting in the ordinary order permitted by the contract's own API (`add_request`, `confirm`, `DeleteMember` request, `confirm` again).

### Recommendation
In `confirm`, before comparing against `num_confirmations`, filter `confirmations` to only members still present in `self.members` (or persist confirmations by validating membership at read time), and/or have `delete_member` scan all pending requests' confirmation sets and remove the deleted member's entry, not just requests they originated.

### Proof of Concept
1. Initialize multisig with members `{A, B, C, D}` and `num_confirmations = 3`.
2. `A` calls `add_request_and_confirm(R)` where `R` is a `Transfer` — `confirmations[R] = {A}`.
3. `B` calls `confirm(R)` — `confirmations[R] = {A, B}` (len 2, still short of 3).
4. Separately, members `A, C, D` submit and confirm a `DeleteMember{B}` request (3 confirmations, satisfies threshold) — `delete_member` removes `B` from `self.members`, but since `B` did not originate `R`, `R`'s confirmation set is left untouched at [7](#0-6) .
5. `C` (a genuinely live member who has not yet confirmed `R`) calls `confirm(R)`. The check `confirmations.len() + 1 >= num_confirmations` evaluates `2 + 1 >= 3` and executes `R`'s `Transfer`, even though only `A` and `C` are live members who actually approved it — one fewer live confirmation than the configured threshold of 3.

### Citations

**File:** multisig2/src/lib.rs (L116-133)
```rust
#[near_bindgen]
#[derive(BorshDeserialize, BorshSerialize, PanicOnDefault)]
pub struct MultiSigContract {
    /// Members of the multisig.
    members: UnorderedSet<MultisigMember>,
    /// Number of confirmations required.
    num_confirmations: u32,
    /// Latest request nonce.
    request_nonce: RequestId,
    /// All active requests.
    requests: UnorderedMap<RequestId, MultiSigRequestWithSigner>,
    /// All confirmations for active requests.
    confirmations: LookupMap<RequestId, HashSet<String>>,
    /// Number of requests per member.
    num_requests_pk: LookupMap<String, u32>,
    /// Limit number of active requests per member.
    active_requests_limit: u32,
}
```

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

**File:** multisig2/src/lib.rs (L355-379)
```rust
    /// Delete member from the list. Removes access key if the member is key based.
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
