### Title
Multisig request can execute below its confirmation threshold because stale confirmations from removed members are never purged - ([File: multisig2/src/lib.rs])

### Summary
`MultiSigContract::confirm` counts entries in the `confirmations` `HashSet<String>` to decide whether a request has reached `num_confirmations` and should be executed. `delete_member` only purges requests *authored* by the removed member; it never scans other requests' `confirmations` sets to strip out the removed member's prior approvals. A later, still-current member's confirmation can therefore push a request over threshold even though one of the counted approvals belongs to a member who has since been deleted, letting a `Transfer`/`FunctionCall`/etc. request execute with fewer genuinely live approving identities than `num_confirmations` requires.

### Finding Description
`confirm` validates only that the *caller* is a current member, then relies purely on the size of the stored confirmation set: [1](#0-0) 

`assert_valid_request`/`current_member` only check that the address invoking `confirm` right now is a member; they never re-validate the *previously stored* entries in `confirmations`: [2](#0-1) [3](#0-2) 

When a member is removed via `DeleteMember`, `delete_member` deletes only requests whose original submitter (`r.member`) is the removed member, and removes `num_requests_pk` bookkeeping for that member. It does not touch the `confirmations` map for requests submitted by *other* members that the removed member had already confirmed: [4](#0-3) 

Sequence that breaks the "confirmations counted == live members that approved" binding:
1. Multisig has 4 members {A,B,C,D}, `num_confirmations = 3`.
2. Member A calls `add_request` for a `Transfer` (request `R`), no auto-confirm.
3. Members B and C call `confirm(R)` → `confirmations = {B, C}` (len 2, below threshold, not yet executed).
4. Separately, members A, B, D approve a `DeleteMember { member: C }` request (allowed since `4-1 >= 3`, per the check in `delete_member`). C is removed from `self.members`; `R`'s `confirmations` set is untouched and still contains `"C"`.
5. Member D now calls `confirm(R)`. `current_member()` succeeds for D (D is still a member), `confirmations.len() (2, still containing stale "C") + 1 (D) = 3 >= num_confirmations (3)` → the request executes.

At execution time the real, live approving membership for `R` is only {B, D} — 2 members — while the code believes it reached the 3-of-4 threshold because a stale confirmation from removed member C is still counted.

### Impact Explanation
This is a threshold-bypass on a multisig contract: a request (including a `Transfer` of NEAR out of the multisig account, or a privileged `AddKey`/`FunctionCall`) can be executed with fewer currently-authorized approvals than `num_confirmations` mandates. This matches the Critical impact category "a multisig request executed below threshold" — funds or privileged actions can move without the intended number of live signers agreeing, which is a direct custody/authorization-binding violation (confirmations recorded vs. members actually current and consenting).

### Likelihood Explanation
No privileged access is required beyond normal multisig member participation that is already assumed in the threat model (any subset of members acting normally, including one that is later removed for legitimate reasons such as key rotation or key compromise). The scenario requires: (a) a request accumulating some but not all confirmations, (b) a legitimate `DeleteMember` action on one of the members who already confirmed that pending request, and (c) a further confirmation from a still-live member. All of these are ordinary, expected multisig operations (member churn + pending requests coexisting) — no exploit of cryptography or race condition is needed, only ordinary operational sequencing, making this readily reachable in normal usage, especially when member removal is used defensively (e.g., removing a compromised/departing signer) while requests are in flight.

### Recommendation
When a member is deleted, `delete_member` should also purge that member's confirmation entries from every pending request's `confirmations` set (not just requests they authored), or alternatively `confirm` should re-validate, at confirmation time, that every entry in the stored `confirmations` set still corresponds to a current member (filtering stale entries) before comparing the count against `num_confirmations`.

### Proof of Concept
Conceptual reproduction using the contract's own public API (`multisig2/src/lib.rs`):
```
1. new(members = [A, B, C, D], num_confirmations = 3)
2. as A: add_request(Transfer{ amount, receiver_id: attacker }) -> request_id = R
3. as B: confirm(R)      // confirmations = {B}
4. as C: confirm(R)      // confirmations = {B, C}, len 2 < 3, not executed
5. as A: add_request_and_confirm(DeleteMember{ member: C }) -> request_id = R2
6. as B: confirm(R2)
7. as D: confirm(R2)     // 3 confirmations reached, C is removed from self.members;
                         // confirmations map for R2 is cleaned via remove_request, but R's confirmations {B, C} is untouched
8. as D: confirm(R)      // current_member() succeeds for D; confirmations.len() (2, incl. stale "C") + 1 == 3 >= num_confirmations
                         // -> execute_request(Transfer) runs with only B and D as genuinely live approvers
``` [1](#0-0) [5](#0-4)

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

**File:** multisig2/src/lib.rs (L321-339)
```rust
    /// Returns current member: either predecessor as account or if it's the same as current account - signer.
    fn current_member(&self) -> Option<MultisigMember> {
        let member = if env::current_account_id() == env::predecessor_account_id() {
            MultisigMember::AccessKey {
                public_key: env::signer_account_pk()
                    .try_into()
                    .unwrap_or_else(|_| env::panic_str("Failed to deserialize public key")),
            }
        } else {
            MultisigMember::Account {
                account_id: env::predecessor_account_id(),
            }
        };
        if self.members.contains(&member) {
            Some(member)
        } else {
            None
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

**File:** multisig2/src/lib.rs (L406-420)
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
```
