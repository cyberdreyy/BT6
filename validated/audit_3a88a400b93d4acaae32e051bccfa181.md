### Title
Stale confirmations from removed multisig members allow requests to execute below the current confirmation threshold - (File: `multisig2/src/lib.rs`)

### Summary
`delete_member` in the `multisig2` contract only purges requests that were *created* by the removed member; it does not purge confirmations that the removed member previously cast on *other* still-pending requests. Because `confirm()` counts confirmations purely by size of the stored `HashSet<String>` without re-validating that every entry still belongs to a current member, a request can later reach `num_confirmations` and execute even though one (or more) of the counted confirmations came from an account/key that is no longer a member. This breaks the binding "confirmations counted == confirmations from live members," letting a multisig request execute with effectively fewer live confirmations than the configured threshold.

### Finding Description
`add_request`/`confirm` record confirmations in `self.confirmations: LookupMap<RequestId, HashSet<String>>`, keyed only by `request_id`, with each member's confirmation added by `member.to_string()`: [1](#0-0) 

`delete_member` is the only place that reconciles state when a member leaves. It removes *requests whose creator (`r.member`) equals the removed member*, and removes the member from `num_requests_pk` and `members`, but it never scans `self.confirmations` for entries belonging to the removed member on requests created by someone else: [2](#0-1) 

`assert_valid_request` (called from `confirm`) only checks that the *caller* is currently a member and that the request/confirmations maps have entries — it never validates that the *previously stored* confirmations still belong to current members: [3](#0-2) 

Sequence that breaks the binding:
1. Members A, B, C, D exist, `num_confirmations = 3`.
2. B creates request R (`add_request`, `r.member = B`). A confirms R (`confirmations = {A}`, still short of 3).
3. Through a separate, properly-threshold-confirmed multisig action, member A is removed via `DeleteMember`. `delete_member` only deletes requests created *by A*; R was created by B, so R and its confirmation set `{A}` remain untouched, even though A is no longer a member.
4. Now only B, C, D are current members (3 members, `num_confirmations` still 3). C confirms R → `confirmations = {A, C}` (size 2, still short of 3, since B as creator hasn't confirmed yet — but B can add itself, or if B already implicitly counted via `add_request_and_confirm` the count could already be 2).
5. D confirms R → count becomes 3 (`{A, C, D}` or similar), meeting `confirmations.len() as u32 + 1 >= self.num_confirmations`, and `execute_request(R)` runs.

The executed request was approved by only 2 genuinely current members (C and D) plus one stale confirmation from a removed member (A), i.e. it executed with confirmations counted (3) diverging from confirmations by live members (2) — below the intended 3-of-N threshold.

### Impact Explanation
This falls under the Critical category "a multisig request executed below threshold." Any action reachable through a multisig request — transferring NEAR, deploying a contract, adding a full-access key, adding/removing members — can be pushed through with fewer genuinely live approvals than configured, undermining the entire security guarantee of the multisig (`num_confirmations`-of-N).

### Likelihood Explanation
This does not require a compromised deployment or ignored initialization — it is a direct consequence of the documented `delete_member` / `confirm` flow. It is triggered by an ordinary, expected operational event: removing a member (e.g., rotating out a compromised or departing signer) while a request that member had already confirmed is still outstanding. No special privilege beyond being a (soon-to-be-removed) member and the remaining members proceeding normally is needed; the bug is latent in every multisig2 deployment that ever removes a member with pending confirmed-but-unexecuted requests in flight.

### Recommendation
When removing a member in `delete_member`, iterate over `self.requests`/`self.confirmations` and strip the removed member's entry from every confirmation set (not just requests they authored), re-checking whether removal drops any request below the confirmation threshold. Alternatively, validate at `confirm()`/execution time that every public key/account in a request's confirmation set is still a current member before counting it toward `num_confirmations`.

### Proof of Concept
```rust
// members: A, B, C, D; num_confirmations = 3
let request_id = contract.add_request(transfer_request); // created by B
// A confirms
contract.confirm(request_id); // as A -> confirmations = {A}

// Separately, via a fully-threshold-approved DeleteMember request, A is removed.
contract.delete_member(promise, MultisigMember::Account { account_id: "A".to_string() });
// R (created by B) is untouched; confirmations for R still contain "A"

// C confirms
contract.confirm(request_id); // as C -> confirmations = {A, C}
// D confirms -> len()+1 >= 3 triggers execute_request, even though A is no longer a member
contract.confirm(request_id); // as D -> executes with only B/C/D live, one stale "A" confirmation counted
``` [1](#0-0) [2](#0-1) [3](#0-2)

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
