### Title
Multisig confirmations from a deleted key/member remain counted toward the confirmation threshold, allowing requests to execute with fewer live signers than `num_confirmations` - (File: multisig/src/lib.rs, multisig2/src/lib.rs)

### Summary
When a multisig key (v1) or member (v2) is removed via `DeleteKey`/`DeleteMember`, the contract only purges confirmations and pending requests that the removed key **created**, but leaves intact any confirmations that key added to **other** still-pending requests created by someone else. Those stale confirmations continue to count toward `num_confirmations`, so a request can later be executed with fewer than K live signers actually agreeing — breaking the "K confirmations from K live keys" custody binding the multisig is supposed to enforce.

### Finding Description
`MultiSigContract::confirm` only checks whether the *current* number of stored confirmations for a request has reached `num_confirmations`; it never re-validates that every public key/member in that stored confirmation set is still an active multisig member: [1](#0-0) 

When a key is deleted through the `DeleteKey` request action, the cleanup logic only removes requests and confirmations for requests **created by** that key (`r.signer_pk == pk`). It does not scan `self.confirmations` for entries where the deleted key appears as a *confirmer* on requests created by other signers: [2](#0-1) 

The same pattern exists in the newer `multisig2` contract: `delete_member` removes requests created by the removed member and their `num_requests_pk` entry, but does not strip that member's confirmations from other outstanding requests: [3](#0-2) 

`confirm` in `multisig2` has the same threshold check against the raw stored confirmation count, with no liveness check of the confirmers already recorded: [4](#0-3) 

The binding the contract is supposed to guarantee is:
`count(confirmations for request R) == count(distinct LIVE members who confirmed R)`.

Once a member is deleted, this equality breaks: the stored confirmation count still includes the removed member, but the set of live members confirming is now one smaller. A request that legitimately needed K live confirmations can execute with K-1 live confirmations plus one confirmation from a key/account that is no longer part of the multisig.

### Impact Explanation
This matches the "Critical - a multisig request executed below threshold" impact bucket. An attacker who is (or was) a multisig member can confirm an arbitrary pending request (e.g., a `Transfer` or `AddKey` request) and later be removed from the multisig (e.g., after key rotation, compromise remediation, or a legitimate offboarding). Their earlier confirmation is never invalidated, so it still counts. If the remaining active members later add their confirmations, the request executes even though only `num_confirmations - 1` currently-authorized members actually approved it, moving funds or granting access with a lower effective threshold than configured.

### Likelihood Explanation
This requires no privileged capability beyond being (or having been) an ordinary multisig member/key-holder — a role explicitly designed to be one of several co-equal signers, not a trusted admin over the whole system. It is realistic: key rotation/removal of members is an expected, routine multisig operation, and any pending request that received a confirmation before a member removal is silently left with a stale, still-valid confirmation. No malicious node, redeploy, or social engineering is needed — only ordinary use of `confirm`, `DeleteKey`/`DeleteMember`, and eventually reaching the (now effectively-lowered) threshold on an existing request.

### Recommendation
When executing `DeleteKey` (v1) or `DeleteMember` (v2), iterate over **all** pending requests' confirmation sets (not just those created by the removed key/member) and strip out the removed identity's confirmation, decrementing the effective count. Alternatively, validate at `confirm`-time (and at execution time) that every entry in `confirmations` for the request still corresponds to a currently active member before counting toward `num_confirmations`.

### Proof of Concept
1. Initialize a multisig with 3 keys/members `A`, `B`, `C` and `num_confirmations = 2`.
2. `B` calls `add_request` to create a `Transfer` request `R` (receiver = attacker-controlled account).
3. `A` calls `confirm(R)` — one confirmation recorded (1/2), request not yet executed (`multisig/src/lib.rs` `confirm`, lines 246-266).
4. Members submit and confirm a separate `DeleteKey`/`DeleteMember` request removing `A` (e.g., legitimate key rotation) — `A`'s own created requests/confirmations are purged, but `R`'s stored confirmation set still contains `A` (`multisig/src/lib.rs` `execute_request` `DeleteKey` branch, lines 198-216; `multisig2/src/lib.rs` `delete_member`, lines 355-379).
5. `C` (now one of only 2 remaining live members) calls `confirm(R)`. `confirmations.len() + 1 == 2 >= num_confirmations`, so `R` executes with only `C` currently a live authorized signer, even though the configured policy requires 2-of-3 *live* members to agree. [5](#0-4) [1](#0-0)

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
