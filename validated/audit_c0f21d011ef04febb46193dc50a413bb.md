Confirmed: `delete_member` (multisig2/src/lib.rs:355-379) only strips outstanding requests **created by** the removed member, and only clears confirmations for those specific requests. It never scans other pending requests' `confirmations` sets to purge a stale entry left by the just-removed member. Since `confirm()` counts `confirmations.len()` toward `num_confirmations` without checking that each entry in the `HashSet<String>` still corresponds to a current member of `self.members`, a member who confirmed a request and was later removed still counts toward the threshold for that pre-existing request created by someone else. [1](#0-0) [2](#0-1) 

### Title
Multisig executes requests with confirmations from removed members, executing below the effective live-member threshold - (File: multisig2/src/lib.rs)

### Summary
`confirm()` in `multisig2/src/lib.rs` counts entries in the per-request `confirmations: LookupMap<RequestId, HashSet<String>>` set against `num_confirmations` without verifying that each confirming identity is still a current member of `self.members`. `delete_member()` only removes confirmations for requests that were *created by* the removed member; confirmations that the removed member had already cast on *other* pending requests (created by different members) are left untouched in the `confirmations` map.

### Finding Description
The intended binding is: `confirmations counted toward num_confirmations == confirmations by accounts that are currently live members`. This binding is broken as follows:
1. Member A creates a request R (`add_request`), targeting e.g. a `Transfer` or `AddKey` action.
2. Member B confirms R (`confirm`), inserting `B` into `self.confirmations[R]`. Suppose `num_confirmations = 3` and only B has confirmed so far (`len() == 1`).
3. The multisig (via another already-confirmed `DeleteMember` request, or any path reaching `execute_request` → `delete_member`) removes B from `self.members`. `delete_member` loops only over requests where `r.member == B` (i.e., requests B personally created) — see `multisig2/src/lib.rs:362-371`. Request R was created by A, not B, so R's entry in `confirmations` is never touched, and B's confirmation for R survives.
4. Now only 2 live members, C and D, need to confirm R. Once they do, `confirmations.len() + 1 >= num_confirmations` (`multisig2/src/lib.rs:304`) is satisfied by combining 2 live confirmations with the 1 stale confirmation from removed member B, and `execute_request` runs — even though only 2 currently-live members actually approved.

This directly breaks the "confirmations counted versus live members" custody binding: a request that should require `num_confirmations` distinct *current* members can execute with strictly fewer live approvers, as long as a confirmation from a subsequently-removed member is still cached against it.

### Impact Explanation
This allows a multisig request (e.g., a `Transfer` moving NEAR, or an `AddKey`/`AddMember` action that grants new signing power) to execute with fewer than `num_confirmations` currently-authorized approvers. This is a threshold-bypass: funds can move, or control of the multisig can be seized, with less authorization than the contract's own security model guarantees. This matches the Critical impact category "a multisig request executed below threshold."

### Likelihood Explanation
Reaching this requires only unprivileged actions available to any existing multisig member: creating and partially confirming a request, and having another already-in-flight/approved action remove one of the partial-confirmers before the vulnerable request is completed. No foundation, owner, or out-of-protocol assumption is needed — it's a straightforward sequencing of the multisig's own public methods (`add_request`, `confirm`, `DeleteMember` action). Any multisig that ever removes a member while other requests are pending with that member's confirmation attached is exposed.

### Recommendation
When executing a `DeleteMember` action (or generally in `delete_member`), iterate over **all** entries in `self.confirmations` (not just requests authored by the removed member) and remove the removed member's identity string from each `HashSet<String>`. Alternatively, when counting confirmations in `confirm()`, filter `confirmations` against `self.members.contains(...)` before comparing against `num_confirmations`, so stale entries from removed members never count toward the threshold.

### Proof of Concept
1. Deploy multisig with members `{A, B, C, D}` and `num_confirmations = 3`.
2. `A.add_request(R)` — a `Transfer` request to an attacker-controlled account.
3. `B.confirm(R)` → `confirmations[R] = {B}`, `len()=1 < 3`, so it just records.
4. Separately, an already-fully-confirmed request executes `DeleteMember { member: B }` (assume this was legitimately approved for unrelated reasons, e.g. B is leaving the org). `delete_member` removes B from `self.members` but does not touch `confirmations[R]`.
5. `C.confirm(R)` → `confirmations[R] = {B, C}`, `len()=2 < 3`.
6. `D.confirm(R)` → `confirmations.len() as u32 + 1 == 3 >= num_confirmations`, so `execute_request` runs and the transfer executes — approved in effect by only C and D (2 live members) plus a stale confirmation from the now-removed B, not 3 live members.

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
