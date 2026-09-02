### Title
Stale confirmations from removed multisig members are still counted toward the confirmation threshold, allowing a request to execute below the effective `K`-of-`N` requirement - (File: `multisig2/src/lib.rs`)

### Summary
This is the same class of bug as the reported `SPBinaryPrompt::getScore`/`getResult` issue: a recorded accounting value (confirmations already collected) is trusted without being reconciled against the current ground truth (the live member set) at the moment it is consumed. In `multisig2/src/lib.rs`, `confirm()` counts entries in a per-request `HashSet<String>` of confirmations against `num_confirmations` without verifying that every confirming identity is still a current member. `DeleteMember` only purges confirmation state for requests that member *created*, not confirmations that member *cast* on other pending requests, so those stale confirmations remain and can push a request past the confirmation threshold even though a live member no longer backs it.

### Finding Description
`confirm()` decides whether to execute a request purely by comparing the size of the stored confirmation set to `self.num_confirmations`: [1](#0-0) 

The binding that should hold is: `distinct confirmations from members ∈ current self.members == confirmations counted toward self.num_confirmations`.

When a member is removed via `DeleteMember`, `delete_member()` only cleans up requests that the removed member itself **added** (`r.member == member`), and removes that member's `num_requests_pk` bookkeeping and the access key/entry from `self.members`: [2](#0-1) 

It never scans the `confirmations: LookupMap<RequestId, HashSet<String>>` map to strip out entries where the removed member had previously **confirmed** (but not created) some other still-pending request. `current_member()` is only checked for the *new* confirmer in `confirm()`/`assert_valid_request()`, not retroactively for confirmations that are already stored: [3](#0-2) 

So the equality is broken: `self.members` (ground truth) no longer contains the removed member, but `confirmations[request_id]` (recorded claim) still does, and `confirm()` treats that stale entry as a valid vote toward the threshold.

### Impact Explanation
This lets a `Transfer`, `FunctionCall`, `AddKey`, etc. request execute with fewer live/current-member confirmations than `num_confirmations` actually requires — a multisig request executed below threshold, which the rules classify as Critical impact (unauthorized fund movement from an account that should require K-of-N approval).

### Likelihood Explanation
This requires no privileged access beyond being (at some point) a legitimate multisig member/key holder, and no redeploy or foundation action: any member can confirm several pending requests, then get lawfully removed via a separate `DeleteMember` request, and their earlier confirmations on the still-pending requests remain counted. Any remaining member(s) confirming afterward can inadvertently or intentionally push a request past the configured threshold using this stale vote, which is a realistic operational sequence (member turnover, key rotation, or malicious pre-staging of confirmations before requesting one's own removal).

### Recommendation
When confirming a request (or when removing a member), reconcile the confirmation set against `self.members`: either (a) have `delete_member()` iterate all pending requests' confirmation sets and strip any confirmation belonging to the removed member, or (b) have `confirm()` filter `confirmations` to only members present in `self.members` before comparing its length to `num_confirmations`.

### Proof of Concept
1. Deploy `multisig2` with members `{A, B, C, D}` and `num_confirmations = 3`.
2. `A` calls `add_request` to create request `R1` (e.g. `Transfer` to an attacker-controlled account).
3. `B` calls `confirm(R1)` → confirmations = `{A, B}` (size 2, below threshold 3).
4. `C` and enough members create+confirm a separate request `R2` = `DeleteMember{member: B}`, executing it. `B` is removed from `self.members`; `delete_member()` only cleans requests created by `B` (none), leaving `R1`'s confirmation set untouched: `{A, B}`.
5. `A` calls `confirm(R1)` again is blocked (`already confirmed`), but any other still-eligible member, e.g. `D`, calls `confirm(R1)` → confirmations size becomes 3 (`{A, B, D}`) ≥ `num_confirmations`, and `R1` executes — even though `B` is no longer a member, so only 2 *live* members (`A`, `D`) actually approved a transfer that should have required 3 live approvals. [4](#0-3) [5](#0-4)

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
