### Title
Removed multisig member's stale confirmation still counts toward `num_confirmations`, allowing execution below threshold - ([File: multisig2/src/lib.rs])

### Summary
`MultiSigContract::delete_member` only purges *requests originated by* the removed member, but never scrubs that member's entries from the `confirmations` set of requests that other members submitted. A confirmation cast by a member who is later removed from the multisig therefore remains counted forever, letting a request reach `num_confirmations` and execute with fewer *live* member approvals than the configured threshold.

### Finding Description
`confirm()` reads the member's identity at call time via `current_member()` and inserts `member.to_string()` into the `confirmations: LookupMap<RequestId, HashSet<String>>` set for that request: [1](#0-0) 

When a `DeleteMember` action is executed, `delete_member` removes the member from `self.members`, deletes the member's own outstanding *requests* (filtered by `r.member == member`, i.e. requests they *created*), and clears their `num_requests_pk` entry — but it never iterates the `confirmations` map to strip that member's vote from requests created by *other* members: [2](#0-1) 

Because the `confirmations` HashSet is keyed purely by the string form of the member and is never revalidated against the current `self.members` set, `confirm()`'s threshold check (`confirmations.len() as u32 + 1 >= self.num_confirmations`) treats a stale confirmation from a now-removed member exactly the same as a confirmation from a live member: [3](#0-2) 

This breaks the binding that should hold: `confirmations counted == confirmations from members currently in self.members`. Instead, `confirmations counted >= confirmations from live members`, which lets a request execute with real approval below `num_confirmations`.

### Impact Explanation
This crosses the "confirmations counted versus live members" custody boundary called out in the rules. A multisig request (e.g. `Transfer`, `AddKey`, `FunctionCall`) can be executed while having fewer genuinely authorized (currently-member) confirmations than the configured `num_confirmations` threshold, because a removed member's leftover vote is silently counted. This matches the Critical impact category: "a multisig request executed below threshold."

### Likelihood Explanation
This requires no special privilege beyond being (or having been) an ordinary multisig member — any request that collects confirmations from a member who is subsequently removed (a routine governance action such as key rotation or off-boarding) will retain that stale vote indefinitely. Any remaining member can then submit or confirm the pending request to push it over the threshold using the removed member's leftover approval, achieving execution with fewer live approvals than intended.

### Recommendation
When executing `DeleteMember`, iterate over all entries in `self.confirmations` (not just requests where the member is the original submitter) and remove the deleted member's entry from every confirmation set, or alternatively re-validate confirmation set membership against `self.members` inside `confirm()`'s threshold check before counting it toward `num_confirmations`.

### Proof of Concept
1. Deploy `MultiSigContract::new([A, B, C, D], 3)` (`num_confirmations = 3`).
2. `A` calls `add_request(R)` (e.g., a `Transfer` request). `confirmations[R] = {}`.
3. `B` calls `confirm(R)` → `confirmations[R] = {B}` (1 < 3).
4. `C` calls `confirm(R)` → `confirmations[R] = {B, C}` (2 < 3).
5. Separately, members create and confirm a `DeleteMember{C}` request with 3 valid confirmations (e.g. A, B, D), which executes `delete_member` and removes `C` from `self.members`. Because `R` was submitted by `A`, not `C`, it is not filtered/removed in `delete_member`'s cleanup loop at [4](#0-3) , and `confirmations[R]` still contains `C`.
6. `D` calls `confirm(R)` → `confirmations.len() (2) + 1 = 3 >= num_confirmations (3)` → `remove_request` and `execute_request(R)` fire, executing the transfer with only `B` and `D` as genuinely live-member approvals (`C`'s vote is stale) — one fewer live confirmation than the configured threshold of 3.

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
