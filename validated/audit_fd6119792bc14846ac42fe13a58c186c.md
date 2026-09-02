### Title
Removed multisig member's stale confirmation still counts toward quorum, allowing request execution below the configured threshold - (File: multisig2/src/lib.rs)

### Summary
In `multisig2/src/lib.rs`, `delete_member` removes a member from `self.members` but does not scrub that member's confirmation entries from the `confirmations` map of *other* still-pending requests. Since `confirm()` only checks the count of stored confirmation strings against `num_confirmations`, without verifying that every confirming party is still a current member, a stale confirmation left behind by a removed member is counted as if it were a valid, live approval.

### Finding Description
`confirm()` accepts a request if `confirmations.len() as u32 + 1 >= self.num_confirmations`, purely counting entries already stored in the `confirmations: LookupMap<RequestId, HashSet<String>>` map for that request: [1](#0-0) 

`delete_member` is the only place that mutates `self.members` and `confirmations`/`requests` on member removal, but it only purges requests that were *created* by the removed member (`r.member == member`) — it does not iterate over other requests' confirmation sets to strip the removed member's prior confirmation: [2](#0-1) 

Consequently, if member `M` confirms a request `R` created by someone else, and `M` is later removed via a legitimate `DeleteMember` action (e.g., because `M`'s key is suspected compromised, or `M` is simply rotated out), `M`'s confirmation string remains inside `R`'s `HashSet<String>` in the `confirmations` map. When a *live* member later confirms `R`, the stale entry from the now-removed `M` is still counted toward `num_confirmations`, so `R` can execute with fewer genuinely live/current confirmations than the configured threshold requires.

This breaks the intended custody binding: `confirmations counted == live members who approved`. After the bug, `confirmations counted > live members who approved`, letting a request pass with fewer than `num_confirmations` currently-trusted parties.

### Impact Explanation
This is a **Critical** issue per the given impact taxonomy — "a multisig request executed below threshold." `execute_request` can perform `Transfer`, `DeployContract`, `AddKey` (including full-access keys), and `FunctionCall` actions on behalf of the multisig account: [3](#0-2) 

If a member is removed (e.g., for being compromised) after having confirmed a still-pending malicious `Transfer` or `AddKey` request, that stale confirmation persists and can be combined with fewer live approvals to push the malicious request through — moving NEAR out of the multisig account, or granting a full-access key, without the number of currently-authorized signers actually reaching `num_confirmations`.

### Likelihood Explanation
The scenario requires: (1) a request pending confirmation created by one member and confirmed by another, (2) that confirming member subsequently being removed via `DeleteMember` (a routine, expected operational event — e.g., rotating out a suspected-compromised key), and (3) the remaining live members later completing confirmation of the still-pending request. None of these steps require exploiting any access-control bypass beyond ordinary multisig operations already exposed to members; the root cause is a missing cleanup step in `delete_member`, making this readily reachable in normal governance workflows (particularly the exact scenario the removal is meant to protect against: revoking a compromised signer).

### Recommendation
In `delete_member`, before removing the member from `self.members`, iterate over all entries in `self.requests`/`self.confirmations` and remove the deleted member's `to_string()` key from every request's confirmation `HashSet`, not just from requests the member itself created. Alternatively, validate in `confirm()` that every entry in the stored confirmation set still corresponds to a current member before counting it toward the threshold.

### Proof of Concept
1. Initialize `MultiSigContract::new([A, B, C, D], 3)` (`num_confirmations = 3`).
2. Member `A` calls `add_request` with a `Transfer` action to an attacker-controlled account, producing `request_id = R`.
3. Member `B` calls `confirm(R)` → `confirmations[R] = {A, B}` (2/3, not yet executed): [4](#0-3) 
4. Separately, the remaining members execute a `DeleteMember { member: B }` request (3 confirmations from A, C, D) because `B`'s key is suspected compromised. `delete_member` removes `B` from `self.members` and only deletes requests *created by* `B`; `R` (created by `A`) is untouched and still contains `B`'s confirmation: [5](#0-4) 
5. Members = `{A, C, D}`, `num_confirmations` is still `3`.
6. Member `C` calls `confirm(R)`. `confirmations[R].len() == 2` (stale `A`, `B`), so `2 + 1 >= 3` is true, and the `Transfer` executes — even though only `A` and `C` are actually live, currently-trusted members who approved it (2 of 3 required, with the third being a revoked/compromised party's stale approval).

Note: I was unable to fully compare this against the older `multisig/src/lib.rs` (key-based only) contract's `delete_key`/`remove_request` logic within the available iterations to confirm whether it shares or avoids this exact defect; the analysis above is limited to `multisig2/src/lib.rs`, which was fully read and verified.

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
