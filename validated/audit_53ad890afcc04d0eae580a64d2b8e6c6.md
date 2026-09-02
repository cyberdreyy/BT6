## Title
Stale confirmations from removed multisig members still count toward the execution threshold - (File: `multisig2/src/lib.rs`)

## Summary
`MultiSigContract::delete_member` only purges pending requests that the removed member *created*; it never scans the `confirmations` map to strip that member's approval from requests created by *other* members. Because `confirm()` decides whether to execute a request purely from `confirmations.len()` versus `self.num_confirmations`, a stale confirmation left behind by an ex-member is still counted, letting a request execute with fewer *live* member approvals than the configured threshold.

## Finding Description
`add_request` seeds an empty confirmation set per request, and `confirm()` inserts the caller's identity (as `member.to_string()`) into that set and executes the request once the set size plus one reaches `num_confirmations`: [1](#0-0) 

`delete_member` removes a member from `self.members`, deletes their `num_requests_pk` entry, and deletes only the requests *they originally created* — it does not touch `self.confirmations` entries for requests created by other members that this member had already confirmed: [2](#0-1) 

This breaks the equality the multisig is supposed to guarantee: `confirmations counted == confirmations by current, live members`. After a member is removed, `confirmations counted > confirmations by live members` for any request that the removed member had confirmed but not created, because that stale entry is never purged.

## Impact Explanation
This falls under the explicitly listed Critical impact: "a multisig request executed below threshold." A `Transfer`, `AddKey`, `AddMember`/`DeleteMember`, `FunctionCall`, or any other multisig action can be executed with fewer genuinely live confirmations than `num_confirmations` requires, because one (or more) of the counted confirmations belongs to an account/key that is no longer a member at execution time. This undermines the entire security guarantee of the k-of-n multisig.

## Likelihood Explanation
This requires only normal operational sequencing, not any implausible privilege escalation:
1. A pending request exists with some (but not all) required confirmations.
2. One of the confirming members is later removed via a separate, legitimately-passed `DeleteMember` action (e.g., normal signer rotation, or removing a suspected-compromised key).
3. The originally pending request is still open; its confirmation set still includes the now-removed member's stale entry.
4. Remaining members supply the rest of the confirmations and the request executes, using the stale confirmation to reach threshold.

This can happen inadvertently during routine member rotation, or can be engineered by a soon-to-be-removed member who confirms high-value pending requests before agreeing to (or being forced into) removal, effectively "banking" influence beyond their tenure as a member.

## Recommendation
When removing a member in `delete_member`, iterate over `self.confirmations` for **all** pending requests (not just ones created by that member) and strip the departing member's identity string from each confirmation set. Alternatively/additionally, in `confirm()`, before comparing against `num_confirmations`, filter the confirmation set to only entries whose corresponding member is still present in `self.members`, so that only currently-live approvals count toward the threshold.

## Proof of Concept
Assume members `{A, B, C, D}`, `num_confirmations = 3`:

1. `A.add_request_and_confirm(R)` → `confirmations[R] = {A}`.
2. `B.confirm(R)` → `confirmations[R] = {A, B}` (2 of 3, not yet executed) — see the threshold check at [3](#0-2) .
3. Separately, `A`, `C`, `D` create and confirm a `DeleteMember { member: B }` request, which passes with 3 confirmations and calls `delete_member(promise, B)`. Since `R` was created by `A` (not `B`), it is **not** in the `request_ids` filtered/removed set at [4](#0-3) , so `confirmations[R]` remains `{A, B}` even though `B` is now removed from `self.members`.
4. `C.confirm(R)` → `confirmations.len() (2) + 1 = 3 >= num_confirmations (3)` → `R` executes via `execute_request`, even though the current live membership only actually approved with `{A, C}` (2 live confirmations) plus one stale, no-longer-valid confirmation from removed member `B`.

The request executes below the intended 3-of-current-members threshold.

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
