### Title
Stale confirmations from removed multisig members still count toward the execution threshold, allowing requests to execute below the current-member quorum - (File: `multisig2/src/lib.rs`)

### Summary
The `MultiSigContract` in `multisig2/src/lib.rs` counts confirmations by string identity (`HashSet<String>`) per `request_id`, but `delete_member` never scrubs a departing member's confirmations from requests they did not personally create. Once a member is removed, any confirmation they previously cast on a still-pending request (created by someone else) remains in the `confirmations` set and continues to count toward `num_confirmations` when a later, still-current member confirms — even though the confirming party is no longer a trusted member of the multisig.

### Finding Description
`confirm()` only validates that the *current* caller is a member; it never re-validates the identities already stored in the `confirmations` set for that request: [1](#0-0) 

`delete_member()` only removes requests where the deleted member is the *request creator* (`r.member == member`); it does not walk `self.confirmations` to purge entries where the deleted member was merely a *confirmer* on someone else's request: [2](#0-1) 

Because of this, the binding "confirmations counted == confirmations from currently-live members" is broken: a confirmation cast by member A survives A's removal from `self.members` and still contributes to `confirmations.len() as u32 + 1 >= self.num_confirmations` for any pending request A previously confirmed but did not create.

### Impact Explanation
This lets a request execute (e.g. `Transfer`, `AddKey` with a full-access key, `DeployContract`, `AddMember`) with fewer *currently trusted* confirmations than `num_confirmations` mandates, because a stale confirmation from a compromised/removed member is silently counted as if it were live consent. This directly matches the Critical impact category "a multisig request executed below threshold," since the effective quorum of currently-authorized members is bypassed.

### Likelihood Explanation
This is reachable by design, not by a hypothetical misconfiguration: any time a multisig removes a member (e.g., because a key was suspected compromised, an employee left, or a governance rotation occurred) while a request that member had previously confirmed is still pending, the stale confirmation persists indefinitely. No special privilege beyond normal multisig membership/administration flow is needed to trigger it — it only requires the ordinary sequence of confirm-then-remove-member-then-confirm-again, which is a routine multisig lifecycle event.

### Recommendation
When removing a member in `delete_member`, iterate all pending requests' confirmation sets (not just the ones the member created) and strip the removed member's identity from each. Alternatively, when checking the threshold in `confirm()`, intersect the stored confirmations with the current `self.members` set and count only the live members' confirmations against `num_confirmations`.

### Proof of Concept
1. Deploy `MultiSigContract::new(members = [X, A, B, C], num_confirmations = 3)`.
2. `X` calls `add_request(...)` for a malicious action (e.g. `AddKey` with a new full-access key). `request_id = R`.
3. `A` calls `confirm(R)` → confirmations = `{A}` (len 1, 1+1<3, not executed).
4. `B` calls `confirm(R)` → confirmations = `{A, B}` (len 2, 2+1<3, still not executed).
5. Members detect `A`'s key is compromised and execute a separate, properly-confirmed multisig request with `MultiSigRequestAction::DeleteMember { member: A }`. `delete_member` only checks `self.members.len() - 1 >= num_confirmations` and removes requests created by `A` — it does not touch `R`'s confirmation set. `self.members` is now `[X, B, C]`.
6. `C` calls `confirm(R)` → `confirmations.len() (2, still {A,B}) + 1 = 3 >= num_confirmations (3)` → `execute_request(R)` runs, granting the malicious full-access key, even though only `B` and `C` are current, trusted members who actually confirmed — `A`'s stale, revoked confirmation supplied the missing vote.

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
