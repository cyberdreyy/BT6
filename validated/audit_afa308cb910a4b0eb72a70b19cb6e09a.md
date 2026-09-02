## Finding: Stale confirmations from deleted multisig members are still counted toward the confirmation threshold

### Title
Deleted multisig members' confirmations remain counted toward `num_confirmations`, allowing requests to execute below the live-member threshold - (File: `multisig2/src/lib.rs`)

### Summary
`MultiSigContract::delete_member` in `multisig2/src/lib.rs` only purges requests and confirmation records for requests *initiated* by the removed member, but never scrubs that member's confirmation entries from the `confirmations` sets of *other* pending requests they had already confirmed. As a result, a member who has been removed from `self.members` can still have their earlier confirmation counted when tallying `num_confirmations`, letting a request execute with fewer live, authorized confirmations than the configured threshold.

### Finding Description
The contract's core custody binding is: a request may only execute once `confirmations.len() >= num_confirmations`, where every entry in `confirmations` is expected to represent a currently-authorized member. This is enforced in `confirm`: [1](#0-0) 

However, `delete_member` only cleans up requests where the deleted member was the *original signer* (`r.member == member`), not the confirmations they may have added to *other* requests initiated by someone else: [2](#0-1) 

Contrast this with `remove_request`, which does fully clear `confirmations` for the request being removed - but that only happens for the specific request that reaches quorum or is deleted, not for the surviving requests a soon-to-be-removed member had previously confirmed: [3](#0-2) 

Because `confirmations` is a plain `HashSet<String>` keyed by `member.to_string()` with no back-reference from member to the requests they've confirmed, `delete_member` has no way (and makes no attempt) to find and strip the deleted member's confirmation out of every other pending request's confirmation set.

The equality that should hold is:
```
confirmations_for(request) ⊆ members  (at all times)
```
After a `DeleteMember` action executes, this becomes violated for any pending request that the removed member had confirmed but did not initiate: `confirmations_for(request)` still contains the removed member's identity even though `members` no longer does.

### Impact Explanation
This is Critical: it results in "a multisig request executed below threshold." Concretely, a `Transfer` (or `FunctionCall`, `AddKey`, `DeployContract`, etc.) request can be pushed through `confirm` to execution with only `num_confirmations - 1` (or fewer) live, currently-authorized members actually approving it, because one of the counted confirmations belongs to a member that has since been removed (e.g., due to a compromised key, an offboarded team member, or a governance decision to reduce trust in that member). This directly breaks the K-of-N custody guarantee the contract is supposed to provide over the account's NEAR balance and permissions.

### Likelihood Explanation
This requires no attacker privilege beyond being (or having been) a legitimate multisig member — a completely realistic and expected operational sequence:
1. Member C creates a request (e.g., `Transfer`).
2. Member B confirms it (but doesn't push it over threshold).
3. The multisig later executes `DeleteMember { member: B }` (e.g., because B's key was compromised or B left the organization) — a normal, documented multisig operation, not a misconfiguration or ignored initialization step.
4. B's confirmation on the still-pending request from step 1 is never removed.
5. One more live member confirms, reaching `num_confirmations`, and the request executes — even though B is no longer a member and was never re-consulted.

Since removing a member specifically because they are no longer trusted (e.g., compromised key) is the primary intended use of `DeleteMember`, this scenario is highly likely to occur in practice, not a theoretical edge case.

### Recommendation
In `delete_member`, iterate over all active `requests`/`confirmations` entries and remove the deleted member's identity (`member.to_string()`) from every confirmation set, not just the sets belonging to requests the member itself initiated. Alternatively, when tallying confirmations in `confirm`, filter `confirmations` against the current `self.members` set before comparing against `num_confirmations`, so stale entries from removed members are never counted.

### Proof of Concept
1. Deploy `multisig2` with members `[A, B, C, D]` and `num_confirmations = 3` via `MultiSigContract::new`.
2. As `C`, call `add_request` with a `Transfer` action to an attacker-controlled receiver — creates `request_id = R` with empty confirmations.
3. As `D`, call `confirm(R)` → `confirmations[R] = {D}` (1/3, below threshold, per [4](#0-3) ).
4. As `B`, call `confirm(R)` → `confirmations[R] = {D, B}` (2/3, still below threshold).
5. Separately, the group legitimately executes a `DeleteMember { member: B }` request (3 confirmations from A, C, D) because B's key is suspected compromised. `delete_member` removes `B` from `self.members` and deletes only requests *initiated* by `B`; request `R` (initiated by `C`) and its confirmation set `{D, B}` are untouched — see [5](#0-4) .
6. As `A` (a live member), call `confirm(R)` → `confirmations[R].len() + 1 = 3 >= num_confirmations`, so `execute_request` fires the `Transfer` in `R`, even though only `A` and `D` are still live confirming members (2 live confirmations) plus one stale confirmation from removed member `B`.

This transfers funds out of the multisig account despite only 2 of the 3 required *currently-authorized* members having actually approved the transfer at execution time.

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

**File:** multisig2/src/lib.rs (L356-379)
```rust
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
