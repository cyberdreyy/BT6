The reported bug class does have a valid, reachable analog in this repo: stale confirmations from removed multisig members are still counted toward the execution threshold, letting a shrunk set of "live" members execute a request that never received `num_confirmations` worth of confirmations from members currently authorized.

### Title
Stale confirmations from removed multisig members are still counted toward execution threshold, allowing execution below the true live-member quorum - ([File: multisig2/src/lib.rs])

### Summary
`confirm()` in `multisig2/src/lib.rs` decides whether to execute a request purely by comparing the size of the `confirmations` set stored for that `request_id` against `self.num_confirmations`. `delete_member()` only purges confirmations/requests that were *originated* by the removed member (`r.member == member`), but it never scans other pending requests to strip out confirmations that removed member previously cast on requests created by someone else. As a result, a confirmation given by a member who is later removed remains permanently counted, letting the remaining/current members reach the threshold with fewer live confirmations than `num_confirmations` actually requires.

### Finding Description
`confirm()` only checks: [1](#0-0) 
It never validates that every public key/account already in the `confirmations: HashSet<String>` for a request is still `self.members.contains(...)` — it only validates the *current* caller via `current_member()` inside `assert_valid_request`: [2](#0-1) 

`delete_member()` removes a departing member's own *originated* requests and their `num_requests_pk` entry, but does nothing to scrub that member's *confirmations* recorded on requests they didn't originate: [3](#0-2) 

So the binding that should hold is:
`confirmations.len() (of currently-valid members) >= num_confirmations` ⇔ execute.

Instead the actual code enforces:
`confirmations.len() (including confirmations from members removed after they confirmed) >= num_confirmations` ⇔ execute.

Once a member is removed, any confirmation they gave earlier on a still-pending request continues to count as if it were a live, authorized signature.

The identical pattern exists in the legacy `multisig` contract, where `delete_key`/`confirm` never purge a removed key's confirmations from requests it didn't originate: [4](#0-3) 

### Impact Explanation
This breaks the authorization/threshold boundary the multisig is built to enforce: an operation (transfer, `AddKey`, `AddMember`, `DeployContract`, `SetNumConfirmations`, etc.) can be executed with fewer signatures from currently-authorized members than `num_confirmations` requires, because a stale confirmation from a since-removed member is silently counted. This matches the Critical bucket "a multisig request executed below threshold" — the multisig can move funds or change its own membership/config using a quorum that, among *current* members, never actually existed.

### Likelihood Explanation
This requires no attacker privilege beyond being (or having been) a legitimate member at some point and the multisig later removing a member via the normal `DeleteMember` flow while a request is still pending with that member's earlier confirmation attached — a realistic operational sequence (member rotation/offboarding) rather than a contrived edge case. No owner/foundation misbehavior, redeploy, or victim key compromise is needed; it is a pure logic gap in `confirm`/`delete_member`.

### Recommendation
When executing a request in `confirm()`, filter `confirmations` down to entries where `self.members.contains(&member)` before comparing against `self.num_confirmations` (or, on `delete_member`, iterate all pending requests and remove the departing member's entry from every `confirmations` set, not only requests they originated).

### Proof of Concept
1. Multisig has members `{A, B, C, D}`, `num_confirmations = 3`.
2. `Y` (any member) creates a request `R` (e.g. `Transfer`).
3. Member `A` confirms `R` (`confirmations = {A}`).
4. Members vote to `DeleteMember { A }` and it executes; `delete_member` only removes requests A *originated*, so `R`'s `confirmations` set still contains `A` — `{A}` unchanged: [5](#0-4) .
5. Now only `{B, C, D}` are live members (3 of them), yet `R` already has 1 stale confirmation from removed `A`.
6. `B` confirms → `confirmations.len()+1 = 2` (still short).
7. `C` confirms → `confirmations.len()+1 = 3 >= num_confirmations (3)` → `execute_request(R)` fires: [6](#0-5) .
8. `R` executed with only 2 *live* member confirmations (`B`, `C`) even though the policy requires 3-of-N among current members — `D` never signed, and `A` is no longer a member at all.

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

**File:** multisig2/src/lib.rs (L406-423)
```rust
    /// Prevents access to calling requests and make sure request_id is valid - used in delete and confirm
    fn assert_valid_request(&mut self, request_id: RequestId) {
        // request must come from key added to contract account
        assert(
            self.current_member().is_some(),
            "Caller (predecessor or signer) is not a member of this multisig",
        );
        // request must exist
        assert(
            self.requests.get(&request_id).is_some(),
            "No such request: either wrong number or already confirmed",
        );
        // request must have
        assert(
            self.confirmations.get(&request_id).is_some(),
            "Internal error: confirmations mismatch requests",
        );
    }
```

**File:** multisig/src/lib.rs (L246-292)
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
