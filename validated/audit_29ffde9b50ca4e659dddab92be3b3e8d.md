### Title
Multisig `confirm()` counts stale confirmations from removed members, allowing requests to execute below the live-member threshold - ([File: multisig2/src/lib.rs])

### Summary
`MultiSigContract::delete_member` in `multisig2/src/lib.rs` only purges confirmations for requests that the removed member *authored*; it never scrubs that member's confirmation entries from requests authored by other members. Because `confirm()` decides whether to execute a request purely by counting entries in the `confirmations: HashSet<String>` for that `request_id`, a stale confirmation left behind by an ejected member still counts toward `num_confirmations`, letting a request execute (fund transfer, key/member changes, arbitrary `FunctionCall`) with fewer than the required number of currently-authorized members.

### Finding Description
`confirm()` gates execution as: [1](#0-0) 

The membership check (`current_member()`) is only applied to the account calling `confirm` right now — not retroactively to entries already stored in the `confirmations` set. Those entries are plain serialized `MultisigMember` strings inserted whenever `confirm` was previously called: [2](#0-1) 

`delete_member` is the only place that could invalidate a departed member's prior votes, but it filters requests by `r.member == member`, i.e. only the request's *creator* field, and only cleans confirmations as a byproduct of deleting those specific requests: [3](#0-2) 

Any request authored by a *different* member, on which the now-removed member had previously called `confirm()`, keeps that member's confirmation string in the `confirmations` map untouched. The binding the contract is supposed to enforce is:

`confirmations counted for request R == confirmations from members who are live members of the multisig at the time R executes`

After `delete_member` removes member `B`, this becomes:

`confirmations counted (includes B's stale vote) > confirmations from live members`

so a request can reach `num_confirmations` with fewer than `num_confirmations` currently-authorized signers.

### Impact Explanation
This is a Critical-impact issue per the specified taxonomy: "a multisig request executed below threshold." A member removed for being compromised, terminated, or malicious can leave a "ghost" confirmation on any pending request (e.g. a large `Transfer`, an `AddKey`/`AddMember` granting new access, or a `FunctionCall` to an arbitrary contract). The remaining live members, believing the multisig still requires `K` live confirmations, can unknowingly execute the request with only `K-1` (or fewer) live approvals, since the stale vote silently fills the gap. This directly breaks the multisig's core security guarantee (K-of-N authorization) and can lead to unauthorized fund transfers or unauthorized key/member additions.

### Likelihood Explanation
The scenario requires no privileged action from the attacker beyond having been, at some point, a legitimate multisig member/key holder who confirmed a request before being removed (a routine, expected operational event — e.g. rotating a compromised key or removing an employee). Any request that received partial confirmations before a member removal is affected; the remaining live members simply need to add confirmations to reach `num_confirmations` without realizing one of the counted votes belongs to a member that is no longer part of the multisig. No special timing, race condition, or extra transaction from the attacker is needed once their confirmation is recorded.

### Recommendation
When executing `DeleteMember`, iterate over all pending requests and remove the deleted member's entry from every `confirmations` set (not just requests they authored), or equivalently, validate at `confirm()`-time (and before `execute_request`) that every account/key in the stored confirmation set is still a current member, discarding any stale entries before counting them against `num_confirmations`.

### Proof of Concept
1. Initialize multisig with members `{A, B, C, D}` and `num_confirmations = 3`.
2. `A` calls `add_request` to create request `R` (e.g. `Transfer` of a large amount to an external account). `R.member = A`.
3. `B` calls `confirm(R)` → `confirmations[R] = {B}`.
4. `C` calls `confirm(R)` → `confirmations[R] = {B, C}` (2 of 3, not yet executed).
5. Members legitimately vote to remove `B` (e.g., because `B`'s key was compromised) via a separate `DeleteMember{member: B}` request that reaches quorum and executes `delete_member`. Because `R.member == A ≠ B`, `R` and its confirmations `{B, C}` are left untouched — `B` is now removed from `self.members`, but `confirmations[R]` still contains `B`'s string.
6. `D` (a current, live member) calls `confirm(R)` → `confirmations.len() + 1 = 3 >= num_confirmations (3)` → `execute_request(R)` runs the `Transfer`.
7. Result: `R` executed with only 2 currently-live confirmers (`C`, `D`) plus a stale vote from removed member `B`, i.e. below the intended 3-of-4 live-member threshold. [3](#0-2) [1](#0-0)

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
