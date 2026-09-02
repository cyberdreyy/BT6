### Title
Stale confirmations from removed members still count toward the execution threshold - (File: `multisig2/src/lib.rs`)

### Summary
`delete_member` only purges pending *requests that a removed member created*, but it never scans the `confirmations` map to strip that member's votes off requests created by *other* members. `confirm` then counts those stale votes toward `num_confirmations` with no re-validation that every counted voter is still a live member, so a request can execute with fewer *live* signers than the configured threshold, exactly mirroring the Karak H-04 pattern where a "removed" party's prior action (an unregistered operator's exposure) is still honored after removal instead of being invalidated together with the removal.

### Finding Description
`confirm()` fetches the stored confirmation set for a request and checks only its cardinality against `self.num_confirmations`, without checking whether each entry in that set still belongs to `self.members`: [1](#0-0) 

`delete_member()` removes the member from `self.members` and deletes the access key, and cleans up pending *requests whose creator (`r.member`)* is the member being removed — but it does **not** iterate `self.confirmations` to remove that member's string from confirmation sets of requests created by other members: [2](#0-1) 

Concretely:
- Member `B` (of `{A, B, C}`, `num_confirmations = 2`) creates request `R` (a `FunctionCall`/`Transfer`) without confirming.
- Member `A` calls `confirm(R)` → `confirmations = {"A"}` (1/2), stored via `self.confirmations.insert(&request_id, &confirmations)`.
- The members separately pass a `DeleteMember { member: A }` request; `delete_member` removes `A` from `self.members` and deletes `A`'s key, but `R`'s confirmation set is untouched because `r.member == B`, not `A` — the loop at `multisig2/src/lib.rs:362-371` never matches `R`.
- `A` is now not a member — `self.members.contains(&A) == false` — yet the string `"A"` remains inside `confirmations.get(&R)`.
- Member `C` calls `confirm(R)`. `confirmations.len() as u32 + 1 = 2 >= self.num_confirmations (2)` → the request executes via `execute_request`, even though only one *currently live* member (`C`) actually approved it at that time; `A`'s vote is a ghost of a removed identity.

This is the same class of bug as the Karak finding: an action taken while a party held a status ("registered operator" / "current member") is honored later even though that party's status has since been revoked, because the system that revokes status doesn't retroactively invalidate the artifacts (a queued slash / a stored confirmation) tied to that party. The equality that should hold is:

`confirmations counted at execution == confirmations from accounts that are still members at execution`

but the contract instead enforces:

`confirmations stored at execution >= num_confirmations` (regardless of current membership of each voter)

### Impact Explanation
This lets a request be finalized with fewer than `num_confirmations` *currently authorized* signers, i.e. **a multisig request executed below threshold** — explicitly one of the listed Critical impacts (funds moved/transferred, keys added, contracts deployed, or members added/removed by a party not entitled to make that decision at execution time). Any `Transfer`, `AddKey`/`AddMember`, or `FunctionCall` action queued this way can move NEAR or grant privileges using an authorization count that no longer reflects reality, directly breaking the K-of-N custody guarantee the contract exists to provide.

### Likelihood Explanation
No compromised keys or external exploitation are needed — this occurs through entirely legitimate, expected multisig operation: normal request creation, normal confirmation by a legitimate member, and a normal, later `DeleteMember` action (e.g., rotating out a departing member, or reacting to a suspected key compromise). Any time membership changes while a request is pending with partial confirmations, the stale-vote condition exists silently; the bug requires no attacker collusion, only ordinary sequencing of routine operations, making it readily triggerable in production usage of `multisig2`.

### Recommendation
When executing `DeleteMember`, iterate `self.confirmations` (not just `self.requests` filtered by creator) and remove the deleted member's string from every request's confirmation set, or alternatively have `confirm()` recompute confirmations by filtering the stored set against `self.members` before comparing to `self.num_confirmations`. Either fix ensures only currently-live members' approvals count toward the threshold at the moment of execution.

### Proof of Concept
```rust
// members = {A, B, C}; num_confirmations = 2
// 1. B creates request R (Transfer) without confirming
let request_id = c_as_B.add_request(transfer_request.clone());

// 2. A confirms R -> confirmations = {"A"} (1/2), R not executed
c_as_A.confirm(request_id);

// 3. Members pass a separate, fully-confirmed request removing A
//    delete_member() only strips requests where r.member == A; R (created by B) is untouched
c_as_members.add_request_and_confirm(delete_member_A_request);
// now self.members == {B, C}; confirmations.get(&request_id) still == {"A"}

// 4. C confirms R
c_as_C.confirm(request_id);
// confirmations.len() + 1 = 2 >= num_confirmations (2) -> execute_request runs
// but only C is a currently live, willing member; A's stale vote made up the "2nd" signature
``` [1](#0-0) [2](#0-1)

### Citations

**File:** multisig2/src/lib.rs (L294-315)
```rust
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
