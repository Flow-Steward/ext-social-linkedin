# LinkedIn Publisher for Flow Steward

Publish prepared text, image posts and article links to LinkedIn from workflows, using a member profile or company Page.

Publish public text posts, posts with one JPEG or PNG image, and link cards for
externally hosted articles. Four workflow actions validate content, upload an
authorized artifact, publish a post and inspect connection status. Inputs can
come from a webhook, CMS, release workflow or an upstream content-preparation
step; the extension does not require WordPress or a particular AI provider.

This is an independent integration, not a LinkedIn product or endorsement.
It requires `extension_host_v2`, extension-host API 1.9.0 or later for saving
the Page author, and public SDK 0.4.0 or later.
Host compatibility was checked on Core 29bbe748; standalone CI tests are mocked.
No live OAuth, Page authorization or LinkedIn publication has been verified.

The package uses the publisher’s neutral Flow Steward mark, not LinkedIn’s
logo. Official LinkedIn artwork is an optional, separately reviewed placement;
see [BRANDING.md](BRANDING.md).

## Setup

1. Install this package using the existing extension installer. It requires the
   `extension_host_v2` host and its public SDK 0.4.0 or later.
2. In project extension setup, create OAuth app configurations with your own
   LinkedIn developer app's Client ID and Client Secret. Register the exact HTTPS
   redirect URL shown by Flow Steward in LinkedIn Developer Portal.
3. For a member connection, enable **Share on LinkedIn** and **Sign In with LinkedIn
   using OpenID Connect**. Consent to `openid profile w_member_social`. The member
   identity is read from LinkedIn UserInfo and stored by the host.
4. For a Page connection, use an app approved for **Community Management** with
   `w_organization_social`. Authorize a member with an eligible Page role. Open
   **Page author**, select the connection and save `urn:li:organization:<numeric ID>`.
   Create another connection to publish as a different Page. LinkedIn checks
   Page-role and API-product authorization when publishing.
5. Bind the selected project connection explicitly to each Extension tools step.
   Input cannot override the publishing author. To publish as both authors, use
   two separately bound steps and the ordinary workflow error policy. A failure
   does not remove another successful publication.

Use separate LinkedIn developer apps when connecting both author types: LinkedIn
documents that authorizing a different scope set can invalidate earlier tokens
for the same app/member, and these two connection types request different scopes.
Never share or combine grants between project connections. Client secrets
and tokens remain in the host's secret store. Programmatic refresh is not assumed:
if credentials expire or are revoked, reconnect through the standard OAuth UI.
The confidential-server flow uses the existing host's `pkce: none` declaration,
state checks and client-secret-post token exchange. No custom login flow is added.

## Operations

| Operation | Input | Result |
| --- | --- | --- |
| `validate_post` | `post` object, no connection | `valid`, `violations`, local `limits`; no network calls |
| `upload_image` | bound `connection_ref`, `artifact_handle` | `image_urn`, `author_urn`, `status: uploaded` |
| `publish_post` | bound `connection_ref`, prepared `post` | `post_urn`, `post_url`, `author_urn`, `api_version`, `status: accepted` |
| `test_connection` | bound `connection_ref` | redacted author/type/authorization; Page access explicitly unverified |

Posts have a closed input shape:

```json
{"kind":"text","text":"We shipped an update."}
```

```json
{"kind":"image","text":"Our new release","image_urn":"urn:li:image:FROM_UPLOAD","alt_text":"Release screenshot"}
```

```json
{"kind":"article","text":"What changed in our release","source_url":"https://example.org/releases/1","title":"Release notes","description":"Features and fixes","thumbnail_image_urn":"urn:li:image:FROM_UPLOAD"}
```

Description, thumbnail, and alt text are optional. An article is a link card,
not a native long-form LinkedIn article. The extension does not fetch article
URLs or rely on provider scraping. Text is escaped for LinkedIn `little` syntax;
it is never silently truncated, rewritten or rendered as Markdown/HTML.

Images enter only through authorized host artifact handles. Download a source
file with existing generic workflow tooling if needed, then map that artifact
into `upload_image` and map its returned URN into `publish_post` using the same
connection. No file paths, arbitrary upload URLs or binary JSON are accepted.
Images are checked for structure, format, MIME, dimensions and bounded size;
the extension does not decode/re-encode, resize or crop them. LinkedIn processes
the uploaded image; `uploaded` does not mean processing is finished.

## Generic webhook workflow

Create a workflow using existing builder input/trigger controls. Accept a `post`
object and map it to `publish_post.post`, with the connection bound in the step.
An optional upstream agent or template can prepare the object. There is no CMS
or AI-provider dependency. The examples directory contains the source-agnostic
prepared-content and artifact-upload patterns.
They are bundled as disabled, unpublished workflow artifacts (`publish: false`)
to satisfy the existing distribution contract. They do not authorize a live
project or select credentials; bind connections before explicitly enabling a copy.

Configure authentication on the existing workflow webhook. Enable its existing
header idempotency mode. Send a stable `Idempotency-Key` for retries of the same
publication intent; a different key represents a new intent. Example body:

```json
{"post":{"kind":"article","text":"New release","source_url":"https://example.org/release","title":"Release"}}
```

