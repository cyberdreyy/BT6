This is a valid finding. `ldapGroupMembersListToUser` (shared function at `core/sessions/ldapauth/ldap.go` line 764) stores `userEmail := strings.TrimPrefix(uidComponent, "uid=")` without any case normalization, unlike `CreateSession`'s `strings.ToLower(sr.Email)` at line 445, or `FindUser`'s `strings.ToLower(email)` at line 116. [1](#0-0) [2](#0-1) 

The `Work` function builds `upstreamUserStateMap` keyed on this raw-cased email [3](#0-2)  and separately builds `existingSessionsMap` keyed on the lowercase `user_email` from the `ldap_sessions` table [4](#0-3) . The purge and role-sync logic both do exact string-key lookups (`upstreamUserStateMap[ldapSession.UserEmail]`, `existingSessionsMap[email]`) with no case folding [5](#0-4) [6](#0-5) .

### Title
LDAP session role downgrade/removal bypass due to case-sensitive email key mismatch in upstream sync - (File: core/sessions/ldapauth/sync.go)

### Summary
`ldapGroupMembersListToUser` populates the upstream state map with emails parsed verbatim from the LDAP `uniqueMember` DN (`uid=` value, original case preserved), while `ldap_sessions.user_email` is always stored lowercase by `CreateSession`. If the LDAP directory returns a `uniqueMember` value whose `uid=` casing differs from the lowercase form recorded at login, `Work()`'s map lookups (`existingSessionsMap[email]`, `upstreamUserStateMap[ldapSession.UserEmail]`) fail to match, so neither the purge-on-removal path nor the `UPDATE ldap_sessions SET user_role = CASE...` role-sync path fires for that user, leaving a stale, previously-cached elevated role in place indefinitely.

### Finding Description
The root cause is inconsistent case normalization between two code paths that must agree on the same key space:
- `ldap.go` `CreateSession` (line 445) inserts `strings.ToLower(sr.Email)` into `ldap_sessions.user_email`.
- `ldap.go`/`sync.go` shared helper `ldapGroupMembersListToUser` (line 764) builds `userEmail` directly from the parsed `uid=` component of the `uniqueMember` LDAP attribute with no `ToLower` call, then this is used as the map key in `sync.go`'s `Work` for `upstreamUserStateMap` (lines 165-172).

In `Work`, three critical operations rely on exact map key equality between these two differently-cased sources: the emails-to-purge computation (lines 216-219), the API-token purge computation (lines 222-227), and the `CASE WHEN user_email = $n` role-sync builder (lines 249-258), which only appends a role update for emails present in both `upstreamUserStateMap` and `existingSessionsMap`/`existingAPITokensMap`. Since Go map lookups are case-sensitive, if the upstream directory server returns `uid=` values with different letter casing than what was captured at login (a scenario plausible with many real LDAP schemas that don't strictly normalize `uid` casing, e.g. Active Directory-backed LDAP proxies), then for a demoted/removed user: (1) the purge won't match `ldapSession.UserEmail` against `upstreamUserStateMap`, so the row isn't deleted; and (2) the role sync CASE clause won't build a matching `WHEN` for that email, so the stale elevated role in `ldap_sessions` remains untouched (`ELSE user_role END` preserves the old value).

This is not stopped by any other check: `AuthorizedUserWithSession` (ldap.go lines 345-373) trusts the cached `user_role` from `ldap_sessions` directly with no re-validation against LDAP on each request — the cache is only refreshed by the periodic/rate-limited `Work()` sync, which is defeated by this case mismatch.

### Impact Explanation
An LDAP user who was previously granted Admin (or Edit) role retains their session's cached elevated `user_role` in `ldap_sessions` even after being removed from the upstream admin/edit group, as long as the directory's `uid=` casing for that user differs from the lowercase form stored at session creation. Since `AuthorizedUserWithSession` and `FindUserByAPIToken` both trust this cached role without re-checking upstream, the attacker retains admin-level API access — including key-export and other admin-only endpoints — after revocation. This maps to Chainlink's "authorization bypass / privilege escalation leading to unauthorized access to sensitive node data" bounty impact class.

### Likelihood Explanation
Requires: (1) attacker previously held a legitimate elevated LDAP role and an active session/API token, (2) upstream LDAP removes/demotes them, and (3) the directory's `uniqueMember`/`uid=` value casing for that user differs from the lowercase value stored at login time. Condition (3) depends on the specific upstream LDAP server's data hygiene and is not guaranteed for all deployments, making this a real but environment-dependent (moderate-likelihood) issue rather than universally exploitable — it requires no additional attacker action beyond normal usage and waiting, so once the precondition holds, exploitation is 100% reliable and persists indefinitely until natural session/token expiry.

### Recommendation
Normalize casing consistently across the entire LDAP email key space: apply `strings.ToLower(userEmail)` in `ldapGroupMembersListToUser` when constructing `sessions.User{Email: userEmail, ...}` (ldap.go line ~764-767), ensuring upstream state map keys always match the lowercase convention used by `CreateSession`, `FindUser`, and the `ldap_sessions`/`ldap_user_api_tokens` tables. Additionally, consider using SQL-level case-insensitive comparisons (`lower(user_email) = lower($n)`) in the `Work()` purge and CASE-WHEN queries as defense in depth.

### Proof of Concept
Go unit test in `core/sessions/ldapauth/sync_test.go` (or new test file):
1. Seed `ldap_sessions` table with a row: `user_email = "attacker@example.com"` (lowercase), `user_role = 'admin'`, `localauth_user = false`.
2. Mock `ldapClient`/`LDAPConn.Search` so `ldapGroupMembersListToUser` returns for the View group a `uniqueMember` value `uid=Attacker@Example.com,ou=users,...` (mixed case), and returns empty/no membership for Admin/Edit groups — simulating the demotion.
3. Call `LDAPServerStateSyncer.Work(ctx)`.
4. Query `ldap_sessions` for `user_email = "attacker@example.com"` and assert `user_role` is still `'admin'` (unchanged) instead of being downgraded to `'view'` or purged, demonstrating the stale-role persistence.
5. As a control, repeat with matching-case `uid=attacker@example.com` and assert the role is correctly downgraded/purged, confirming the case-sensitivity mismatch is the root cause.

### Citations

**File:** core/sessions/ldapauth/ldap.go (L440-448)
```go
	session := sessions.NewSession()
	_, err = l.ds.ExecContext(
		ctx,
		"INSERT INTO ldap_sessions (id, user_email, user_role, localauth_user, created_at) VALUES ($1, $2, $3, $4, now())",
		session.ID,
		strings.ToLower(sr.Email),
		foundUser.Role,
		isLocalUser,
	)
```

**File:** core/sessions/ldapauth/ldap.go (L748-769)
```go
	// Get string list of members from 'uniqueMember' attribute
	uniqueMemberValues := result.Entries[0].GetAttributeValues(UniqueMemberAttribute)
	for _, uniqueMemberEntry := range uniqueMemberValues {
		parts := strings.Split(uniqueMemberEntry, ",") // Split attribute value on comma (uid, ou, dc parts)
		uidComponent := ""
		for _, part := range parts { // Iterate parts for "uid="
			if strings.HasPrefix(part, "uid=") {
				uidComponent = part
				break
			}
		}
		if uidComponent == "" {
			lggr.Errorf("unexpected LDAP group query response for unique members - expected list of LDAP Values for uniqueMember containing LDAP strings in format uid=test.user@example.com,ou=users,dc=example,dc=com. Got %s", uniqueMemberEntry)
			continue
		}
		// Map each user email to the sessions.User struct
		userEmail := strings.TrimPrefix(uidComponent, "uid=")
		users = append(users, sessions.User{
			Email: userEmail,
			Role:  roleToAssign,
		})
	}
```

**File:** core/sessions/ldapauth/sync.go (L165-172)
```go
	upstreamUserStateMap := make(map[string]sessions.User)
	dedupedEmails := []string{}
	for _, user := range users {
		if _, ok := upstreamUserStateMap[user.Email]; !ok {
			upstreamUserStateMap[user.Email] = user
			dedupedEmails = append(dedupedEmails, user.Email)
		}
	}
```

**File:** core/sessions/ldapauth/sync.go (L204-212)
```go
		// Create existing sessions and API tokens lookup map for later
		existingSessionsMap := make(map[string]LDAPSession)
		for _, sess := range existingSessions {
			existingSessionsMap[sess.UserEmail] = sess
		}
		existingAPITokensMap := make(map[string]LDAPSession)
		for _, sess := range existingAPITokens {
			existingAPITokensMap[sess.UserEmail] = sess
		}
```

**File:** core/sessions/ldapauth/sync.go (L216-227)
```go
		for _, ldapSession := range existingSessions {
			if _, ok := upstreamUserStateMap[ldapSession.UserEmail]; !ok {
				emailsToPurge = append(emailsToPurge, ldapSession.UserEmail)
			}
		}
		// Likewise for API Tokens table
		apiTokenEmailsToPurge := []any{}
		for _, ldapSession := range existingAPITokens {
			if _, ok := upstreamUserStateMap[ldapSession.UserEmail]; !ok {
				apiTokenEmailsToPurge = append(apiTokenEmailsToPurge, ldapSession.UserEmail)
			}
		}
```

**File:** core/sessions/ldapauth/sync.go (L249-258)
```go
		for email, user := range upstreamUserStateMap {
			// Only build on SET CASE statement per local session and API token role, not for each upstream user value
			_, sessionOk := existingSessionsMap[email]
			_, tokenOk := existingAPITokensMap[email]
			if !sessionOk && !tokenOk {
				continue
			}
			emailValues = append(emailValues, email)
			fmt.Fprintf(&queryWhenClause, "WHEN user_email = $%d THEN '%s' ", len(emailValues), user.Role)
		}
```
