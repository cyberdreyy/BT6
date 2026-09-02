I found a concrete analog to the reported bug class in `multisig2/src/lib.rs`. The custody binding at risk is the multisig authorization guarantee: confirmations counted must correspond to live members. Let me verify there's no other check remedying this.### Title
Stale confirmations from deleted multisig members allow requests to execute below the intended live-member threshold - ([File: multisig2/src/lib.rs])

### Summary
`MultiSigContract::confirm` in `multisig2/src/lib.rs` tallies a request's confirmation count purely from the `HashSet<String>` stored under `self.confirmations`, without verifying that each entry still corresponds to a current member of `self.members`. When a member is removed via `DeleteMember`, `delete_member` only purges confirmations/requests that member itself *submitted* — it never scrubs that member's votes from other pending requests where it acted only as a *confirmer*. This lets a request execute using votes from accounts that are no longer multisig members.

### Finding Description
`confirm()` checks the caller is a current member (`current_member()`… `self.members.contains`) and enforces the threshold as: [1](#0-0) 
but the confirmations already stored in the set are never re-validated against the current `members` set — only the *new* confirming caller is checked.

`delete_member()` removes a departing member from `self.members`, and cleans up only requests *they authored*: [2](#0-1) 
Note the filter `r.member == member` at line 365 matches on the request's *original submitter*, not on entries inside `self.confirmations`. Any confirmation the removed member cast on a request *authored by someone else* is left untouched in the `confirmations` map.

This breaks the intended equality:
`confirmations.len() (should equal) == count of votes from accounts in members`
After a member is deleted, the left side can still include that ex-member's vote, so
`confirmations.len() > |{live members who voted}|`.

### Impact Explanation
This is a Critical-impact case per the custody-binding classes in scope: *"a multisig request executed below threshold."* A request can reach `num_confirmations` and be executed (transfers, `AddKey`, `DeployContract`, `AddMember`/`DeleteMember`, etc. — see the action set at [3](#0-2)  ) with fewer *live* member confirmations than the configured `K` of `K-of-N` scheme, undermining the entire authorization guarantee documented in [4](#0-3) .

### Likelihood Explanation
This requires only ordinary, expected multisig usage — no privileged access beyond being one of the existing members (which the threat model already allows to submit/confirm requests), and no reliance on a redeploy, foundation action, or victim key: 
1. A malicious/compromised member B confirms an outstanding request R authored by another member A.
2. Through the normal governance flow, B is later removed via a `DeleteMember` request (a legitimate, unrelated action, e.g. because B's key was thought to be compromised or B left the organization).
3. B's stale confirmation on R remains counted.
4. Fewer honest, still-live members than `num_confirmations` are needed to push R over the threshold and execute it.

### Recommendation
When deleting a member, iterate all `confirmations` entries (not just requests authored by that member) and remove the departing member's `to_string()` key from every confirmation `HashSet`. Alternatively, when tallying in `confirm()`, filter `confirmations` down to entries that are still `self.members.contains(...)` before comparing against `self.num_confirmations`.

### Proof of Concept
1. Initialize `MultiSigContract::new(members: [A, B, C, D], num_confirmations: 3)`.
2. `A.add_request(R)` — creates request `R` (e.g., `Transfer` to attacker-controlled account), `confirmations[R] = {}`.
3. `B.confirm(R)` — `confirmations[R] = {B}` (1 vote, below threshold of 3).
4. Separately, a legitimate governance request `DeleteMember{member: B}` reaches quorum and executes; `delete_member` removes B from `members` and only cleans requests where `r.member == B` — `R` (authored by A) is untouched, so `confirmations[R]` still contains `B`.
5. `A.confirm(R)` — `confirmations[R] = {B, A}`, size 2.
6. `C.confirm(R)` — `confirmations.len() as u32 + 1 = 3 >= num_confirmations(3)` → `execute_request(R)` fires.

Only 2 currently-live members (A, C) plus one confirmer's own vote actually approved execution, yet the vote of removed member B was still counted, satisfying a nominal "3-of-4" threshold with effectively 2 live members' consent — a multisig request executed below the intended live-member threshold.

### Citations

**File:** multisig2/src/lib.rs (L41-73)
```rust
pub enum MultiSigRequestAction {
    /// Transfers given amount to receiver.
    Transfer { amount: U128 },
    /// Create a new account.
    CreateAccount,
    /// Deploys contract to receiver's account. Can upgrade given contract as well.
    DeployContract { code: Base64VecU8 },
    /// Add new member of the multisig.
    AddMember { member: MultisigMember },
    /// Remove existing member of the multisig.
    DeleteMember { member: MultisigMember },
    /// Adds full access key to another account.
    AddKey {
        public_key: PublicKey,
        #[serde(skip_serializing_if = "Option::is_none")]
        permission: Option<FunctionCallPermission>,
    },
    /// Call function on behalf of this contract.
    FunctionCall {
        method_name: String,
        args: Base64VecU8,
        deposit: U128,
        gas: U64,
    },
    /// Sets number of confirmations required to authorize requests.
    /// Can not be bundled with any other actions or transactions.
    SetNumConfirmations { num_confirmations: u32 },
    /// Sets number of active requests (unconfirmed requests) per access key
    /// Default is 12 unconfirmed requests at a time
    /// The REQUEST_COOLDOWN for requests is 15min
    /// Worst gas attack a malicious keyholder could do is 12 requests every 15min
    SetActiveRequestsLimit { active_requests_limit: u32 },
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

**File:** multisig2/README.md (L5-14)
```markdown
This contract provides:
 - Set K out of N multi sig scheme
 - Request to sign transfers, function calls, adding and removing keys.
 - Any of the access keys or set of specified accounts can confirm, until the required number of confirmation achieved.

## Multisig implementation details

Multisig uses set of `FunctionCall` `AccessKey`s and account ids as a set of allowed N members. 
When contract is being setup, it should be initialized with set of members that will be initially managing this account.
All operations going forward will require `K` members to call `confirm` to be executed.
```
