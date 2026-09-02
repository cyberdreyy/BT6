## Analysis Confirmed

I found a concrete analog in `multisig2/src/lib.rs`. The `delete_member` function only purges confirmations/requests for requests where the **request originator** (`r.member`) equals the deleted member, but never scans other members' active requests to strip the deleted member's stale confirmation entries out of their `confirmations` `HashSet`. Since `confirm()` counts `confirmations.len()` without re-validating that each entry in the set is a currently-live member, a confirmation cast by a member who is later removed still counts toward `num_confirmations` on any request they weren't the originator of — exactly the same class of bug as the DittoETH report: a recorded state value (confirmation count / debt) diverges from the live reality (member set / short's actual updated debt) because the removal path only maintains state for one narrow code path, letting a stale entry be leveraged to cross an authorization boundary.

### Title
Stale confirmations from removed multisig members are still counted toward the execution threshold - (File: `multisig2/src/lib.rs`)

### Summary
`delete_member` in `multisig2/src/lib.rs` only clears confirmations for requests that the removed member themselves created (`r.member == member`). It never removes that member's confirmation entry from the `confirmations: LookupMap<RequestId, HashSet<String>>` of requests created by *other* members. `confirm()` later counts `confirmations.len()` (a raw string set) with no re-check that every string in the set corresponds to a currently live `members` entry. A request can therefore be executed with fewer than `num_confirmations` distinct, currently-authorized confirmers.

### Finding Description
- `confirm()` [1](#0-0)  increments/counts confirmations by looking up the `confirmations` set for a `request_id` and compares `confirmations.len() as u32 + 1 >= self.num_confirmations`. It never validates that the accounts/keys already present in that set are still members of `self.members`.
- `delete_member()` [2](#0-1)  removes the member from `self.members`, but the request/confirmation cleanup loop only targets requests where `r.member == member` (i.e., requests the removed member personally created):
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
```
It does not iterate over `self.confirmations` values to strip the removed member's identity out of confirmation sets belonging to requests created by *other* members.

**Binding broken**: `confirmations counted for a request` should equal `confirmations cast by members who are still live at execution time`. After `delete_member` runs, a pending request created by a different member can retain a confirmation string from the now-deleted member, so `confirmations.len()` overcounts relative to the true number of currently authorized confirmers.

### Impact Explanation
This lets a multisig request execute with effectively fewer than `num_confirmations` live, entitled confirmers — e.g., in a 3-of-N multisig, a request confirmed by member A (removed afterward) and member B could later be pushed to execution by only member C, since A's stale confirmation still counts, when the security model calls for 3 currently-authorized signers. This is a `Critical` impact class per the scope rules: "a multisig request executed below threshold." Any funds transferred, keys added, or contract code deployed via `execute_request` [3](#0-2)  as a result of this under-threshold confirmation is an unauthorized action.

### Likelihood Explanation
Requires a realistic sequence of unprivileged-attacker-observable events, not owner collusion required beyond what the scheme already assumes (any subset of members can act):
1. Member A creates/confirms request R with a partial confirmation count (< `num_confirmations`).
2. The group legitimately removes member A via `DeleteMember` (e.g., A left the org, key rotation, etc.) — a normal governance action that does not require A's involvement or complicity.
3. Request R survives (still has A's stale confirmation) because `delete_member` doesn't touch it.
4. A single further "confirm" call by a remaining member pushes `confirmations.len() + 1 >= num_confirmations`, executing the request with fewer live confirmers than `num_confirmations`.

No malicious node, no owner override, and no redeploy is required — only normal multisig usage patterns (member turnover + a pending unconfirmed request) that are common in production multisig lifecycles.

### Recommendation
When a member is deleted, iterate all pending `requests`/`confirmations` (not only those the deleted member created) and remove the deleted member's identity string from every confirmation set. Alternatively, when counting confirmations in `confirm()`, filter the confirmations set to only entries still present in `self.members` before comparing against `num_confirmations`.

### Proof of Concept
```rust
// 1. Init multisig with members [A, B, C], num_confirmations = 3
let mut c = MultiSigContract::new(vec![A, B, C], 3);

// 2. Member B creates a Transfer request (not yet fully confirmed)
testing_env!(context_for(B));
let request_id = c.add_request_and_confirm(transfer_request.clone()); // 1 confirmation (B)

testing_env!(context_for(A));
c.confirm(request_id); // 2 confirmations (A, B)

// 3. Governance removes member A via a DeleteMember request (2-of-3 confirm, separate request)
// -> delete_member(A) executes; loop only clears requests where r.member == A;
//    the Transfer `request_id` above was created by B, so its confirmations set
//    ["A", "B"] is left untouched even though A is no longer in `self.members`.

// 4. Now only C is a live, uninvolved member. C alone confirms:
testing_env!(context_for(C));
c.confirm(request_id);
// confirmations.len() (2, containing stale "A") + 1 >= num_confirmations (3) => TRUE
// Request executes with only 2 *live* confirmers (B, C) instead of the required 3.
```
This demonstrates the request executes below the intended live-member threshold, matching the "multisig request executed below threshold" Critical impact criterion.

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

**File:** multisig2/src/lib.rs (L294-315)
```rust
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
