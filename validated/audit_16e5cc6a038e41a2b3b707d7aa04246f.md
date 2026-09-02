## Title
Stale confirmations from removed members still count toward request execution threshold - (`multisig2/src/lib.rs`)

### Summary
The `multisig2` contract's `delete_member` function removes a member from the multisig set but fails to purge that member's existing confirmations from other members' pending requests. As a result, a request that received a confirmation from a member *before* that member was removed can still be executed later, counting the stale confirmation toward the current `num_confirmations` threshold — even though that member is no longer part of the multisig.

### Finding Description
The custody binding that the multisig is supposed to enforce is:
`live-member confirmations counted at execution time == num_confirmations threshold`

`confirm()` executes a request once `confirmations.len() + 1 >= self.num_confirmations`, where `confirmations` is a raw `HashSet<String>` of member identifiers collected over the life of the request: [1](#0-0) 

`delete_member` is the only place member removal happens. It removes the member from `self.members` and deletes requests that were *originated* by that member, but it never scans `self.confirmations` to strip that member's entry from requests originated by *other* members: [2](#0-1) 

Concretely, with members `{A, B, C, D}` and `num_confirmations = 3`:
1. `A` creates request `R` via `add_request` (no auto-confirm).
2. `B` confirms `R` → `confirmations = {B}`.
3. `C` confirms `R` → `confirmations = {B, C}` (2 < 3, not yet executed).
4. Multisig separately executes a `DeleteMember { member: B }` request (a normal, properly-authorized multisig action) — `B` is removed from `self.members`, but `confirmations` for `R` still contains `"B"` because `delete_member` only clears requests where `r.member == B` (i.e., requests *added by* B), not confirmations *given by* B on other requests.
5. `D` confirms `R` → `confirmations.len() (2) + 1 >= 3` → request executes, using B's stale confirmation as one of the three "confirmations," even though B is no longer a member.

This breaks the K-of-N guarantee stated in the contract's own README: "Any of the access keys or set of specified accounts can confirm, until the required number of confirmation achieved" — the number achieved is meant to reflect live members, not historical ones. [3](#0-2) 

### Impact Explanation
This is Critical impact: a multisig request (including a `Transfer` action moving NEAR/funds, or `AddKey`/`FunctionCall`) can be executed with fewer live-member confirmations than the configured threshold, because a removed member's stale confirmation is still counted. This directly violates the "multisig request executed below threshold" custody binding — funds could be moved or privileged actions taken without the intended number of currently-trusted signers agreeing.

### Likelihood Explanation
The precondition (member removal happening while another request they confirmed is still pending) is a normal operational sequence for any active multisig with member turnover (e.g., rotating signers, removing a compromised or departing member) — no attacker privilege beyond being one of the remaining legitimate confirmers (`D` in the example) is required to trigger execution once the stale state exists.

### Recommendation
When executing `DeleteMember`, iterate over all entries in `self.confirmations` and remove the deleted member's identifier from every confirmation set, not just from requests originated by that member. Alternatively, re-validate at confirm/execute time that every account/key in `confirmations` is still a current member (filtering stale identifiers) before comparing against `num_confirmations`.

### Proof of Concept
1. Deploy `multisig2` with members `[A, B, C, D]`, `num_confirmations = 3`.
2. `A.add_request(R)` (creates R with 0 confirmations).
3. `B.confirm(R)` → confirmations = `{B}`.
4. `C.confirm(R)` → confirmations = `{B, C}`.
5. Get 3 members to pass a `DeleteMember { member: B }` request (normal governance action) — `B` removed from `members`, but `R`'s confirmation set untouched.
6. `D.confirm(R)` → `len({B,C}) + 1 = 3 >= num_confirmations (3)` → `R` executes using `B`'s stale, no-longer-valid confirmation. [1](#0-0) [2](#0-1)

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

**File:** multisig2/README.md (L121-130)
```markdown
### State machine

Per each request, multisig maintains next state machine:
 - `add_request` adds new request with empty list of confirmations.
 - `add_request_and_confirm` adds new request with 1 confirmation from the adding key.
 - `delete_request` deletes request and ends state machine.
 - `confirm` either adds new confirmation to list of confirmations or if there is more than `num_confirmations` confirmations with given call - switches to execution of request. `confirm` fails if request is already has been confirmed and already is executing which is determined if `confirmations` contain given `request_id`.
 - each step of execution, schedules a promise of given set of actions on `receiver_id` and puts a callback.
 - when callback executes, it checks if promise executed successfully: if no - stops executing the request and return failure. If yes - execute next transaction in the request if present.
 - when all transactions are executed, remove request from `requests` and with that finish the execution of the request.   
```
