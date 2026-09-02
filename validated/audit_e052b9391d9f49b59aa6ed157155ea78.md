### Title
Multisig request can execute below the required confirmation threshold because stale confirmations from deleted members are never purged - (File: `multisig2/src/lib.rs`)

### Summary
`MultiSigContract::confirm` in `multisig2/src/lib.rs` decides whether a request has reached quorum purely by counting the size of the `confirmations: LookupMap<RequestId, HashSet<String>>` set for that request. `delete_member` removes a member from `self.members` and deletes *that member's own* outstanding requests, but it never scans other pending requests' `confirmations` sets to strip out a confirmation the removed member had already cast on someone else's request. The confirmation count therefore silently includes votes from accounts/keys that are no longer members, breaking the `num_confirmations`-of-`members` custody binding the contract is supposed to enforce.

### Finding Description
The relevant equality that must hold is: `confirmations counted for request R == confirmations from accounts currently in self.members`. This invariant is violated as follows: [1](#0-0) 

`confirm()` only checks `confirmations.len() as u32 + 1 >= self.num_confirmations` — it never re-validates that every public key/account already present in the stored `confirmations` set is still a current member. [2](#0-1) 

`delete_member()` only removes requests **created by** the deleted member (`r.member == member`) and clears that member's `num_requests_pk` entry. It does not iterate over `self.confirmations` to remove the deleted member's vote from requests created by *other* members that the deleted member had already confirmed. Those stale entries remain keyed by the (now invalid) public key/account-id string inside the `HashSet<String>`.

Because `assert_valid_request` only checks that the *caller* confirming right now is a current member, not that previously recorded confirmations still belong to current members, a request can reach the numeric threshold `num_confirmations` using a mix of live and stale (removed-member) confirmations.

### Impact Explanation
This directly breaks the "confirmations counted versus live members" custody binding called out as in-scope: a `MultiSigRequest` — which can be an arbitrary `Transfer`, `FunctionCall`, `AddKey`, `AddMember`, etc. — can be executed with fewer genuine, currently-authorized approvals than `num_confirmations` requires. That is explicitly a Critical-severity outcome ("a multisig request executed below threshold"), since it lets funds be transferred or account control be altered without the intended K-of-N approval.

### Likelihood Explanation
This does not require any privileged attacker action, victim key theft, or redeploy — it only requires the normal, expected lifecycle of the contract: members are added and removed over time via ordinary `DeleteMember` requests (a first-class, documented feature of the contract), and pending requests can span a member-rotation event. Any organization that removes a member while a request has already collected that member's confirmation is exposed; no special timing exploit beyond ordinary operational sequencing is needed.

### Recommendation
When a member is deleted, iterate all pending `requests`/`confirmations` (not just those authored by the deleted member) and remove the deleted member's identifier from every confirmations `HashSet`. Alternatively, validate at `confirm()`/execution time that every entry in the stored confirmations set is still present in `self.members` before counting it toward the threshold.

### Proof of Concept
1. Deploy `multisig2` with members `{A, B, C, D}` and `num_confirmations = 3`.
2. Member `A` calls `add_request` to create request `R` (e.g., `Transfer` to an attacker-controlled account).
3. Member `B` calls `confirm(R)` → `confirmations[R] = {B}` (size 1 < 3, stored, not executed).
4. Members later legitimately rotate `B` out: an `AddMember`/`DeleteMember`-type request removing `B` is created and confirmed by 3 members and executed via `delete_member` (`multisig2/src/lib.rs:355-379`). This only purges requests where `r.member == B` (i.e., requests B *authored*), leaving `confirmations[R] = {B}` untouched even though `B` is no longer in `self.members`.
5. Member `C` calls `confirm(R)` → `confirmations[R] = {B, C}`, size 2 < 3, not yet executed.
6. Member `D` calls `confirm(R)` → `confirmations.len() as u32 + 1 = 3 >= num_confirmations (3)` → `execute_request(R)` runs and the transfer/action is executed.
7. In reality only `C` and `D` — 2 of the 3 currently-live members (`A, C, D`) — knowingly approved `R`; the third "confirmation" came from `B`, who was removed from the multisig before step 6. The K-of-N (3-of-3-live-members) guarantee is broken, and the request executed below the effective live-member threshold.

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
