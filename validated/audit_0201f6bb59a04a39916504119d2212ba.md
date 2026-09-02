## Analog Found: Stale confirmations from removed multisig members still count toward execution threshold

The DNS bug's root cause — a value recorded under one "version"/identity being read back and trusted under a stale, no-longer-valid state — has a direct structural analog in `multisig2/src/lib.rs`. The binding that should hold is:

`confirmations.len() >= num_confirmations` ⇒ `num_confirmations` distinct **current, live** members have confirmed the request

But the contract lets confirmations recorded by a member who has since been deleted continue to count toward that threshold, because member deletion only purges *requests originated by* the deleted member — not the deleted member's *confirmation entries on other pending requests*.

### Root cause

`confirm()` stores the confirming member's identity as a raw string in a `HashSet<String>` keyed to the request, and executes once the set size reaches `num_confirmations`: [1](#0-0) 

`delete_member()` only cleans up requests that were *added by* the removed member; it never scans other pending requests' `confirmations` sets to strip the removed member's entry: [2](#0-1) 

Nothing in `assert_valid_request` (called from `confirm`) re-validates the *existing* confirmations against the current `members` set — it only checks that the caller invoking `confirm` right now is still a member: [3](#0-2) 

### Exploit scenario
1. Multisig initialized with members `{A, B, C, D}`, `num_confirmations = 3`.
2. Member `A` (or `D`) creates request `R` (e.g. `Transfer` to an attacker-controlled account), and `D` confirms it → `confirmations(R) = {D}`.
3. `B` confirms `R` → `confirmations(R) = {D, B}` (2/3, not yet executed) — see the threshold check at [4](#0-3) .
4. Separately, the multisig legitimately executes a `DeleteMember { member: D }` request (e.g. because `D`'s key was compromised or `D` left). `delete_member` only removes requests **created by** `D`; request `R` (created by `A`) is untouched, and `R`'s confirmation set still contains `D`'s stale entry: [5](#0-4) .
5. Members are now `{A, B, C}`, so a legitimate 3-of-3 (or K-of-3) approval should require 3 *live* members. But `C` simply confirms `R`: `confirmations.len() as u32 + 1 >= 3` evaluates to `3 >= 3`, and the transfer executes — using `D`'s phantom, no-longer-valid confirmation as one of the three votes: [6](#0-5) .

The transfer request executed with only 2 currently-authorized members (`B`, `C`) actually approving it, one vote short of the true current threshold — funds move (`Transfer`, `AddKey`, `DeployContract`, etc.) despite the K-of-N guarantee being violated.

### Impact
Critical — a multisig request (including `Transfer` of NEAR, `AddKey`, or `DeployContract`) can execute with fewer than `num_confirmations` *live* members having approved it, because a removed member's stale confirmation is never purged from pending requests they didn't create.

### Recommendation
When a member is deleted, iterate all pending `requests`/`confirmations` and remove that member's entry from every confirmation set (not just requests they authored), or alternatively re-validate at execution time that every entry in a request's confirmation set still belongs to `self.members`.

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
