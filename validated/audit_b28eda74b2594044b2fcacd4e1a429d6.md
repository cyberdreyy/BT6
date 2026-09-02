### Title
Multisig `DeleteMember`/`DeleteKey` leaves stale confirmations from removed members counted toward the K-of-N quorum, allowing execution below threshold - (File: multisig2/src/lib.rs, multisig/src/lib.rs)

### Summary
`MultiSigContract::confirm` counts entries already stored in `self.confirmations[request_id]` toward `num_confirmations` without re-validating that every prior confirmer is still a current member. `delete_member` (multisig2) and the `DeleteKey` action (multisig) only purge requests *created by* the removed member/key and never scan-and-strip that member's confirmations off *other* pending requests. A confirmation cast by a member who is later removed therefore keeps counting, letting a request execute with fewer live, currently-authorized approvals than `num_confirmations` requires.

### Finding Description
`confirm()` only checks that the caller hasn't already confirmed and that the caller is a current member (`current_member()` panics if not found in `self.members`); it never re-validates the *existing* confirmers stored in the `confirmations` `HashSet` for that request: [1](#0-0) 

`delete_member` only removes pending requests where the removed member is the *creator* (`r.member == member`), and only clears `num_requests_pk`/access key — it does not touch `confirmations` entries left by that member on requests created by *other* members: [2](#0-1) 

The same gap exists in the original `multisig` contract's `DeleteKey` action, which filters outstanding requests by `signer_pk` (the request creator) only, leaving that key's confirmations on other requests untouched: [3](#0-2) 

Because `self.confirmations` is a plain set of member identifiers per request, once a member's confirmation is recorded it is permanent storage state independent of current membership. The quorum check `confirmations.len() as u32 + 1 >= self.num_confirmations` treats a stale confirmation from a now-removed member identically to a confirmation from a currently trusted member.

This breaks the intended custody binding of the K-of-N scheme: `confirmations counted == live members who approved`. After a member is removed, that equality no longer holds for requests pending before the removal — `confirmations counted > live members who approved`.

### Impact Explanation
This allows a multisig-controlled account (holding NEAR, keys, or contract deploy authority) to execute a `Transfer`, `AddKey`/`AddMember`/`DeleteMember`, `FunctionCall`, or `DeployContract` action with fewer genuinely-current approvals than the configured `num_confirmations` threshold. This is exactly the "multisig request executed below threshold" Critical impact category: funds can be moved, keys added, or contract code redeployed by a smaller live coalition than the K-of-N policy requires, because a stale/removed member's earlier vote still counts.

### Likelihood Explanation
The path requires no external privilege escalation beyond normal multisig operations: (1) a request is created and partially confirmed, (2) one of its confirmers is later removed via a legitimate `DeleteMember`/`DeleteKey` action (e.g. rotating out a departing member or revoking a compromised key — the exact scenario a multisig is meant to defend against), and (3) any remaining member supplies the final confirmation. No special conditions or timing constraints are needed beyond the request still being pending when the removal happens, which is realistic given `REQUEST_COOLDOWN` and the default 12-active-request limit per member giving ample window.

### Recommendation
When removing a member/key (`delete_member` in multisig2, the `DeleteKey` action in multisig), iterate over **all** pending requests' confirmation sets (not just requests created by that member) and strip the removed member's confirmation entry from each, re-checking whether the remaining confirmation count still satisfies invariants. Alternatively, validate at `confirm()` time that every stored confirmer identifier in `confirmations` is still present in `self.members` before counting it toward the threshold (filtering out stale entries lazily), and persist the filtered set back to storage.

### Proof of Concept
Using `multisig2`:
1. `new(members = [A, B, C, D], num_confirmations = 3)`.
2. `A` calls `add_request_and_confirm(request_X)` → `confirmations[X] = {A}`.
3. `B` calls `confirm(request_X)` → `confirmations[X] = {A, B}` (len 1 + 1 = 2 < 3, not yet executed).
4. Members `A, C, D` create and confirm a `DeleteMember{member: B}` request, executed via `delete_member` — `B` is removed from `self.members` and its access key revoked. Because `request_X.member == A` (not `B`), the loop in `delete_member` (`multisig2/src/lib.rs:361-371`) does not touch `confirmations[X]`, which still equals `{A, B}`.
5. `C` (a genuinely current member) calls `confirm(request_X)`. `confirmations.len() == 2` (`A, B`), `+1 == 3 >= num_confirmations` → `execute_request` runs `request_X`, e.g. transferring funds.

Result: `request_X` executes with only `A` and `C` being current, live approving members, yet the contract treats it as a full 3-of-4 quorum because `B`'s stale confirmation was never invalidated after removal — confirming the "confirmations counted vs. live members" binding is broken and a request can execute below the intended threshold.

### Citations

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
