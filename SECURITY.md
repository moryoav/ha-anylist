# Security Policy

AnyList for Home Assistant stores AnyList credentials and can read shopping list, recipe, and meal planning data from an AnyList account. Please treat security and privacy issues with care.

## Supported Versions

Security fixes are intended for the latest published release and the current `main` branch.

Older releases are not actively supported unless a maintainer says otherwise in a specific issue or release note.

## Reporting a Vulnerability

Please do not open a public issue with exploit details, working proof-of-concept code, private logs, credentials, tokens, private URLs, shopping list contents, recipes, or personal Home Assistant configuration.

If GitHub private vulnerability reporting is available for this repository, use the **Report a vulnerability** button on the Security tab.

If private vulnerability reporting is not available, open a minimal public issue that says you have a security concern and asks the maintainer to arrange private disclosure. Do not include sensitive details in that issue.

## What to Include

When reporting a vulnerability privately, include as much of the following as you can safely share:

- A clear description of the issue.
- The affected version or commit.
- Steps to reproduce in a safe test environment.
- The expected impact.
- Any relevant logs with secrets and private data removed.
- Suggested mitigations, if you know them.

## Security-Sensitive Areas

Please use extra care when changing or reviewing:

- AnyList credential handling and reauthentication.
- Token refresh behavior in the local AnyList client.
- Diagnostics and data redaction.
- Service action responses that may include recipe or list content.
- AnyList API request construction and protobuf parsing.
- Logging around authentication, requests, recipes, shopping lists, and meal planning URLs.

## Responsible Testing

Test security reports and fixes only in an environment you own or have permission to use. Do not attempt to access, modify, or disclose another person's Home Assistant instance, AnyList account, credentials, logs, lists, recipes, or devices.

## Public Disclosure

Please give the maintainer reasonable time to investigate and fix confirmed vulnerabilities before publishing details publicly. Coordinated disclosure helps protect users while a fix is prepared.
