Confirmed: `assert_valid_request` in `multisig2/src/lib.rs` (lines 406-423) only checks that the *current caller* is a member and that the request/confirmations exist — it never revalidates that previously recorded confirmations in `self.confirmations` still belong to accounts/keys that are still in `self.members`. The `confirm()` function at lines 292-315 simply counts `confirmations.len() as u32 + 1 >= self.num_confirmations` and executes the request once that threshold is reached, with no filtering of stale confirmations from removed members.

### Title
Stale confirmations from removed members are still counted toward the execution threshold, allowing a multisig request to execute below the live-member confirmation count - (File: multisig2/src/lib.rs)

### Summary
`MultiSigContract::confirm()` in `multisig2/src/lib.rs` counts confirmations stored in the `confirmations: LookupMap<RequestId, HashSet<String>>` map without checking whether the accounts/keys that produced those confirmations are still members of the multisig. A member who confirms a pending request and is later removed via `DeleteMember` still has their confirmation counted, so a request can reach `num_confirmations` and execute even though fewer than `num_confirmations` *current* members actually approved it.

### Finding Description
The binding the multisig is supposed to enforce is: `count(confirmations by accounts ∈ current members) >= num_confirmations` before `execute_request` runs. Instead the code enforces: `count(confirmations recorded historically) >= num_confirmations`, regardless of whether those confirming identities are still in `self.members`.

Walking through the code:
- `confirm()` ( [1](#0-0) ) calls `assert_valid_request(request_id)` and then simply checks `confirmations.len() as u32 + 1 >= self.num_confirmations` to decide whether to execute the request.
- `assert_valid_request()` ( [2](#0-1) ) only validates that the *caller of `confirm()` right now* is a current member (`current_member().is_some()`), and that the request/confirmations map entries exist. It performs no iteration over the existing `confirmations` set to strip out members that have since been removed.
- `delete_member()` ( [3](#0-2) ) removes the member from `self.members` and from `num_requests_pk`, and purges only requests that were *added* by that member (`self.requests.iter().filter_map(...)`); it does **not** scan `self.confirmations` for other, unrelated requests that this member had previously confirmed and does not remove that member's confirmation entry from those other requests' `HashSet<String>`.

Concretely: Members A, B, C, D with `num_confirmations = 3`. A creates request R (unrelated to membership). B and C confirm R (2/3, request stays pending since 2 < 3). Separately, a different request removes member C via `DeleteMember` (that removal only requires 3 confirmations from the *current* member set, so A, B, D can approve it while C is unaware). C is now no longer a member, but their earlier confirmation string is still present in `self.confirmations.get(&R)`. D then confirms R: `confirmations.len() as u32 + 1` = `2 + 1 = 3 >= num_confirmations`, so `execute_request(R)` fires — using C's stale confirmation as one of the three, even though C is no longer part of the multisig. Effectively R executed with only 2 live-member confirmations (B and D) plus one confirmation from a removed member, one short of true `K`-of-`N` consent.

### Impact Explanation
This directly breaks the equality the contract is meant to guarantee: `live-member confirmations for request == num_confirmations` before any `Transfer`, `FunctionCall`, `AddKey`, `AddMember`, or `DeleteMember` action executes. A request (including a `Transfer` of NEAR held by the multisig account) can be executed with fewer than the configured threshold of *currently authorized* members, which matches the Critical impact category: "a multisig request executed below threshold." This can lead to unauthorized movement of NEAR funds held by the multisig account.

### Likelihood Explanation
This does not require compromising any keys or the foundation; it only requires the ordinary flow of legitimate multisig operations: a member being removed (a normal governance action already supported by `DeleteMember`) while they have outstanding, un-executed confirmations on other pending requests. Any multisig that rotates membership over time — a normal, expected lifecycle event — is exposed, so the likelihood is not purely theoretical, though it depends on timing (a confirmation must be outstanding when the confirming member is removed).

### Recommendation
When executing `DeleteMember`, iterate over all entries in `self.confirmations` (or maintain a reverse index from member to the requests they've confirmed) and remove the deleted member's confirmation from every pending request's `HashSet`. Alternatively, in `confirm()`, before comparing against `num_confirmations`, filter `confirmations` to only those entries that are still present in `self.members` (or re-derive membership validity per confirming identity at confirmation-counting time), so that only confirmations from currently-live members count toward the threshold.

### Proof of Concept
1. Deploy `multisig2` with `members = [A, B, C, D]`, `num_confirmations = 3`.
2. A calls `add_request` with an arbitrary `Transfer` request `R` to some receiver — `add_request` ( [4](#0-3) ).
3. B calls `confirm(R)` → `confirmations = {B}` (1 < 3, stays pending) — `confirm()` ( [1](#0-0) ).
4. C calls `confirm(R)` → `confirmations = {B, C}` (2 < 3, stays pending).
5. Separately, A creates and (with A, B, D confirming, i.e. 3/4 current members) executes a `DeleteMember { member: C }` request — this removes C from `self.members` via `delete_member()` ( [3](#0-2) ), but `R`'s `confirmations` set still contains C's identity because no cleanup of unrelated pending requests occurs.
6. D calls `confirm(R)` → `confirmations.len() + 1 = 2 + 1 = 3 >= num_confirmations (3)` → `execute_request(R)` fires, transferring funds, even though C — one of the three counted confirmers — is no longer a member of the multisig. [1](#0-0) [3](#0-2) [2](#0-1)

### Citations

**File:** multisig2/src/lib.rs (L170-200)
```rust
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
