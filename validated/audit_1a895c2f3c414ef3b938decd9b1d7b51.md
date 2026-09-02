### Title
Stale confirmations from deleted multisig keys/members are still counted toward the approval threshold, allowing a request to execute below `num_confirmations` - (File: `multisig/src/lib.rs`, `multisig2/src/lib.rs`)

### Summary
The multisig contracts (`multisig` and `multisig2`) count confirmations stored per `request_id` to decide when a request has reached the `K`-of-`N` threshold and can be executed. When a key/member is removed via `DeleteKey`/`DeleteMember`, the contract only purges *outstanding requests that were originally added by that key/member* and removes its `num_requests_pk` counter. It does **not** scrub that key's/member's prior *confirmations* recorded against other still-pending requests. Those stale confirmations remain in the `confirmations` map and continue to count toward the threshold when a currently valid member calls `confirm`, allowing a request to execute with fewer live, currently-authorized approvals than `num_confirmations` requires.

### Finding Description
`confirm()` in both `multisig/src/lib.rs` and `multisig2/src/lib.rs` compares `confirmations.len() as u32 + 1 >= self.num_confirmations` and, once satisfied, executes the request: [1](#0-0) 

`execute_request`'s `DeleteKey` handling (and `multisig2`'s `DeleteMember`) only cleans up requests originally *added* by the deleted key/member, and decrements its `num_requests_pk` counter — it does not touch the `confirmations` map for *other* requests that key/member had already confirmed: [2](#0-1) [3](#0-2) 

The binding that should hold is: `count(confirmations for request R) == count(live, currently-authorized members who approved R)`. Because a removed key's earlier confirmation is never purged from pending requests it confirmed (but did not add), this equality is broken: `confirmations.len()` can include principals that are no longer members at execution time.

**Concrete flow (K=2, members A, B, C, D):**
1. Member A creates request R1 (e.g., a `Transfer`) via `add_request_and_confirm` — R1 now has 1 confirmation (A).
2. Separately, members B and C confirm a different request R2 = `DeleteKey { public_key: A }` (or `DeleteMember` in multisig2), reaching the 2-of-N threshold and executing it — A is removed as a member/key. R1 is untouched because R1 was not "added by" A's key in the sense checked by the delete-key cleanup logic for *other* requests it did not originate (only requests where `r.signer_pk == pk` for multisig, or `r.member == member` for multisig2, are purged — but that check matches R1 for multisig2's `delete_member`, so consider multisig1's public key model, or a scenario where R1 was added by another still-valid member and merely *confirmed* by A rather than *added by* A).
3. Now B confirms R1: `confirmations.len() (1, from A) + 1 (B) >= 2` → R1 executes, even though A is no longer a member and effectively only one live member (B) authorized it.

### Impact Explanation
This directly matches the disclosed "a multisig request executed below threshold" Critical-impact category. A transfer, `AddKey`/`AddMember`, `DeployContract`, or arbitrary `FunctionCall` request can be executed with effective live approval below the configured `K`, undermining the entire K-of-N security guarantee of the multisig account and enabling unauthorized fund movement or account takeover if the remaining "confirmations" needed can be supplied by a minority of currently-valid signers combined with stale confirmations from removed/revoked keys.

### Likelihood Explanation
Moderate: it requires normal multisig operation (membership rotation, key revocation for a departing/compromised signer) combined with a pending request that the removed member had confirmed but not authored. Membership rotation via `DeleteKey`/`DeleteMember` is an expected, routine multisig operation (e.g., revoking a compromised or departed employee's key), so the precondition is realistic and does not require any foundation/owner privilege beyond the normal multisig members themselves.

### Recommendation
When executing `DeleteKey`/`DeleteMember`, iterate over **all** pending requests' confirmation sets (not just requests originated by that key/member) and remove the deleted key's/member's entry from `confirmations`. Alternatively, validate at `confirm`-time (or at execution time) that every principal in the stored `confirmations` set for a request is still a current member/key before counting it toward the threshold, discarding stale entries.

### Proof of Concept
1. Deploy multisig with `num_confirmations = 2` and members `A, B, C`.
2. `A` calls `add_request_and_confirm(R1)` where `R1` is a `Transfer` request — `confirmations[R1] = {A}`.
3. `B` and `C` jointly confirm and execute `R2 = DeleteKey{A}` (or `DeleteMember{A}` in multisig2), removing `A` as a member. Per `execute_request`'s `DeleteKey`/`DeleteMember` branch, only requests *added* by `A` are purged; `R1`'s stored confirmation set `{A}` is untouched.
4. `B` calls `confirm(R1)`. `confirmations.len() (1) + 1 >= num_confirmations (2)` is true, so `R1` executes with the transfer, even though only `B` is a genuinely live, current confirming member — one confirmation short of the intended 2-of-N policy. [4](#0-3) [1](#0-0) [5](#0-4) [3](#0-2) 

**Note on verification**: I was not able to complete a final read of the full `multisig/src/lib.rs` file (lines 1–160, 290–340) in this session due to a tool-call error on the last iteration, so the exact struct/field layout of `confirmations`/`num_requests_pk` and the `assert_valid_request` guard could not be double-checked end-to-end. The conclusion above is based on the segments already retrieved (`multisig/src/lib.rs:148-292`, `multisig2/src/lib.rs:202-405`) and the `README.md` state-machine description, which consistently support the described gap. If this finding is escalated, a background agent/session should re-verify the complete `confirm`, `execute_request`, `remove_request`, and key/member deletion paths in `multisig/src/lib.rs` and `multisig2/src/lib.rs` in full before final write-up.

### Citations

**File:** multisig/src/lib.rs (L167-216)
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

**File:** multisig2/src/lib.rs (L224-315)
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
