### Title
Multisig `DeleteMember` fails to purge a removed member's existing confirmations on other members' requests, allowing execution below the live-member threshold - (File: `multisig2/src/lib.rs`)

### Summary
`MultiSigContract::confirm()` counts a request's `confirmations: HashSet<String>` regardless of whether each recorded confirmer is still a current member. `delete_member()` only purges requests that the removed member *originated*, not confirmations that member placed on requests originated by others. A confirmation cast before removal therefore keeps counting toward `num_confirmations` after the confirmer is deleted from `members`, letting a request execute with fewer *live* member approvals than the configured K-of-N threshold.

### Finding Description
The intended custody binding is: `confirmations counted toward execution == confirmations from members currently in self.members`. This invariant is broken.

- `confirm()` increments/checks the confirmation count purely from the stored `HashSet<String>` for the request, without re-validating that each entry in the set is still in `self.members`: [1](#0-0) 
- `delete_member()` (invoked via `MultiSigRequestAction::DeleteMember` in `execute_request`) removes the departing member from `self.members` and deletes only requests *they authored* (`r.member == member`), and clears `num_requests_pk` for them - but it never scans `self.confirmations` for entries where the removed member is merely a *confirmer* on someone else's still-open request: [2](#0-1) 
- `assert_valid_request()`, called at the top of `confirm()`, only validates that the *caller* is a current member and that the request/confirmations map exist - it performs no re-validation of previously stored confirmers: [3](#0-2) 

Concretely, with `num_confirmations = K`:
1. Member A creates a request (e.g. `Transfer`, `AddKey`, `FunctionCall`) via `add_request`. [4](#0-3) 
2. Member B confirms it, `confirmations.len() == 1 < K`, so the confirmation is just stored. [5](#0-4) 
3. Member B is removed via a separate `DeleteMember` request (approved by other members). `delete_member` only deletes requests *authored* by B; A's request (authored by A, merely confirmed by B) is untouched, and B's stale entry stays in `confirmations`. [6](#0-5) 
4. Remaining live members continue confirming A's request. When `confirmations.len() + 1 >= K` is reached, `execute_request` fires - but one of the counted confirmations came from B, who is no longer a member. The request executes with only `K-1` live-member approvals. [7](#0-6) 

This is the direct analog of the external report's root cause: a stale, no-longer-authorized principal (there, the old `PirexGmx` contract still trusted as `pirexRewards`'s producer after migration; here, a removed multisig member whose prior confirmation is still trusted after `DeleteMember`) continues to influence privileged logic (`claimRewards()` there; threshold-gated `execute_request` here) because the transition (`migrateReward()` there; `delete_member()` here) does not scrub the stale reference/vote.

### Impact Explanation
This breaks the threshold binding explicitly called Critical in scope: "a multisig request executed below threshold." Any privileged action gated by `num_confirmations` - `Transfer`, `AddKey` (granting a new full/limited access key), `DeployContract` (code upgrade), `FunctionCall`, `AddMember`/`DeleteMember` itself - can be executed with fewer genuinely-authorized confirmations than the configured K, undermining the entire K-of-N security guarantee of the account (and any funds/keys it controls).

### Likelihood Explanation
No special privilege is required beyond being a legitimate member at confirmation time (a normal, expected role) — the flaw is purely in the missing cleanup path during member removal, which is a routine multisig maintenance operation (e.g., offboarding a team member or rotating a compromised key). Any multisig that partially confirms requests before a `DeleteMember` action is exposed; this does not require malicious intent from the confirming member, only that removal happens after they confirm but before the request reaches quorum or is otherwise cleaned up.

### Recommendation
When executing `DeleteMember`, iterate over all entries in `self.confirmations` (not just requests authored by the removed member) and strip the removed member's string from every confirmation set; alternatively, revalidate at `confirm()`/`execute_request()` time that every recorded confirmer in the set is still present in `self.members`, discarding stale entries (and decrementing/recomputing the count) before comparing against `num_confirmations`.

### Proof of Concept
1. Deploy `multisig2` with members `[A, B, C, D]`, `num_confirmations = 3`.
2. A calls `add_request` with a `Transfer` action (or `AddKey`) — request `id=0` created, 0 confirmations.
3. B calls `confirm(0)` — `confirmations[0] = {B}`, count = 1 < 3, stored.
4. C and D jointly create+confirm a `DeleteMember { member: B }` request to remove B (reaches quorum, executes `delete_member`), which deletes only requests B *authored* — request `0` (authored by A) is untouched; `confirmations[0]` still contains B.
5. C calls `confirm(0)` — count becomes 2 (B, C) < 3, stored.
6. D calls `confirm(0)` — `confirmations.len() + 1 == 3 >= num_confirmations`, so `execute_request` fires and the `Transfer`/`AddKey` executes, even though B is no longer a member — the request executed with only 2 live-member confirmations (C, D) against a nominal 3-of-N policy.

### Citations

**File:** multisig2/src/lib.rs (L169-200)
```rust
    /// Add request for multisig.
    pub fn add_request(&mut self, request: MultiSigRequest) -> RequestId {
        let current_member = self.current_member().unwrap_or_else(|| {
            env::panic_str(
                "Predecessor must be a member or transaction signed with key of given account",
            )
        });
        // track how many requests this key has made
        let num_requests = self
            .num_requests_pk
            .get(&current_member.to_string())
            .unwrap_or(0)
            + 1;
        assert(
            num_requests <= self.active_requests_limit,
            "Account has too many active requests. Confirm or delete some.",
        );
        self.num_requests_pk
            .insert(&current_member.to_string(), &num_requests);
        // add the request
        let request_added = MultiSigRequestWithSigner {
            member: current_member,
            added_timestamp: env::block_timestamp(),
            request,
        };
        self.requests.insert(&self.request_nonce, &request_added);
        let confirmations = HashSet::new();
        self.confirmations
            .insert(&self.request_nonce, &confirmations);
        self.request_nonce += 1;
        self.request_nonce - 1
    }
```

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
