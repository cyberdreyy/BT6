## Title
Stale confirmations from removed multisig members are still counted toward the execution threshold, allowing a request to execute with fewer live approvers than `num_confirmations` - (File: `multisig2/src/lib.rs`, also present in `multisig/src/lib.rs`)

### Summary
The multisig contracts count the *size* of the `confirmations` set stored per request against `num_confirmations` to decide when to execute a request. When a member is removed via `DeleteMember` (or `DeleteKey` in the legacy `multisig`), the code only purges pending requests and confirmation records for requests *originated* by that member. It never scrubs that member's confirmation entries from *other* pending requests they had previously confirmed. Those stale confirmations remain counted, so a request can later execute with strictly fewer *live* members having approved it than `num_confirmations` requires.

### Finding Description
`confirm()` compares the stored confirmation-set size against the threshold and executes once satisfied: [1](#0-0) 

`delete_member()` is the only cleanup path run when a member is removed. It removes requests/confirmations for requests where `r.member == member` (i.e., requests the removed member *added*), and removes the member's `num_requests_pk` entry — but it does **not** scan `self.confirmations` for entries belonging to `member` on requests added by *other* members: [2](#0-1) 

The equality that should hold is:
```
count(confirmations[request_id]) == count(distinct *current* members who confirmed request_id)
```
After a `DeleteMember` action, this equality breaks: `confirmations[request_id]` can still contain the string of a member who no longer belongs to `self.members`, while `assert_valid_request`/`current_member()` only checks that the *caller* is a current member — it never re-validates the pre-existing entries in the set: [3](#0-2) 

The same pattern exists in the legacy `multisig` contract: `DeleteKey` only removes requests where `r.signer_pk == pk`, leaving that key's confirmation entries on other pending requests intact: [4](#0-3) 

### Impact Explanation
This breaks the core custody/authorization binding of the multisig: "confirmations counted" no longer equals "live members who approved." A request can execute (transferring NEAR, deploying new contract code, adding a full-access key, etc. — see `execute_request`) with fewer than `num_confirmations` currently-valid members having signed off on it, because a since-removed member's stale confirmation still counts. This is a multisig request executed below its configured threshold, directly matching the Critical impact category ("a multisig request executed below threshold").

### Likelihood Explanation
This requires no external attacker privilege beyond ordinary multisig operation: any pending, not-yet-executed request that has partial confirmations, followed by a routine `DeleteMember`/`DeleteKey` governance action removing one of those confirmers (e.g., key rotation, offboarding, compromised-key removal), is sufficient to leave a stale confirmation in place. No malicious governance vote or extra privilege is needed — it is a natural sequence of legitimate operations (partial confirm → remove that confirmer → remaining members confirm) that silently lowers the effective quorum.

### Recommendation
When removing a member, iterate over **all** pending requests' confirmation sets (not only requests the member added) and strip that member's entry, decrementing/adjusting as needed. Alternatively, when counting confirmations in `confirm()`, filter `confirmations[request_id]` against `self.members` (i.e., recompute the live count by intersecting with `self.members` instead of trusting the raw set length) before comparing to `num_confirmations`.

### Proof of Concept
Assume `MultiSigContract::new(members = [A, B, C, D], num_confirmations = 3)`:
1. `A` calls `add_request_and_confirm(R)` → `confirmations[R] = {A}`, `requests[R].member = A`.
2. `B` calls `confirm(R)` → existing len (1) + 1 = 2 < 3, so it just inserts: `confirmations[R] = {A, B}`.
3. Separately, the group executes a `DeleteMember { member: B }` self-request (approved by the required threshold at that time) → `delete_member(promise, B)` runs: it only removes requests where `r.member == B` (none, since `R.member == A`), removes `B` from `members` and revokes its key, but leaves `confirmations[R] = {A, B}` untouched, per [5](#0-4) .
4. `C` (a remaining live member) calls `confirm(R)` → existing len (2) + 1 = 3 ≥ `num_confirmations` (3), so `execute_request(R)` runs via [6](#0-5) .
5. Result: `R` executes with confirmations nominally "3 of 4," but only `A` and `C` are actually current members — `B`'s confirmation was stale/revoked. The request executed with 2 live approvals against a 3-of-4 threshold.

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
