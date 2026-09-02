Based on the code I was able to examine, I found a valid analog in the multisig contracts.

### Title
Confirmations from deleted members remain counted toward execution threshold, allowing a request to execute below the live-member quorum - (File: `multisig2/src/lib.rs`)

### Summary
The reported bug class is: a validation check is performed against a snapshot of state that can diverge from the state actually used at execution time, allowing an outcome to bypass an invariant the check was meant to enforce. In `multisig2/src/lib.rs`, the `confirm` function counts confirmations recorded in the `confirmations` map against `num_confirmations` [1](#0-0)  but `delete_member` only purges confirmation records for requests *originated* by the removed member — not confirmation entries where the removed member merely confirmed someone else's still-pending request [2](#0-1) .

### Finding Description
The binding that should hold is: `confirmations counted at execution == confirmations from currently-live members`. Concretely:

- `add_request` creates a request with an empty confirmation set [3](#0-2) .
- `confirm` adds the calling member's identity to `self.confirmations[request_id]`, and once `confirmations.len() + 1 >= num_confirmations`, executes the request [1](#0-0) .
- `delete_member` is the only path that cleans up confirmation state, and it does so by filtering `self.requests` for entries where `r.member == member` (i.e., requests *created* by that member), removing those requests and their confirmations entirely: [2](#0-1) . It does **not** scan other members' pending requests to strip out a confirmation contributed by the member being deleted.

Sequence showing the break:
1. Members A, B, C, D exist, `num_confirmations = 3`.
2. A calls `add_request` (or `add_request_and_confirm`), creating request R (owned by A).
3. B calls `confirm(R)` — now confirmations = {A (if add_request_and_confirm) , B}.
4. Members execute a `DeleteMember` request removing B (this only touches requests where `r.member == B`, i.e., requests B created — R is untouched since A created it).
5. B is now gone from `self.members`, yet `self.confirmations[R]` still contains B's identity.
6. C calls `confirm(R)`. The count reaches `num_confirmations` (3) using A + B(deleted) + C, even though B is no longer a member — i.e., execution proceeds with only 2 *live* confirmations against a nominal 3-of-N threshold.

This breaks the equality `live confirmations == required threshold` that the K-of-N scheme is supposed to enforce, since a stale confirmation from a removed member is silently counted as valid.

### Impact Explanation
This is a Critical-class impact under the given rules: "a multisig request executed below threshold." An attacker who is a member (or colludes with members) can arrange for a request to be pre-confirmed by a member who is later removed, effectively reducing the real quorum needed from live members to execute arbitrary `Transfer`, `AddKey`, `DeleteKey`, `FunctionCall`, or `DeployContract` actions — including moving NEAR out of the multisig account with fewer genuine authorizations than configured.

### Likelihood Explanation
Requires only actions available to existing multisig members (no owner/foundation/validator privilege beyond normal multisig membership), and no redeploy or key compromise — any member set change via `DeleteMember` combined with a pending request from a *different* member triggers this. This satisfies the "unprivileged attacker" and "no redeploy/foundation/social engineering" constraints in the rules, since all actors are already normal multisig members performing normal member/request operations.

### Recommendation
When removing a member in `delete_member` (and analogously `delete_key`/`delete_member` paths in `multisig` v1), iterate over **all** pending requests' confirmation sets (not just requests created by that member) and remove the deleted member's identity from each. Alternatively, revalidate at `confirm`-time / execution-time that every entry in the confirmation set for a request still corresponds to a current member before counting it toward `num_confirmations`.

### Proof of Concept
Given `multisig2/src/lib.rs`:
1. Deploy with members `[A, B, C, D]`, `num_confirmations = 3`.
2. `A.add_request(transfer_request)` → request id `R` (owned by A, confirmations = {}).
3. `B.confirm(R)` → confirmations(R) = {B}.
4. Owners execute a separate request: `DeleteMember(member=B)` reaching quorum from A, C, D — this calls `delete_member` which only removes requests where `r.member == B`; `R` (owned by A) is left intact with `confirmations(R) = {B}` still stored.
5. `C.confirm(R)` → `confirmations.len() (1, containing stale B) + 1 (C) = 2 >= num_confirmations? ` — depending on exact count semantics, one more live confirmation (`D.confirm(R)`) combined with the stale B entry reaches 3, even though only 2 *live* members (C, D) actually authorized it, one fewer than the configured 3-of-4 threshold.
6. The `Transfer` action in `R` executes, moving funds authorized by effectively 2 live signatures instead of 3.

Note: I was unable to fully re-verify the exact confirm-counting arithmetic and the `assert_valid_request` implementation due to tool errors on the final iteration (file read calls failed), so the exact confirmation count needed to trigger execution should be double-checked against the live `confirm` and `assert_valid_request` code in `multisig2/src/lib.rs` before treating this as fully confirmed; the `delete_member` cleanup gap itself, however, is clearly shown in the code retrieved. [2](#0-1) [1](#0-0)

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
