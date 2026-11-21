# Where the VAN details came from

The server and its fake VAN follow three sources. This lists which one each piece rests on, so it's
clear what to check first against a real key.

## From the official docs (docs.ngpvan.com)

- Base URL `https://api.securevan.com/v4`; HTTP Basic auth with the Application Name as username
  and the API key plus `|0` (My Voters) or `|1` (My Campaign) as password.
- `GET /apiKeyProfiles` (introspection) and its fields.
- `GET /events`: `startingAfter` and `startingBefore` exclude the date given; 10 per page by
  default, 50 at most; `$expand` of `locations,codes,shifts,roles,notes`. Event and shift shapes.
- `GET /events/{eventId}`.
- `GET /signups` needs `eventId` or `vanId`; signup shape (person, event, shift, role, status,
  location, time overrides). `GET /signups/statuses` returns a bare array.
- `GET /folders`; `GET /savedLists` with `folderId`, `maxDoorCount`, `maxPeopleCount`, returning
  `listCount` and `doorCount`; `GET /savedLists/{id}`. The docs say the people on a list are not
  available from this endpoint.
- `GET /activistCodes` (50 per page, 200 at most, `statuses` defaults to Active) and
  `GET /people/{vanId}/activistCodes`.
- `GET /surveyQuestions` with responses.
- `GET /canvassResponses/resultCodes` and `/contactTypes`, both bare arrays.
- `POST /people/{vanId}/canvassResponses`: `canvassContext` (`contactTypeId`, `inputTypeId`
  defaulting to 11 for API, `dateCanvassed`), `resultCodeId` or `responses` but not both,
  response types `ActivistCode`, `SurveyResponse`, `VolunteerActivity`.
- The standard error envelope `{"errors": [{"code", "text"}]}`.
- `POST /exportJobs` and `GET /exportJobs/{id}` exist (asynchronous, webhook on completion); not
  used here.

## From the community spec (NickCrews/van-openapi), checked against the sandbox

- `GET /people` needs at least one search parameter; text matches are case-insensitive prefixes;
  city, state and ZIP match the Voting address only; phone search only matches bare 10 digits.
- Person collections (`phones`, `emails`, `addresses`) are `null` unless named in `$expand`, and
  the full list of 23 `$expand` values for `GET /people/{vanId}`.
- Person phone, email and address shapes, including `smsOptInStatus` and `subscriptionStatus`.
- A 429 carries the wait only in the error text, with code `"429"` and no `Retry-After`.
- Some malformed paths get HTML pages instead of JSON, so the client checks the content type.
- No aggregate or count-by-attribute endpoint exists.

## Modeled, not documented

- The response of `GET /people/{vanId}/canvassResponses`. The docs mention it (it returns the
  `canvassResponseId` needed for PATCH) but don't show it. The fixture mirrors the POST body
  (`canvassContext`, `resultCodeId`, `responses`) plus `canvassResponseId`.
  `contact_history_summary` reads only those fields.
- Status names beyond the documented examples (`No Show`) and the fake data itself.
