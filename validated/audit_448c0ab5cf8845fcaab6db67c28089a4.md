### Title
Multisig confirmations from a removed member persist on requests submitted by other members, allowing execution below the true confirmation threshold - (File: multisig2/src/lib.rs)

### Summary
`MultiSigContract::delete_member` only purges pending requests and confirmation sets for the request(s) originally *submitted* by the member being removed. It never scans other members' outstanding requests to strip the removed member's stale confirmation entries. Since `confirm()` counts raw entries in the `confirmations: LookupMap<RequestId, HashSet<String>>` set against `num_confirmations`, a confirmation cast by a member who is later removed continues to count toward the threshold on any request that member did not author, letting the contract execute an action with fewer than `num_confirmations` *live* members having approved it.

### Finding Description
The invariant the K-of-N multisig is supposed to enforce is:

```
confirmations_counted(request) == confirmations_by_current_members(request)
```

`delete_member` breaks this equality: [1](#0-0) 

It filters `self.requests` for entries where `r.member == member` (i.e., requests *created* by the removed member) and deletes only those requests and their confirmation sets: [2](#0-1) 

It does not iterate `self.confirmations` to remove the departing member's identifier (`member.to_string()`) from confirmation sets belonging to requests authored by *other* members. Those stale confirmations remain stored under `self.confirmations`.

`confirm()` then trusts the raw size of the stored confirmation set plus the new confirmer: [3](#0-2) 

```rust
if confirmations.len() as u32 + 1 >= self.num_confirmations {
    let request = self.remove_request(request_id);
    self.execute_request(request)
} ...
```

Neither `confirm` nor `assert_valid_request` (see below) validates that every entry inside the stored `HashSet<String>` is still a current member of `self.members`. [4](#0-3) 

So the binding "confirmations counted" == "confirmations by accounts currently trusted as members" is violated: a request can be executed by `num_confirmations - 1` *current* members plus one stale confirmation from a member who has since been removed.

### Impact Explanation
This is Critical impact per the rubric: "a multisig request executed below threshold." A K-of-N multisig's entire security model rests on requiring K *currently authorized* approvals before moving funds, deploying code, adding full-access keys, or changing `num_confirmations`/membership itself. With this bug, once any member has ever confirmed a request and is later removed, the remaining members effectively only need `num_confirmations - 1` live confirmations to execute that specific pending request (e.g., a `Transfer` or `AddKey` request), silently lowering the security threshold below what was configured and what other members believe is required.

### Likelihood Explanation
This requires no owner/foundation/redeploy/social engineering — it is reachable purely through the documented, unprivileged multisig workflow: any member submits a request (`add_request`), gets it partially confirmed by other members, later one of those confirmers is removed via a normal `DeleteMember` action (a routine operation, e.g. offboarding an employee or rotating a compromised key), and the stale confirmation is never cleaned up. Any request that received a partial confirmation before the confirmer's removal is permanently primed to execute with one fewer live confirmation than intended, for as long as the request remains pending. Given `active_requests_limit` allows many pending requests, and member turnover (`DeleteMember`) is an expected, common operation, this is a realistic path that undermines fund custody guarantees.

### Recommendation
When removing a member in `delete_member`, iterate over all pending requests (not just those authored by the removed member) and strip the removed member's entry from every stored confirmation `HashSet`. Alternatively, validate membership of every confirmer at `confirm()`-time (lazily re-derive the effective count by filtering `confirmations` against `self.members` before comparing to `num_confirmations`), so stale confirmations from removed members never count toward the threshold.

### Proof of Concept
1. Initialize multisig with members `[A, B, C, D]` and `num_confirmations = 3`.
2. `B` calls `add_request(transfer_to_attacker)` → `request_id = X` (submitted by `B`, confirmations = `{}`).
3. `A` calls `confirm(X)` → confirmations = `{A}` (1 of 3, request not yet executed).
4. Members execute a separate, properly-quorate `DeleteMember { member: A }` request (3 confirmations, legitimately reaches threshold) → `A` is removed from `self.members`. Because request `X` was authored by `B`, not `A`, `delete_member`'s cleanup loop (`r.member == member`) does **not** touch request `X` or its confirmation set; `X`'s confirmations remain `{A}`.
5. `C` calls `confirm(X)` → confirmations = `{A, C}`, size 2; `2 + 1 >= 3` → request `X` executes the transfer.
6. Only `B` (implicitly, by submitting) and `C` are current members who acted on `X`; `A`'s stale confirmation from before removal was counted, so the transfer executed with only 2 live-member approvals instead of the required 3. [5](#0-4) [6](#0-5)

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
