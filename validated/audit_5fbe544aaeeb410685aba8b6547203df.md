### Title
Stale confirmations from removed multisig members still count toward the approval threshold, allowing requests to execute below the live-member threshold - (File: multisig2/src/lib.rs)

### Summary
`MultiSigContract::confirm` in [1](#0-0)  authorizes execution purely by counting entries already stored in the `confirmations` `HashSet` for a request plus the new caller, without re-validating that the previously stored confirmers are still current multisig `members`. `delete_member` in [2](#0-1)  only purges pending requests that were *authored* by the removed member (`r.member == member`); it does not scrub that member's confirmations from other pending requests that they merely confirmed. As a result, a removed member's stale confirmation remains counted toward the `num_confirmations` threshold, letting a request execute with fewer live, currently-authorized confirmations than the configured threshold requires.

### Finding Description
The binding the multisig is supposed to enforce is:
```
confirmations_counted_toward_threshold == confirmations_from_current_live_members
```
Steps that break this equality:
1. A member `A` calls `add_request` to create request `R` (a `Transfer`). `add_request` records `A` as the `member` (author) in `MultiSigRequestWithSigner`: [3](#0-2) .
2. A different member `B` calls `confirm(R)`. Since `confirmations.len() + 1 < num_confirmations`, `B`'s identity is inserted into the `confirmations` set for `R`: [1](#0-0) .
3. Through a separate, properly-confirmed governance request, the remaining members execute `DeleteMember { member: B }`. `delete_member` only removes pending requests where `r.member == B`, i.e., requests *authored* by `B`. Request `R` was authored by `A`, so it is untouched, and `B`'s entry inside `R`'s `confirmations` set is never removed: [2](#0-1) . `B` is now removed from `self.members`, so `current_member()` will return `None` for `B`, and `B` can no longer call `confirm` itself: [4](#0-3) .
4. Remaining live members `C` and `D` each call `confirm(R)`. `assert_valid_request` only checks that the *caller* is a current member; it never checks whether previously stored confirmers are still current members: [5](#0-4) . The stale `B` entry is still present, so `confirmations.len()` already starts at 1 (from `B`). When `C` confirms, the set becomes `{B, C}` (len 2, still short of a 3-of-N threshold). When `D` confirms, `confirmations.len() + 1 == 3 >= num_confirmations`, and `execute_request` runs the `Transfer` (or any other bundled actions): [6](#0-5) .

The request is executed on only 2 genuinely live, currently-authorized confirmations (`C`, `D`) plus one stale confirmation from a member (`B`) who has already been revoked — i.e., strictly fewer live confirmations than `num_confirmations` requires. `execute_request` performs no additional re-validation of confirmer identities against `self.members` before dispatching the promise.

### Impact Explanation
This directly matches the Critical impact category "a multisig request executed below threshold." An attacker scenario: a departing/compromised member `B` confirms a high-value `Transfer` (or `AddKey`/`FunctionCall`) request just before being removed for cause. Even though `B` is later revoked, their stale confirmation persists and only 1 additional live confirmation (instead of the full remaining quorum) is needed to push the request past the threshold and move funds or grant access — effectively bypassing the K-of-N guarantee the multisig is meant to provide.

### Likelihood Explanation
This requires only ordinary multisig operations already exposed to any member: `add_request`, `confirm`, and `DeleteMember`. It is triggerable by any unprivileged multisig member without requiring the foundation, a redeploy, or any external actor — a revoked member's earlier honest (or malicious) confirmation on an unrelated pending request continues to silently count toward quorum on that request. The sequence (author submits a request, some other member confirms it, that confirming member is later removed, then remaining members confirm) is a realistic and easily reachable operational sequence for any active multisig account.

### Recommendation
When a member is deleted via `delete_member`, iterate all pending requests' `confirmations` sets (not just requests they authored) and remove that member's entry, decrementing effective progress if necessary. Alternatively, when counting confirmations in `confirm()`, filter the stored `confirmations` set to only those entries that are still present in `self.members` before comparing against `num_confirmations`, so removed members can never contribute to reaching the quorum.

### Proof of Concept
```rust
// members: A, B, C, D ; num_confirmations = 3
let mut c = MultiSigContract::new(vec![A, B, C, D], 3);

// 1. A authors a Transfer request R (not auto-confirmed)
set_caller(A);
let r = c.add_request(MultiSigRequest { receiver_id: victim, actions: vec![Transfer{ amount }] });

// 2. B confirms R -> confirmations(R) = {B}
set_caller(B);
c.confirm(r);

// 3. Separately, members reach quorum to remove B
//    (delete_member only purges requests AUTHORED by B; R was authored by A, so it's untouched)
set_caller(A); let del_req = c.add_request(DeleteMember{ member: B });
set_caller(C); c.confirm(del_req);
set_caller(D); c.confirm(del_req); // executes DeleteMember{B}; B removed from members
// confirmations(R) still == {B}

// 4. Only two LIVE members need to confirm R to hit num_confirmations=3
set_caller(C);
c.confirm(r); // confirmations(R) = {B, C}, len 2 < 3
set_caller(D);
c.confirm(r); // len(confirmations)+1 == 3 >= 3 -> executes Transfer
// Executed with only 2 live confirmations (C, D) instead of the required 3.
```

### Citations

**File:** multisig2/src/lib.rs (L169-200)
```rust
    /// Add request for multisig.
    pub fn add_request(&mut self, request: MultiSigRequest) -> RequestId {
        let current_member = self.current_member().unwrap_or_else(|| {
            env::panic_str(
                "Predecessor must be a member or transaction signed with key of given account",
            )
        });
        // track how many requests this key has made
        let num_requests = self
            .num_requests_pk
            .get(&current_member.to_string())
            .unwrap_or(0)
            + 1;
        assert(
            num_requests <= self.active_requests_limit,
            "Account has too many active requests. Confirm or delete some.",
        );
        self.num_requests_pk
            .insert(&current_member.to_string(), &num_requests);
        // add the request
        let request_added = MultiSigRequestWithSigner {
            member: current_member,
            added_timestamp: env::block_timestamp(),
            request,
        };
        self.requests.insert(&self.request_nonce, &request_added);
        let confirmations = HashSet::new();
        self.confirmations
            .insert(&self.request_nonce, &confirmations);
        self.request_nonce += 1;
        self.request_nonce - 1
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

**File:** multisig2/src/lib.rs (L321-339)
```rust
    /// Returns current member: either predecessor as account or if it's the same as current account - signer.
    fn current_member(&self) -> Option<MultisigMember> {
        let member = if env::current_account_id() == env::predecessor_account_id() {
            MultisigMember::AccessKey {
                public_key: env::signer_account_pk()
                    .try_into()
                    .unwrap_or_else(|_| env::panic_str("Failed to deserialize public key")),
            }
        } else {
            MultisigMember::Account {
                account_id: env::predecessor_account_id(),
            }
        };
        if self.members.contains(&member) {
            Some(member)
        } else {
            None
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
