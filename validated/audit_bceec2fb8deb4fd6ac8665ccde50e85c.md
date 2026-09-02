## Title
Stale Confirmations From Removed Multisig Members Are Still Counted Toward Quorum — (File: `multisig2/src/lib.rs`)

### Summary
The Pico bug is a class of "stale/miscounted state" defect: a counter is updated (or left un-updated) inconsistently with the semantics its sibling operations enforce, so a later check trusts a count that no longer reflects reality. The direct analog in this repository is in `multisig2/src/lib.rs`: when a member is removed via `DeleteMember`, the contract only purges confirmations for requests *proposed* by that member, not confirmations that member previously *cast* on other members' still-pending requests. Those stale confirmations remain in the `confirmations` map and are still counted toward the `num_confirmations` quorum when a later `confirm()` call is made, even though the confirming account is no longer a member.

### Finding Description
`confirm()` decides whether a request executes purely by counting entries in the `confirmations` set: [1](#0-0) 

`delete_member()` is the only place that removes members and it only cleans up requests/confirmations for requests where the *deleted member is the proposer* (`r.member == member`). It does not scan the `confirmations` map for other, still-pending requests that the deleted member previously confirmed as an approver: [2](#0-1) 

`remove_request()` (invoked from `confirm()` once quorum is reached, and from `delete_request()`) also never re-validates that the accounts/keys present in the stored `confirmations` set are still current members: [3](#0-2) 

The custody binding that should hold is: *confirmations counted toward quorum == confirmations from currently live members*. Because a removed member's earlier confirmation is never purged from requests they didn't propose, this equality can be broken — a request can reach `num_confirmations` even though one or more of the "confirming" identities have since been removed from `self.members`, meaning fewer *live* members than `num_confirmations` actually authorized the request's execution (transfers, key/member management, or arbitrary `FunctionCall`s on the contract's own account).

### Impact Explanation
This falls under the Critical impact category "a multisig request executed below threshold." An attacker who is (or colludes with) a member can:
1. Get a member `A` to confirm a pending `Transfer`/other request (recorded confirmation).
2. Separately propose and pass a `DeleteMember{A}` request, removing `A` from `self.members` (this cleanup only touches requests `A` itself proposed, not the transfer `A` merely confirmed).
3. Continue collecting confirmations on the original request from remaining live members until `confirmations.len() + 1 >= num_confirmations` is satisfied — counting `A`'s stale, no-longer-valid confirmation toward the threshold.
4. The request executes (e.g. `Transfer`, `AddKey`, `AddMember`) despite fewer than `num_confirmations` currently-authorized members having actually approved it.

This directly moves NEAR (or grants access) under a quorum guarantee that has silently been weakened, which is exactly the kind of authorization-boundary violation the multisig is meant to prevent.

### Likelihood Explanation
Requires no special privilege beyond being an existing multisig member (which is the normal operating condition of this contract) and simply sequencing two already-supported operations (`confirm`, then `DeleteMember` via the standard request/confirm flow) in an order that is not prevented by any assertion in the code. No malicious validator, redeploy, or foundation action is needed — only ordinary member behavior and the existing on-chain request lifecycle.

### Recommendation
When `delete_member` removes a member, also purge that member's identity string from the `confirmations` set of every *other* pending request (not only ones they proposed), or alternatively, when tallying confirmations in `confirm()`/before executing a request, filter the confirmation set to only accounts/keys still present in `self.members` before comparing against `num_confirmations`.

### Proof of Concept
1. Initialize `MultiSigContract::new([A, B, C, D], 3)`.
2. `B` calls `add_request` with a `Transfer` request → `request_id`. `confirmations = {}`.
3. `A` calls `confirm(request_id)` → `confirmations = {A}` (1 < 3, not yet executed).
4. Separately, members drive a `DeleteMember{A}` request through `add_request_and_confirm`/`confirm` to quorum; it executes via `delete_member`, which removes `A` from `self.members` but does **not** touch the `Transfer` request's confirmations (`r.member` for that request is `B`, not `A`), so `confirmations` for `request_id` still contains `A`.
5. `B` calls `confirm(request_id)` → `confirmations = {A, B}` (2 < 3).
6. `C` calls `confirm(request_id)` → count becomes `3 >= 3` → request executes, even though `A` is no longer a member — only 2 genuinely current members (`B`, `C`) approved it. [4](#0-3) [5](#0-4)

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

**File:** multisig2/src/lib.rs (L381-404)
```rust
    /// Removes request, removes confirmations and reduces num_requests_pk - used in delete, delete_key, and confirm
    fn remove_request(&mut self, request_id: RequestId) -> MultiSigRequest {
        // remove confirmations for this request
        self.confirmations.remove(&request_id);
        // remove the original request
        let request_with_signer = self
            .requests
            .remove(&request_id)
            .unwrap_or_else(|| env::panic_str("Failed to remove existing element"));
        // decrement num_requests for original request signer
        let original_member = request_with_signer.member;
        let mut num_requests = self
            .num_requests_pk
            .get(&original_member.to_string())
            .unwrap_or(0);
        // safety check for underrun (unlikely since original_signer_pk must have num_requests_pk > 0)
        if num_requests > 0 {
            num_requests = num_requests - 1;
        }
        self.num_requests_pk
            .insert(&original_member.to_string(), &num_requests);
        // return request
        request_with_signer.request
    }
```
