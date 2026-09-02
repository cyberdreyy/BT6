## Title
Stale confirmations from removed multisig members are still counted toward `num_confirmations`, allowing requests to execute below the live-member threshold - (File: `multisig2/src/lib.rs`)

### Summary
`MultiSigContract::confirm` in [1](#0-0)  counts every entry stored in the per-request `confirmations: HashSet<String>` to decide whether `num_confirmations` has been reached. `delete_member`, invoked when executing a `DeleteMember` request, only purges requests and confirmation sets for requests that were *created* by the removed member, and never scans other pending requests' `confirmations` sets to strip an entry belonging to the member being removed. As a result, a confirmation recorded by a member who is later removed from the multisig keeps counting toward the threshold for any request they did not create, letting a request execute with fewer live, currently-authorized confirmations than `num_confirmations` requires.

### Finding Description
The `confirm` function increments/consults a per-request `confirmations` set keyed by `member.to_string()`: [1](#0-0) 

Membership validity is only checked at the moment `confirm` is *called* (via `current_member()` / `assert_valid_request`), not retroactively for confirmations already recorded: [2](#0-1) [3](#0-2) 

When a `DeleteMember` action executes, `delete_member` cleans up only the requests whose *creator* (`r.member`) equals the removed member: [4](#0-3) 

It does not iterate `self.confirmations` to remove the deleted member's `to_string()` entry from confirmation sets belonging to requests created by *other* members. So if member `B` confirmed request `R` (created by `A`) before `B` is removed via a separate `DeleteMember(B)` request, `R`'s `confirmations` set still contains `B`. Any subsequent confirmation from a still-live member re-evaluates `confirmations.len() + 1 >= num_confirmations` using the stale entry, and `R` can execute (transfer funds, deploy code, add keys, etc.) with one fewer live, currently-authorized signer than `num_confirmations` mandates.

This breaks the equality the contract is supposed to enforce: `confirmations counted == confirmations from currently-live members`. After `B` is removed, the live-member confirmation count for `R` is 2 (`A`, and whoever confirms after removal), yet the contract treats it as 3 because of `B`'s stale entry.

### Impact Explanation
This is a Critical-severity authorization bypass under the listed impact criteria ("a multisig request executed below threshold"). An attacker who has (or had) access to one member's key can get that key to confirm an outstanding malicious `Transfer`/`FunctionCall`/`AddKey` request before that key is revoked/removed (e.g., as part of routine key rotation or incident response), and the request still executes later once it reaches the *nominal* count, even though the actual number of live, authorized signers is one below the configured `num_confirmations`. This can move funds (`Transfer`), grant access (`AddKey`/`AddMember`), or deploy arbitrary code (`DeployContract`) on the multisig account without full current-member consent.

### Likelihood Explanation
Likelihood is High in any realistic multisig operational flow: removing a member (e.g., because a key was compromised or an employee left) is a normal, expected event, and it is plausible that the removed member had already confirmed one or more still-pending requests before removal. No special privilege beyond being (or having been) a legitimate member is required to plant the stale confirmation; the exploit only requires that removal happens after a confirmation and before the request is otherwise resolved.

### Recommendation
When executing `DeleteMember`, iterate over `self.confirmations` for all pending requests (not just those created by the removed member) and strip the removed member's entry from each confirmation set (optionally decrementing/re-validating counts). Alternatively, when counting confirmations in `confirm`/`execute_request`, re-validate that every account in the stored `confirmations` set is still `self.members.contains(...)` before counting it toward `num_confirmations`.

### Proof of Concept
1. Initialize `MultiSigContract::new` with members `[A, B, C, D]` and `num_confirmations = 3`.
2. `A` calls `add_request_and_confirm(R)` where `R` is a `Transfer` to an attacker-controlled account. `confirmations[R] = {A}`.
3. `B` calls `confirm(R)`. `confirmations[R] = {A, B}` (len 2 < 3, not yet executed).
4. Members detect `B`'s key is compromised. `C` creates `add_request_and_confirm(R2)` where `R2 = DeleteMember{B}`; `D` and `A` also call `confirm(R2)`, reaching 3 confirmations and executing `delete_member(B)` — see [4](#0-3) . `B` is removed from `self.members` and its access key deleted, but `R`'s `confirmations[R] = {A, B}` is untouched because `R.member == A`, not `B`.
5. `C` (a live member) calls `confirm(R)`. Inside `confirm`, `confirmations.len()` is 2, `+1 = 3 >= num_confirmations(3)`, so `R` (the `Transfer`) is executed — see [5](#0-4) . The transfer executes with confirmations from only 2 currently-live members (`A`, `C`) plus the stale, no-longer-valid confirmation from removed member `B`, one short of the required 3 live confirmations.

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

**File:** multisig2/src/lib.rs (L321-339)
```rust
    /// Returns current member: either predecessor as account or if it's the same as current account - signer.
    fn current_member(&self) -> Option<MultisigMember> {
        let member = if env::current_account_id() == env::predecessor_account_id() {
            MultisigMember::AccessKey {
                public_key: env::signer_account_pk()
                    .try_into()
                    .unwrap_or_else(|_| env::panic_str("Failed to deserialize public key")),
            }
        } else {
            MultisigMember::Account {
                account_id: env::predecessor_account_id(),
            }
        };
        if self.members.contains(&member) {
            Some(member)
        } else {
            None
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
