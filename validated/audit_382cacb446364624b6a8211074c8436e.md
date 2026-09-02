### Title
Multisig request can execute below the live-member confirmation threshold when a confirming member is deleted mid-flight - ([File: multisig2/src/lib.rs])

### Summary
`MultiSigContract::confirm` counts confirmations that are already stored in `self.confirmations` for a request, without verifying that the confirmer is still a current member. `delete_member`/`DeleteKey` only purges pending requests that a removed member *originated*, not the stale confirmations that removed member left on other members' pending requests. As a result, a request can accumulate `num_confirmations` "votes" and execute even though one or more of those votes came from an account that is no longer part of the multisig at execution time.

### Finding Description
The intended custody binding for a multisig is: `confirmations_from_current_members >= num_confirmations` before any funds move or privileged action executes. The contract instead checks `confirmations.len() + 1 >= self.num_confirmations` [1](#0-0)  where `confirmations` is a persisted `HashSet` keyed by request id that is never re-validated against the live `members` set at confirm-time.

When a member is removed via `DeleteMember` (or `DeleteKey` in the legacy `multisig`), the cleanup logic only removes requests where `r.member == member`, i.e. requests *added* by that member: [2](#0-1) 
It does not scan `confirmations` for other pending requests where the removed member had already called `confirm` as a co-signer but the threshold hadn't been reached yet. That stale entry remains in the `HashSet<String>` for that request.

Concretely:
1. Members A, B, C, D, E exist, `num_confirmations = 3`.
2. Member X (not B) adds a `Transfer` request. B calls `confirm` → confirmations = {B}, count 1 < 3, request stays pending.
3. Through a separate, legitimately-confirmed `DeleteMember { member: B }` request, B is removed from `members`. That flow only cleans requests *added by B*; the pending transfer request from step 2 (added by X, merely confirmed by B) is untouched, and B's confirmation entry survives in `self.confirmations`.
4. C confirms → count becomes {B, C} = 2, still < 3.
5. A confirms → count becomes {B, C, A} = 3 >= `num_confirmations` → `execute_request` runs the transfer.

Only two currently-live members (C and A) plus one stale, no-longer-valid confirmation from B drove execution, even though the contract's authorization model requires `num_confirmations` (3) live approvals. This is exactly the "confirmations counted versus live members" divergence: the recorded confirmation count no longer equals the count of confirmations obtained from accounts entitled to grant them at execution time.

### Impact Explanation
This falls under the Critical impact bucket "a multisig request executed below threshold." Any privileged action gated by the multisig — `Transfer`, `FunctionCall`, `AddKey`, `DeployContract`, or further `AddMember`/`DeleteMember` changes — can be pushed through with fewer live approvals than configured, undermining the entire K-of-N security guarantee the contract is meant to provide (moving NEAR, deploying new contract code, or granting new access keys with less real consensus than intended).

### Likelihood Explanation
This requires no attacker to be outside the trust set in an unusual way: it only requires normal multisig operation over time — a member confirms a request, is later removed for ordinary reasons (rotation, revocation, off-boarding), and the request that they partially confirmed is left pending. Multisig membership changes and slow-to-confirm requests (up to the `REQUEST_COOLDOWN`/`active_requests_limit` windows) are both normal, expected occurrences, so the precondition is easy to hit without any malicious action beyond a remaining member choosing to confirm an old, partially-approved request after a membership change. No foundation, victim key, or redeploy is needed — only the intersection of two documented multisig operations (confirm-before-threshold, then delete-member) that the code fails to reconcile.

### Recommendation
When checking or counting confirmations in `confirm`, filter `confirmations` against the current `members` set (or, when a member is deleted, actively prune that member's confirmation entries from every request in `self.confirmations`, not only requests they authored). Recompute the effective confirmation count as `confirmations.intersection(current_members).len()` before comparing against `num_confirmations`, ensuring the executed threshold always reflects live membership.

### Proof of Concept
1. Deploy multisig with `members = [A, B, C, D, E]`, `num_confirmations = 3`.
2. Member X (e.g., A) calls `add_request` with a `Transfer` action to a target account.
3. Member B calls `confirm(request_id)` → `confirmations = {B}` (1/3).
4. Members reach 3 confirmations on a separate `DeleteMember { member: B }` request and it executes, removing B from `members`; the transfer request from step 2 is not in the cleanup filter (`r.member == member` matches only requests B originated) so it is untouched.
5. Member C calls `confirm(request_id)` → `confirmations = {B, C}` (2/3).
6. Member A (or X) calls `confirm(request_id)` → `confirmations.len() + 1 = 3 >= num_confirmations` → `execute_request` fires the transfer, even though B is no longer a member and only 2 currently-live members (A/X and C) actually approved. [1](#0-0) [2](#0-1) [3](#0-2)

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
