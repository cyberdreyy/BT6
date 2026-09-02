## Title
Stale confirmations from removed multisig members allow request execution below the effective live-member threshold - (File: `multisig2/src/lib.rs`)

## Summary
`delete_member()` only removes requests that were *created* by the member being deleted; it does not scrub that member's existing confirmations from other still-open requests. Since `confirmations` is a plain `HashSet<String>` counted by length in `confirm()`, a confirmation cast by a member before their removal continues to count toward `num_confirmations` after that member is deleted, breaking the equality that should hold: `confirmations counted == confirmations by currently-live members`.

## Finding Description
`confirm()` determines whether to execute a request purely by comparing `confirmations.len()` to `self.num_confirmations`: [1](#0-0) 

`delete_member()` is invoked when a `DeleteMember` request executes. It removes only the *requests created by* the removed member, and removes that member from `self.members` and `self.num_requests_pk`, but it never iterates `self.confirmations` to strip the removed member's entries from other requests' confirmation sets: [2](#0-1) 

`assert_valid_request()` only validates that the *caller* confirming is currently a member (`current_member().is_some()`); it never re-validates that the accumulated `confirmations` set only contains current members: [3](#0-2) 

Concretely: suppose the multisig has members `{A, B, C, D}` with `num_confirmations = 3`. Member `A` creates request R1 (transfer funds), and `B` confirms it (`confirmations(R1) = {B}`, since A's creation via `add_request_and_confirm` would also count, but assume plain `add_request`). Then, in parallel, a separate request removes `B` via `DeleteMember`, reducing live members to `{A, C, D}`. `delete_member` deletes any requests *created by* `B`, but R1 was created by `A`, so R1 and its confirmation set `{B}` survive untouched. Later `A` confirms R1 (`confirmations(R1) = {B, A}`), then `C` confirms (`confirmations(R1).len() = 3 >= num_confirmations`), and R1 executes. Effectively, `B`'s stale confirmation — cast by an account that is no longer a member — was counted as one of the three required confirmations, so the request executed with only 2 live-member confirmations (A and C) against a nominal 3-of-4 threshold.

## Impact Explanation
This breaks the custody binding "confirmations counted versus live members." Because `Transfer`, `FunctionCall`, `AddKey`, `DeployContract` and other privileged actions are gated solely by `confirmations.len() >= num_confirmations` [4](#0-3) , a request (e.g., a NEAR transfer out of the multisig account) can be executed with fewer live, authorized confirmations than the configured threshold. This is a critical-severity issue matching "a multisig request executed below threshold," and can result in NEAR being moved by a party (or set of parties) not entitled to authorize it under the current membership.

## Likelihood Explanation
This does not require any privileged access beyond being (or having been) a legitimate multisig member at some point — no foundation, owner, or external attacker key is needed, only ordinary use of the multisig's own `add_request`/`confirm`/`DeleteMember` flow. It requires a specific but realistic sequence: a request is confirmed by a member before that member is later removed, and the request is left open long enough to accumulate the remaining confirmations after removal. Multisig membership changes are a normal operational event, and requests can remain open (subject to the 15-minute `REQUEST_COOLDOWN`) while other confirmations trickle in, making this a plausible sequence in real usage rather than a purely theoretical one.

## Recommendation
When executing `DeleteMember`, iterate all entries in `self.confirmations` and remove the deleted member's string from every confirmation set (not just requests the member created), or alternatively re-validate at `confirm()`-execution time that every entry in the confirmations set corresponds to a currently-live member before comparing against `num_confirmations`.

## Proof of Concept
1. Initialize `multisig2` with `members = [A, B, C, D]`, `num_confirmations = 3`.
2. `A` calls `add_request(R1 = Transfer{amount, receiver_id: attacker})`.
3. `B` calls `confirm(R1)` → `confirmations(R1) = {B}`.
4. Separately, `A` (with `C`/`D`) executes a `DeleteMember{member: B}` request that reaches threshold and runs — `B` is removed from `self.members`; R1 (created by `A`, not `B`) is untouched, and `confirmations(R1)` still contains `B`.
5. `A` calls `confirm(R1)` → `confirmations(R1) = {B, A}` (len 2).
6. `C` calls `confirm(R1)` → `confirmations(R1).len() == 3 >= num_confirmations` → `execute_request` runs the `Transfer`, moving funds out of the multisig even though only `A` and `C` are actually live members who confirmed; `B`'s confirmation, from a now-removed account, was counted toward the threshold. [1](#0-0) [2](#0-1)

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

**File:** multisig2/src/lib.rs (L406-423)
```rust
    /// Prevents access to calling requests and make sure request_id is valid - used in delete and confirm
    fn assert_valid_request(&mut self, request_id: RequestId) {
        // request must come from key added to contract account
        assert(
            self.current_member().is_some(),
            "Caller (predecessor or signer) is not a member of this multisig",
        );
        // request must exist
        assert(
            self.requests.get(&request_id).is_some(),
            "No such request: either wrong number or already confirmed",
        );
        // request must have
        assert(
            self.confirmations.get(&request_id).is_some(),
            "Internal error: confirmations mismatch requests",
        );
    }
```
