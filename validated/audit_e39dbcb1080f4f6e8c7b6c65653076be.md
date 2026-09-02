Confirmed: `delete_member` in `multisig2/src/lib.rs` only purges requests where the removed member was the *original requester* (`r.member == member`), not requests the removed member merely *confirmed*. This means a removed member's confirmation, recorded on any other request they voted on, remains counted toward `num_confirmations` on that request forever.

### Title
Stale Confirmations From Removed Multisig Members Can Push a Request Past the Live-Member Threshold - ([File: multisig2/src/lib.rs])

### Summary
`confirm` counts entries in the `confirmations` set for a request against `self.num_confirmations` without verifying that each recorded confirmer is still a current member. `delete_member` removes a member from `self.members` and purges only requests that member itself created, leaving that member's `PublicKey`/`account_id` string inside the `confirmations` `HashSet` of every other pending request they had confirmed. A later confirmation by a legitimate remaining member can then push `confirmations.len() + 1 >= self.num_confirmations`, executing the request using a mix of live and already-removed identities.

### Finding Description
`confirm` (multisig2/src/lib.rs:294-315) does:
```rust
if confirmations.len() as u32 + 1 >= self.num_confirmations {
    let request = self.remove_request(request_id);
    self.execute_request(request)
}
``` [1](#0-0) 
It relies purely on the stored `HashSet<String>` size, never re-checking `self.members.contains(...)` for each entry already inside `confirmations`.

`delete_member` (multisig2/src/lib.rs:356-379) only cleans up requests that the removed member itself originated:
```rust
let request_ids: Vec<u32> = self
    .requests
    .iter()
    .filter_map(|(k, r)| if r.member == member { Some(k) } else { None })
    .collect();
for request_id in request_ids {
    self.confirmations.remove(&request_id);
    self.requests.remove(&request_id);
}
``` [2](#0-1) 
It never scans the `confirmations` map for requests where `member` merely appears as a *confirmer* rather than as the request's `member` field. Those stale entries are left intact, and `current_member()` (multisig2/src/lib.rs:322-339) is only used to authorize *new* actions, not to revalidate previously stored confirmations.

The equality broken is: `confirmations_counted == live_members_who_actually_confirmed`. After a removal, `confirmations_counted > live_members_who_actually_confirmed` for any request the removed member had confirmed before being deleted, so the K-of-N guarantee ("K live members must approve") no longer holds for those pending requests.

The near-identical bug in `multisig/src/lib.rs` (v1) has the same limitation: `DeleteKey` (multisig/src/lib.rs:198-216) purges only requests where `r.signer_pk == pk` (i.e., requests created by that key), not requests where that key appears in another request's `confirmations` set. [3](#0-2) 

### Impact Explanation
This is a "confirmations counted versus live members" custody-binding violation explicitly in scope: a multisig request (which can include `Transfer`, `FunctionCall`, `AddKey`, etc., i.e. moving NEAR or authorizing arbitrary calls) can be executed with fewer than `num_confirmations` *live* approvals. This directly maps to "a multisig request executed below threshold" (Critical impact) since NEAR can be transferred, or arbitrary function calls executed, without the required number of currently-authorized members actually approving.

### Likelihood Explanation
Reaching this requires only ordinary, unprivileged multisig operations available to any current member (no owner/foundation-only action, no redeploy): create a request, have it confirmed by a member who is later removed via a normal `DeleteMember` request, then have any other still-live member confirm it later. This is a realistic operational sequence (member rotation is a common multisig lifecycle event) and does not require the removed member to still hold their key/account — the stale string in the `HashSet` counts regardless.

### Recommendation
When executing `DeleteMember` (and `DeleteKey` in multisig v1), scan all pending `confirmations` entries (not just requests authored by that member) and remove the departing member's confirmation string from every set, re-evaluating whether any request now falls below the confirmation invariant. Alternatively, when counting confirmations in `confirm`, filter `confirmations` against `self.members.contains(...)` before comparing against `self.num_confirmations`, ensuring only currently-live members are counted.

### Proof of Concept
1. Initialize `MultiSigContract::new(members: [A, B, C], num_confirmations: 2)`.
2. Member `A` calls `add_request` to create request `R` (e.g., a `Transfer`).
3. Member `B` calls `confirm(R)` → `confirmations[R] = {B}` (only 1/2, not yet executed).
4. Members execute a separate request removing `B` via `DeleteMember { member: B }` (`delete_member` only checks `r.member == B`, which is false for `R` since `R.member == A`, so `confirmations[R]` is untouched and still contains `B`).
5. Member `C` (a live member) calls `confirm(R)` → `confirmations[R].len() + 1 == 2 >= num_confirmations(2)` → `R` executes, transferring funds, even though `B` is no longer a member and only one *currently live* member (`C`) actually approved at execution time.

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

**File:** multisig2/src/lib.rs (L355-374)
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
