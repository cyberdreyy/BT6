### Title
Multisig2 counts confirmations from removed members toward the execution threshold - ([File: multisig2/src/lib.rs])

### Summary
`multisig2/src/lib.rs`'s `confirm()` decides whether to execute a pending request purely by counting entries in the `confirmations: LookupMap<RequestId, HashSet<String>>` set for that request, without re-verifying that every recorded confirmer is still a current multisig member. `delete_member()` only purges requests *authored* by the removed member; it does not scrub that member's confirmations from *other* still-pending requests they merely confirmed. This lets a request execute once `confirmations.len() + 1 >= num_confirmations`, even though one or more of those confirmations belong to an account that has since been removed from the multisig — i.e. a request can execute with fewer live-member confirmations than the configured threshold.

### Finding Description
The invariant the contract is supposed to guarantee is: `confirmations from current members ≥ num_confirmations` before a request executes.

- `confirm()` only checks that the *caller* is a current member (`assert_valid_request` → `current_member().is_some()`), then unconditionally trusts the stored `confirmations` set size: [1](#0-0) 

- `delete_member()` removes the departing member and deletes only the requests *they authored* (`r.member == member`). It never inspects or cleans the `confirmations` HashSet of requests authored by *other* members that this member had already confirmed: [2](#0-1) 

- `MultisigMember` identity is stored as a serialized string key inside `HashSet<String>` for confirmations, independent of live membership state, so once added it is never revalidated: [3](#0-2) [4](#0-3) 

The result: `confirmations recorded on a request` can diverge from `confirmations by accounts that are still authorized members` — the exact "confirmations counted versus live members" custody binding this scan is meant to catch. A request (e.g. a `Transfer` draining the multisig's NEAR balance) can be pushed over threshold and executed even though the number of *currently valid* member confirmations is below `num_confirmations`.

### Impact Explanation
This directly matches the Critical category "a multisig request executed below threshold." An attacker who is (or colludes with) a subset of members smaller than `num_confirmations` can arrange for a stale confirmation from a just-removed member to count toward execution, letting a `Transfer`/`FunctionCall`/`DeployContract` request run with fewer live approvals than the account's configured security threshold requires. Since multisig contracts typically custody NEAR (and can call other contracts, e.g. wNEAR), this can result in unauthorized movement of funds.

### Likelihood Explanation
Requires only ordinary multisig operations available to members: creating/confirming a request, and later removing a member via a normal `DeleteMember` request (which itself only needs the standard threshold of live confirmations to execute). No owner/foundation/validator privilege beyond being one of the N members is needed, and the sequence (create request → get partial confirmations → remove one confirmer via a separate, unrelated `DeleteMember` request → obtain one more confirmation from a remaining member) is straightforward to trigger deliberately.

### Recommendation
When counting confirmations in `confirm()` (and when computing `get_confirmations`/`get_num_confirmations` for a request), filter the stored `confirmations` set to only members still present in `self.members` before comparing against `num_confirmations`. Additionally, `delete_member()` should scrub the removed member's entry from the `confirmations` set of every pending request (not just requests they authored), or `confirm()` should lazily discard stale entries for non-members before evaluating the threshold.

### Proof of Concept
1. Initialize `MultiSigContract::new(members = [M1, M2, M3, M4], num_confirmations = 3)`.
2. `M2.add_request_and_confirm(R)` where `R = Transfer{amount}` — creates request `R`, `confirmations[R] = {M2}`.
3. `M3.confirm(R)` — `confirmations[R] = {M2, M3}` (len 2 < 3, not executed).
4. Separately, members create and confirm a `DeleteMember{M3}` request with the required 3 live confirmations (e.g. M1, M4, M2), which executes `delete_member` for M3. Because `R`'s author is `M2` (not `M3`), `R` and its confirmations set are left untouched by `delete_member`; `confirmations[R]` still contains `M3` even though M3 is no longer in `self.members`.
5. `M4.confirm(R)` → `confirmations[R].len() + 1 == 3 >= num_confirmations (3)` → `execute_request(R)` runs the `Transfer`, even though only `M2` and `M4` are currently live confirmers (2 live confirmations, below the configured threshold of 3). [1](#0-0) [2](#0-1)

### Citations

**File:** multisig2/src/lib.rs (L94-106)
```rust
/// Represents member of the multsig: either account or access key to given account.
#[derive(Debug, BorshDeserialize, BorshSerialize, Clone, PartialEq, Serialize, Deserialize)]
#[serde(crate = "near_sdk::serde", untagged)]
pub enum MultisigMember {
    AccessKey { public_key: PublicKey },
    Account { account_id: AccountId },
}

impl ToString for MultisigMember {
    fn to_string(&self) -> String {
        serde_json::to_string(&self).unwrap_or_else(|_| env::panic_str("Failed to serialize"))
    }
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