The webhook HTTP response acknowledges workflow admission, normally with a job
identifier. Read the publication receipt from existing job results; do not expect
the webhook response to wait for LinkedIn. Repeated delivery is deduplicated only
within the host's retention window, and step retries/restarts use its existing
effect ledger. This does not promise exactly-once posting across unrelated runs.

## Limits and failure behavior

Local policy caps: text 3,000; title 200; description 256; alt text 120 UTF-16 code
units (an emoji may count as two). URL 2,048 UTF-8 bytes; serialized provider JSON
64 KiB; image 8 MiB. Only JPEG and PNG, positive dimensions and fewer than
36,152,320 pixels. The pixel bound is documented by LinkedIn; other caps are
conservative extension policy, not claims of LinkedIn's exact maxima. The
`validate_post` result exposes these local limits. Invalid content fails before
provider writes, allowing a workflow preparation step to correct it.

API version is pinned to `202610`. There are no automatic retries or background
status polls. A 429 may return safe `retry_after_seconds`; use existing workflow
Wait/error handling. A timeout, 5xx, broken response or malformed create receipt
after a write is an **unknown outcome**, not permission to try creating again.
Resolve it using the existing host effect-resolution controls after checking
LinkedIn. No scraping/listing API or compensating deletion is used.

`accepted` means LinkedIn returned HTTP 201 with a valid post ID. It does not
guarantee the post is already visible or its image processed. A member's write
scope cannot query the current Images API status endpoint. Unused uploaded media
may remain at LinkedIn if a later publish fails. Test mode suppresses uploads and
posts and must not fabricate provider IDs or publication URLs.

Comments, video, documents, multiple images, newsletters, editing/deletion,
analytics, native articles, private/audience-targeted posts and inbound LinkedIn
events are outside this release.

## Development and acceptance

Runtime uses the host-injected public SDK and Python standard library, with no
additional runtime dependency. The bundled SDK wheel is for offline dev tests.
Use the Core checkout's supported Docker gate, never host pytest:

```sh
./core/entrypoints/cli/scripts/lint.sh --checks extension-static,extension-tests --extension-root /absolute/path/to/flowsteward_linkedin
```

If the installed stack's compose.env selects Compact, select the existing
canonical tooling Compose configuration for the command only; do not edit Core
or the installed stack to make this extension pass. Keep evidence tied to the
extension's clean source commit. Mocked verification does not prove live OAuth,
approved Page access or publication. Those need credentials and an explicitly
authorized live test. No live project is installed/configured by these tests.

The pinned CI release recipe packages tracked committed source under the
canonical `flowsteward_linkedin/` folder. It excludes `.git`, `.github`, developer
wheels, caches and local state. Optional LinkedIn artwork is retained separately in the owner’s preparation
evidence, outside this source and the runtime ZIP. Validate the unpacked archive
and run its local health check before handoff; preserve the exact source SHA and
preview checksum. The published asset must be produced by the CI recipe, not
hand-uploaded from a working directory.

## Catalog metadata and release preparation

The manifest keeps short copy in `description` and `listing.summary` and the
full catalog text in `listing.description`; `listing.setup_summary` is the
separate setup introduction. The descriptor mirrors these fields. The canonical
category is `marketing`: this extension publishes content; it does not implement
LinkedIn messaging, recruiting or CRM operations. The Marketplace machine-auth
metadata mirrors the existing `extension:invoke` and `artifact:read` scopes; it
does not create a service account, issue credentials or grant new permissions.

Since version 1.0.1, `icon.png` uses an original blue professional-profile and
connections illustration made for this integration. The same custom artwork
identifies it in the platform catalog and on flowsteward.org. Its generation,
resizing, final hash and visual review are recorded in `BRANDING.md`. It is not
the official LinkedIn logo and does not imply LinkedIn endorsement.

For release maintainers, the repository target is
`Flow-Steward/ext-social-linkedin`; verify that it exists before publishing.
`.github/release-config.json` selects the standalone checks for the portable
package, including Page author binding.
The workflow callers pin the maintained catalog release tooling to an immutable
commit. The CI build has read-only repository permissions; only the release
publisher receives Contents write permission. Repository creation, source and
tag pushes, publication and catalog submission require the release owner’s
coordinated approval. A local preview ZIP is preparation evidence, not a published
CI asset.

The public catalog and flowsteward.org consume the same generated catalog
fields. The shared catalog must be updated by its owner from a separate
registration/data handoff; do not edit generated `index.json` or release records.

## References

- https://learn.microsoft.com/en-us/linkedin/marketing/community-management/shares/posts-api
- https://learn.microsoft.com/en-us/linkedin/marketing/community-management/shares/images-api
- https://learn.microsoft.com/en-us/linkedin/marketing/community-management/shares/little-text-format
- https://learn.microsoft.com/en-us/linkedin/shared/authentication/authorization-code-flow
- https://learn.microsoft.com/en-us/linkedin/consumer/integrations/self-serve/sign-in-with-linkedin-v2

## Support

Open the extension’s **Support** tab to report a problem or request a feature in
the [public issue tracker](https://github.com/Flow-Steward/ext-social-linkedin/issues).
Include the extension and Flow Steward versions, expected and actual behavior,
the exact error, workflow or action, and the time. Review diagnostics before
sharing; never post secrets or customer data in public issues.
