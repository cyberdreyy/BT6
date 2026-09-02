Confirmed: in `multisig2/src/lib.rs`, `delete_member` only purges outstanding requests *initiated* by the removed member (`r.member == member`), and only removes that member's `num_requests_pk` entry.### Title
Stale confirmations from deleted members are still counted toward the confirmation threshold, allowing a multisig request to execute below the required number of live approvers - ([File: multisig2/src/lib.rs])

### Summary
`delete_member()` in `multisig2/src/lib.rs` only discards requests *initiated* by the removed member and clears that member's `num_requests_pk` entry, but it never scrubs the removed member's name out of the `confirmations: LookupMap<RequestId, HashSet<String>>` map for requests initiated by *other* members. Since `confirm()` decides whether to execute purely by comparing the raw size of that `HashSet` to `num_confirmations`, a confirmation cast by a member who is later removed keeps counting toward the threshold forever.

### Finding Description
`confirm()` executes a request once `confirmations.len() + 1 >= self.num_confirmations` [1](#0-0) . The set of confirmations is keyed only by a serialized `MultisigMember` string, with no re-validation that every entry in the set is still a current member of `self.members` at execution time.

`delete_member()` removes stale state, but only for requests where the *deleted member is the original requester* (`r.member == member`); confirmations that the deleted member placed on requests created by *someone else* are left untouched in the `confirmations` map: [2](#0-1) 

Concretely:
1. Member A creates a request (`add_request`) requiring `K` confirmations.
2. Member M (also a current signer) confirms it via `confirm()`, adding `M`'s string to the `HashSet` for that `request_id`.
3. M is later removed via a `DeleteMember` request (e.g., they leave the org or are revoked for unrelated reasons). `delete_member()` runs, but since `r.member == A` (not `M`) for this request, the confirmations set is left as-is, still containing `M`.
4. `K - 1` (or fewer) *currently valid* members confirm the same request. Because M's stale confirmation is still counted, `confirmations.len()` reaches `K` and `execute_request()` fires the `Transfer`/`FunctionCall`/etc, even though only `K-1` (or fewer) live, currently-authorized members actually approved it.

This breaks the invariant the contract advertises — that any state-changing multisig action requires `K` of the *current* `N` members to confirm — the binding `count(confirmations) == count(live members who approved)` no longer holds once membership changes between confirmations.

### Impact Explanation
This directly matches the Critical impact category "a multisig request executed below threshold." A transfer, `FunctionCall`, `AddKey`/`AddMember`/`DeleteMember` action can be authorized by fewer live, currently-trusted signers than the configured `num_confirmations`, effectively lowering the security threshold of the multisig without anyone explicitly agreeing to that. In the worst case a removed/former signer's leftover approval combines with legitimate but insufficient remaining approvals to move funds out of the account.

### Likelihood Explanation
This requires no privileged action beyond what is already possible for an unprivileged former signer: it only requires (a) a member to confirm a request before being removed, and (b) that member later being removed for any reason while the request remains pending (well within the 12 max active requests / 15-minute deletion cooldown window described in the contract). Membership churn (revoking a departing employee, rotating keys, removing a compromised key) is an expected multisig operation, and nothing in `delete_member` or `confirm` protects against this ordering, making the flaw reachable in normal contract use, not merely a theoretical edge case.

### Recommendation
When executing/counting confirmations, validate that every account/key in the stored confirmation set is still `self.members.contains(&member)`, filtering out stale entries before comparing against `num_confirmations` (or actively prune the `confirmations` set for all requests, not just those authored by the removed member, inside `delete_member`).

### Proof of Concept
1. Deploy `multisig2` with `members = [A, B, M, D]`, `num_confirmations = 3`.
2. `A.add_request_and_confirm(transfer_request)` → confirmations = `{A}` (1/3).
3. `M.confirm(request_id)` → confirmations = `{A, M}` (2/3).
4. `A.add_request_and_confirm(DeleteMember{member: M})`, `B.confirm(...)`, `D.confirm(...)` reach 3/3 and execute, removing M from `members` (M's own added request for the delete gets purged by `delete_member`, but the earlier transfer request from step 2, whose `r.member == A`, is untouched since `r.member != M`).
5. Now members = `[A, B, D]`, still `num_confirmations = 3`, and `confirmations[transfer_request_id]` still equals `{A, M}`.
6. `B.confirm(transfer_request_id)` → `confirmations.len() + 1 == 3 >= num_confirmations` → `execute_request` fires the transfer, even though only `A` and `B` are currently valid signers who approved (2 live approvers, not 3).

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
