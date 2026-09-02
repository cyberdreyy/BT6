### Title
Stale confirmations from removed multisig members are still counted toward the execution threshold - (File: multisig2/src/lib.rs)

### Summary
`MultiSigContract::confirm` counts every public key/account already present in a request's `confirmations` set toward `num_confirmations`, but `delete_member` never purges a removed member's confirmation from requests they did not themselves originate. A request that was confirmed by a member before that member's removal keeps counting that stale confirmation, letting the request execute with fewer *live* member confirmations than `num_confirmations` requires. [1](#0-0) [2](#0-1) 

### Finding Description
The intended custody binding for a K-of-N multisig is:
`confirmations from currently-live members >= num_confirmations` before a request executes.

`confirm()` instead checks raw set size:
```
if confirmations.len() as u32 + 1 >= self.num_confirmations {
    let request = self.remove_request(request_id);
    self.execute_request(request)
}
``` [3](#0-2) 

`delete_member()` only cleans up requests that the removed member originated (`r.member == member`) and removes their entry from `num_requests_pk`/`members`; it does not scan `self.confirmations` for other still-pending requests to strip out the removed member's vote:
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
...
self.members.remove(&member);
``` [4](#0-3) 

So a request `X` originated by member `B` and confirmed early by member `A` retains `A` in `confirmations[X]` even after `A` is removed from `self.members` via a later, legitimately-executed `DeleteMember` request. Any subsequent confirmations by remaining live members are added on top of that stale entry, and the threshold check in `confirm()` treats it as a valid vote.

The equality that should hold before executing `X` — `|confirmations[X] ∩ current_members| >= num_confirmations` — is violated because `confirmations[X]` is never intersected with `self.members`.

The near-identical structure exists in the v1 contract (`multisig/src/lib.rs`), whose `remove_request`/`delete_key` logic shows the same pattern of only cleaning up requests keyed by the deleted signer, not confirmations by that signer on other requests. [5](#0-4) 

### Impact Explanation
This breaks the multisig's core authorization guarantee (K-of-N threshold). A request can be executed with fewer than `num_confirmations` *live* member confirmations, because one confirmation may belong to a member who has since been removed (e.g., due to key compromise, offboarding, or governance rotation). This maps directly to "a multisig request executed below threshold," which is called out explicitly as Critical impact.

### Likelihood Explanation
Likelihood is realistic in normal operational flow: membership rotation (removing a departing or compromised member) is a routine multisig action, and any request left pending with that member's earlier confirmation automatically benefits from the stale vote. No malicious collusion or privileged bypass is required beyond normal multisig usage — an ordinary member-removal request executed as designed leaves the vulnerability exposed for any outstanding request.

### Recommendation
When executing `DeleteMember` (and the equivalent `delete_key` path in `multisig/src/lib.rs`), iterate over all entries in `self.confirmations` and remove the deleted member's public key/account from every request's confirmation set, not just requests they originated. Alternatively, at confirmation-count time in `confirm()`, filter `confirmations` against `self.members` before comparing against `num_confirmations`, so only live members' votes are counted.

### Proof of Concept
1. Multisig contract initialized with `members = {A, B, C, D}`, `num_confirmations = 3`.
2. `B` calls `add_request` to create transfer request `X` to a receiver of `B`'s choosing.
3. `A` calls `confirm(X)` → `confirmations[X] = {A}` (len 1, `1+1=2 < 3`, not yet executed). [1](#0-0) 
4. Separately, a `DeleteMember{A}` request is created and confirmed to the threshold by other live members and executes, removing `A` from `self.members`. Because `X.member == B`, the cleanup loop in `delete_member` does not touch `confirmations[X]`, so `A`'s vote on `X` remains. [4](#0-3) 
5. Current live members are now `{B, C, D}`.
6. `B` calls `confirm(X)` → `confirmations[X] = {A, B}` (len 2, `2+1=3 < ... ` wait: check is on pre-insert len: `2+1=3 >= 3` after this step) — the check passes and `execute_request(X)` runs.
7. Result: request `X` executes having received live confirmations from only `B` and (implicitly) whichever member calls confirm last, i.e., 2 live-member confirmations instead of the required 3, because the stale `A` confirmation is counted as if `A` were still a member.

Note: I was unable to fully trace whether an equivalent cleanup exists elsewhere in the codebase (e.g., a periodic sweep) beyond what `delete_member`/`remove_request` show in the retrieved snippets; if such a sweep exists elsewhere in `multisig2/src/lib.rs` outside the reviewed line ranges, it would need to be checked to confirm this is not already mitigated.

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

**File:** multisig/src/lib.rs (L268-292)
```rust
    /********************************
    Helper methods
    ********************************/
    // removes request, removes confirmations and reduces num_requests_pk - used in delete, delete_key, and confirm
    fn remove_request(&mut self, request_id: RequestId) -> MultiSigRequest {
        // remove confirmations for this request
        self.confirmations.remove(&request_id);
        // remove the original request
        let request_with_signer = self
            .requests
            .remove(&request_id)
            .expect("Failed to remove existing element");
        // decrement num_requests for original request signer
        let original_signer_pk = request_with_signer.signer_pk;
        let mut num_requests = self.num_requests_pk.get(&original_signer_pk).unwrap_or(0);
        // safety check for underrun (unlikely since original_signer_pk must have num_requests_pk > 0)
        if num_requests > 0 {
            num_requests = num_requests - 1;
        }
        self.num_requests_pk
            .insert(&original_signer_pk, &num_requests);
        // return request
        request_with_signer.request
    }
    // Prevents access to calling requests and make sure request_id is valid - used in delete and confirm
```
