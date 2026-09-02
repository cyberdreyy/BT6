### Title
Stale confirmations from removed multisig members are not purged, allowing requests to execute below the live-member confirmation threshold - ([File: multisig2/src/lib.rs], [File: multisig/src/lib.rs])

### Summary
The multisig contracts (`multisig` and `multisig2`) never scrub a removed member's *confirmations on other members' pending requests* when that member is deleted. Only requests that the removed member itself created are cleaned up. As a result, a confirmation recorded by a member who has since been removed still counts toward `num_confirmations` when a later `confirm()` call is evaluated, letting a request execute with fewer genuinely live confirmations than the configured threshold.

### Finding Description
`delete_member` (multisig2) only removes requests whose *original requester* (`r.member`) equals the member being deleted: [1](#0-0) 

It filters `self.requests` for `r.member == member` and clears the confirmations/requests for those, but it does **not** scan `self.confirmations` for entries where the deleted member appears merely as a *confirmer* on someone else's still-pending request. Those `confirmations` `HashSet<String>` entries are left untouched.

The legacy `multisig` contract has the identical gap in its `DeleteKey` action handler, which filters `requests` by `r.signer_pk == pk` (i.e. only requests the deleted key created) and likewise never purges that key's confirmation entries from other requests: [2](#0-1) 

`confirm()` then blindly trusts the size of the stored confirmation set against the *current* `num_confirmations` without verifying that every entry in that set is still a current member: [3](#0-2) [4](#0-3) 

The binding that should hold is:
```
confirmations.len() (recorded) == confirmations from members that are still in self.members (live)
```
This equality is broken the moment a member who has confirmed (but not created) a still-pending request is deleted — the recorded count no longer reflects live membership, yet `confirm()` uses it directly to decide whether to execute.

### Impact Explanation
This is a "multisig request executed below threshold" scenario (Critical per the rules): a transfer, `FunctionCall`, `AddKey`/`AddMember`, or `DeployContract` request can be executed with only `num_confirmations - 1` (or fewer) *currently authorized* signers, because one of the counted confirmations belongs to an account/key that is no longer a member. In the worst case, only the request creator plus a single live confirmer is needed to push a request over threshold, since a stale confirmation from a departed member silently fills one of the required slots. This directly undermines the K-of-N custody guarantee the contract is supposed to provide over the funds and permissions held by the multisig account.

### Likelihood Explanation
Membership changes (onboarding/offboarding signers, rotating compromised keys) are a normal, expected multisig operation, and having unconfirmed/partially-confirmed requests in flight at the same time is also expected given the `REQUEST_COOLDOWN` and `active_requests_limit` design. No adversarial timing or privileged bypass is needed beyond ordinary use of `delete_member`/`DeleteKey`; the bug triggers on the standard code path.

### Recommendation
When removing a member (`delete_member` in `multisig2/src/lib.rs`, `DeleteKey` handling in `multisig/src/lib.rs`), iterate over **all** pending requests' confirmation sets (not just requests the member created) and remove the deleted member's entry from each. Alternatively, when evaluating a confirmation count in `confirm()`, filter the stored confirmation set to only those entries that are still `self.members.contains(...)` before comparing against `num_confirmations`.

### Proof of Concept
1. Deploy `multisig2` with members `{A, B, C, D}` and `num_confirmations = 3`.
2. `A` calls `add_request(R1)` (e.g. `Transfer`) without confirming.
3. `D` calls `confirm(R1)` → `confirmations[R1] = {D}` (len 1 < 3, not executed).
4. Members execute a separate, properly-authorized `DeleteMember { member: D }` request (3-of-4 confirm), which calls `delete_member`. Since `R1.member == A` (not `D`), `R1`'s confirmations are untouched; `D`'s stale entry remains in `confirmations[R1]`.
5. `B` calls `confirm(R1)` → `confirmations[R1].len() == 2` (`{D, B}`), and `2 + 1 >= 3` is true, so `execute_request(R1)` fires.
6. `R1` executes even though only `B` is a genuinely live confirming member (plus non-confirming creator `A`) — well below the intended 3-of-remaining-members threshold, and using a phantom confirmation from a deleted signer `D`. [5](#0-4) [6](#0-5)

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
