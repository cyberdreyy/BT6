Confirmed: `delete_member` only purges pending requests/confirmations for requests that the deleted member itself created (`filter_map(|(k, r)| if r.member == member ...)`), but never scrubs that member's confirmation entries from *other* pending requests' `confirmations` sets. Once removed, `self.members` no longer contains that member, yet the stale confirmation the member gave earlier is still stored and still counted toward `num_confirmations` in `confirm`.

### Title
Stale confirmations from removed members still count toward execution threshold - (File: `multisig2/src/lib.rs`)

### Summary
`MultiSigContract::delete_member` in `multisig2/src/lib.rs` removes a member from `self.members` and deletes only the requests that member itself created, but it never removes that member's confirmation entries from other members' pending requests. Because `confirm` counts confirmations purely by `HashSet<String>` length (`confirmations.len() as u32 + 1 >= self.num_confirmations`) without re-validating that each confirmer is still a current member, a request can be executed using confirmations from accounts/keys that are no longer part of the multisig, breaking the invariant "confirmations counted == confirmations by live members."

### Finding Description
The confirmation-count invariant the contract is supposed to enforce is:
`confirmations_counted(request) == confirmations_by_current_members(request)`

- `confirm` (`multisig2/src/lib.rs:294-315`) only checks that the *caller* is a current member via `assert_valid_request` → `current_member()` (`multisig2/src/lib.rs:322-339`), then increments/compares the size of the stored `confirmations: HashSet<String>` against `num_confirmations`.
- `delete_member` (`multisig2/src/lib.rs:356-379`) removes the deleted member from `self.members` and deletes requests where `r.member == member` (i.e., requests *created* by that member), and clears `num_requests_pk` for that member. It does **not** scan `self.confirmations` for entries keyed by the deleted member's `to_string()` on *other* pending requests and remove them.

Thus if member M confirms request R (adding M to `confirmations[R]`) but does not create R, and is later removed via a separate `DeleteMember` request, `confirmations[R]` still contains M's confirmation string. Any subsequent legitimate confirmation from a remaining member can push `confirmations.len() as u32 + 1 >= num_confirmations`, executing R with a confirmation set that includes a party who is no longer a member and whose "vote" no longer reflects current governance — breaking the confirmations-vs-live-members binding. [1](#0-0) [2](#0-1) 

### Impact Explanation
This matches the "High" criteria: a multisig request can be executed below the effective live-member threshold, since one of the counted confirmations belongs to a removed member. This lets a request pass with fewer than `num_confirmations` *current* legitimate approvals (e.g., a Transfer or FunctionCall action executes with N-1 live approvals plus one stale/removed-member approval), enabling unauthorized fund movement or privileged actions (add key, deploy contract, add/delete member) to be pushed through by parties no longer entitled to approve.

### Likelihood Explanation
Requires: (1) a member confirms a request but is not its creator, (2) that member is later removed via `DeleteMember`, and (3) the request remains pending and later receives another confirmation reaching threshold. This is a plausible operational sequence in normal multisig usage (member departs the org/loses trust while there are outstanding multi-step requests), and requires no privileged action beyond the standard `DeleteMember`/`confirm` flow already exposed to members — no owner/redeploy/social engineering needed beyond ordinary governance actions already in scope.

### Recommendation
In `delete_member`, iterate over all pending requests (not just ones created by the member) and remove the deleted member's key from every `confirmations` `HashSet`. Alternatively, in `confirm`/execution-threshold check, filter/re-validate the confirmations set against `self.members` before comparing its length to `num_confirmations`, so only confirmations by currently-live members count.

### Proof of Concept
1. Initialize with members `{A, B, C, D}` and `num_confirmations = 3`.
2. A creates request R (`add_request`), B confirms R (`confirm`) — `confirmations[R] = {A, B}`.
3. Separately, a `DeleteMember{member: B}` request is created and confirmed by 3 members, executing `delete_member` — B is removed from `self.members`, but `confirmations[R]` still contains B because R was created by A, not B, so the `filter_map` in `delete_member` does not touch R.
4. C confirms R: `confirmations[R].len() + 1 == 3 >= num_confirmations`, so R executes — approved by A, B(stale, removed), C, i.e., only 2 live members actually approved, yet the contract treats it as 3-of-4 confirmed. [3](#0-2)

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
