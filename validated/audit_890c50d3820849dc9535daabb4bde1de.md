Found a concrete, in-scope vulnerability class match in `multisig2/src/lib.rs`: stale confirmations from removed multisig members are still counted toward the confirmation threshold, letting a request execute with fewer *live* confirming members than `num_confirmations` requires — a direct match to "a multisig request executed below threshold."

### Title
Multisig request can execute below the confirmation threshold because stale confirmations from removed members are still counted - (File: multisig2/src/lib.rs)

### Summary
`MultiSigContract::delete_member` only purges pending requests that the removed member *originated* (`r.member == member`), but it never removes that member's confirmations recorded on requests originated by *other* members. `confirm()` later counts these stale confirmations toward `num_confirmations` without checking whether the confirming identity is still a live member, so a request can be executed with fewer than `num_confirmations` currently-active members having actually confirmed it.

### Finding Description
`confirm()` reads the `confirmations` set for a request and executes once `confirmations.len() as u32 + 1 >= self.num_confirmations`: [1](#0-0) 

`delete_member` is the only cleanup path invoked when a member is removed (via the `DeleteMember` request action executed in `execute_request`): [2](#0-1) 

It filters `self.requests` for entries where `r.member == member` — i.e., requests the removed member *created* — and clears confirmations only for those. It does not scan `self.confirmations` for entries where the removed member appears as a *confirmer* on requests created by someone else. Those stale confirmations remain stored under `member.to_string()` in the `confirmations: LookupMap<RequestId, HashSet<String>>` map: [3](#0-2) 

`assert_valid_request`, called from `confirm`, only validates that the *current caller* is a live member; it does not revalidate the previously stored confirmations set against the current member list: [4](#0-3) 

The binding that should hold is: `count(confirmations ∩ live_members) >= num_confirmations` before a request executes. Because stale confirmations from removed members are never purged from requests they didn't originate, the actual check degrades to `count(confirmations_ever_recorded) >= num_confirmations`, which can be satisfied while `count(confirmations ∩ live_members) < num_confirmations`.

### Impact Explanation
This directly matches the Critical impact category "a multisig request executed below threshold." A `Transfer`, `FunctionCall`, `AddKey`, or other privileged `MultiSigRequestAction` (executed via `execute_request`) can be pushed through with confirmations from fewer currently-authorized members than `num_confirmations` mandates, because one confirmer of record was removed from the multisig before the request finished collecting real, live confirmations: [5](#0-4) 

### Likelihood Explanation
This requires no compromise of any key — only the ordinary sequence of: (a) a member confirms a pending request, (b) that member is later removed via a legitimate `DeleteMember` execution (a routine operational action, e.g., off-boarding), and (c) the remaining members continue confirming the still-pending request, unaware their confirmation set still silently contains the departed member's stale entry. Any multisig that rotates members while other requests are pending is exposed; no attacker-controlled deployment parameters or ignored initialization are needed.

### Recommendation
When executing `DeleteMember`, iterate all entries in `self.confirmations` (not only requests originated by the removed member) and strip the removed member's identity string from every confirmation `HashSet`. Alternatively, when `confirm()` computes the count for the threshold check, filter the stored confirmation strings against the current `self.members` set before comparing against `num_confirmations`, so only live members' confirmations count.

### Proof of Concept
1. Deploy `MultiSigContract::new` with members `{A, B, C, D}` and `num_confirmations = 3`.
2. `A` calls `add_request_and_confirm(R)` for a `Transfer` request → `confirmations[R] = {A}`.
3. `B` calls `confirm(R)` → `1 + 1 = 2 < 3`, so it just records → `confirmations[R] = {A, B}`.
4. Separately, an unrelated request `DeleteMember{B}` is created and reaches 3 confirmations from `A, C, D` and executes via `delete_member`, which removes `B` from `self.members` and deletes access key, but only purges requests where `r.member == B` (requests `B` originated) — `R` was originated by `A`, so it is untouched and `confirmations[R]` still contains `"B"`.
5. `C` calls `confirm(R)`: current `confirmations[R].len() == 2` (`{A, B}`), check `2 + 1 = 3 >= 3` → executes `R`'s `Transfer`.
6. Result: `R` executed with confirmations attributed to `A`, `B` (removed, non-member), `C` — only 2 of the 3 counted confirmers (`A`, `C`) are actually live members at execution time, violating the `num_confirmations = 3` live-member threshold guarantee. [6](#0-5) [7](#0-6)

### Citations

**File:** multisig2/src/lib.rs (L108-133)
```rust
#[derive(BorshStorageKey, BorshSerialize)]
pub enum StorageKeys {
    Members,
    Requests,
    Confirmations,
    NumRequestsPk,
}

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
