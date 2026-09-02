### Title
Stale confirmations from removed multisig members allow request execution below the configured threshold - ([File: multisig2/src/lib.rs])

### Summary
In `MultiSigContract::delete_member`, when a member is removed from the multisig, only the *requests that member created* are purged. Any confirmations that member cast on requests **created by other members** are left untouched in the `confirmations` map. Because `confirm()` only counts the size of this stale set against `num_confirmations`, a request can later execute with fewer live, currently-authorized members than `num_confirmations` actually requires.

### Finding Description
`confirm()` decides whether to execute a request purely by comparing the size of the `confirmations: HashSet<String>` for that `request_id` against `self.num_confirmations`: [1](#0-0) 

`delete_member()` is the only place that reacts to a member being removed. It deletes the member's own access key/entry and purges pending *requests that member created* (`r.member == member`), but it never scans `self.confirmations` for entries where the removed member is merely a **confirmer** on some other request: [2](#0-1) 

The binding that should hold is:

`confirmations counted for request R == confirmations from members that are still in self.members at execution time`

This invariant is broken because a confirmation recorded by member `M` on request `R` (created by someone else) survives `M`'s removal from `self.members`. When the threshold check in `confirm()` later fires, it counts `M`'s stale confirmation as if `M` were still a valid signer.

The same pattern exists in the original `multisig/src/lib.rs`: `DeleteKey` only removes requests whose `signer_pk` equals the deleted key (i.e., requests that key *created*), not confirmations that key placed on other pending requests: [3](#0-2) 

### Impact Explanation
This is a threshold bypass: a `k`-of-`n` multisig can execute a request (including `Transfer`, `FunctionCall`, `AddKey`/`AddFullAccessKey`, `DeployContract`) with fewer than `k` currently valid members having actually authorized it. This directly matches the Critical impact category "a multisig request executed below threshold," since NEAR (or contract control via `AddKey`/`DeployContract`) can move or be seized without the required number of live approvals.

### Likelihood Explanation
The precondition is realistic and does not require any special privilege beyond being (at some point) one of the `n` multisig members — exactly the actor model this contract is built around. Any multisig that ever rotates/removes a member while requests are pending is exposed: whenever a member is removed for a legitimate reason (key compromise, offboarding, rotation), any confirmation they placed earlier on an outstanding request from another member remains valid and countable, silently reducing the effective threshold for that specific request.

### Recommendation
When removing a member in `delete_member`, iterate `self.confirmations` for **all** pending requests and strip the removed member's identifier from each confirmation set (not just requests the member created). Alternatively, revalidate at `confirm()`/execution time that every entry in `confirmations` still corresponds to a member present in `self.members`, discarding stale ones before comparing against `num_confirmations`.

### Proof of Concept
1. Deploy `multisig2` with `members = [A, B, C]`, `num_confirmations = 2`.
2. `B` calls `add_request` to create request `R` (e.g., `Transfer` to an account `B` controls, or a malicious `FunctionCall`/`AddKey`). `R.member == B`, `confirmations[R] = {}`.
3. `A` calls `confirm(R)`. Now `confirmations[R] = {A}` (1 of 2, not yet executed) — see `confirm` at [1](#0-0) .
4. The group legitimately removes `A` via a properly-confirmed `DeleteMember { member: A }` request. `delete_member(A)` runs: it purges requests where `r.member == A` (none, since `R.member == B`), removes `A` from `self.members`, deletes `A`'s key — but leaves `confirmations[R] = {A}` untouched, per [4](#0-3) .
5. `C` calls `confirm(R)`. `confirmations[R].len() + 1 == 2 >= num_confirmations(2)` → `R` executes via `execute_request`, even though only `C` is currently a valid member who approved it; `A`'s stale confirmation counted toward the 2-of-3 threshold after `A` was removed.

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
