# Contributing to AnyList for Home Assistant

Thanks for your interest in improving AnyList for Home Assistant.

This repository contains one Home Assistant custom integration:

- `custom_components/anylist`: the AnyList integration, local AnyList client, config flow, entities, actions, diagnostics, and translations.

Contributions are welcome, including bug reports, documentation improvements, compatibility fixes, security hardening, and focused feature ideas.

## Before You Start

Please open an issue before starting large or risky changes. This helps avoid duplicated work and gives maintainers a chance to discuss the approach first.

Small fixes, documentation updates, and clearly scoped bug fixes can usually go straight to a pull request.

## Reporting Bugs

When reporting a bug, please include:

- The version of AnyList for Home Assistant you are using.
- Your Home Assistant version.
- Whether you installed through HACS, manually, or from the development branch.
- Clear steps to reproduce the issue.
- Relevant Home Assistant logs with sensitive information removed.
- What you expected to happen.
- What actually happened.

Please remove AnyList credentials, tokens, private URLs, personal paths, shopping list contents, recipes, and private Home Assistant configuration before sharing logs or screenshots.

## Suggesting Features

Feature requests are welcome. Please describe:

- The problem you want to solve.
- The workflow you expect to use in Home Assistant.
- Whether the change affects shopping lists, recipes, meal planning, actions, entities, or setup.
- Any security or privacy concerns the feature may introduce.

Because AnyList does not publish a public API, features that expand the local client should include clear testing notes and a conservative failure mode.

## Development Setup

Clone the repository:

```bash
git clone https://github.com/moryoav/ha-anylist.git
cd ha-anylist
```

The repository layout is:

```text
custom_components/anylist/  Home Assistant custom integration
tests/                      Lightweight local tests
.github/workflows/          HACS and Hassfest validation workflows
```

For local Home Assistant testing, copy the integration into:

```text
/config/custom_components/anylist
```

Restart Home Assistant and add the integration from **Settings** -> **Devices & services** -> **Add integration** -> **AnyList**.

## Pull Request Guidelines

Please keep pull requests focused. A good pull request should:

- Explain what changed and why.
- Mention any related issue.
- Keep unrelated formatting or refactoring out of the change.
- Update documentation when behavior, installation, options, actions, or entities change.
- Include screenshots when changing Home Assistant UI text or flow behavior.
- Avoid committing secrets, credentials, private URLs, logs, shopping list contents, recipes, or personal Home Assistant configuration.

If you change the integration version, update `custom_components/anylist/manifest.json` and release notes consistently.

## Testing

Before opening a pull request, test the parts you changed as much as practical.

For integration changes, verify that Home Assistant can:

- Load the `anylist` integration.
- Complete the config flow.
- Create the expected todo entities and optional diagnostic sensor.
- Call the relevant actions.
- Reload or restart without errors.

For local validation, run:

```bash
python -m compileall custom_components tests
python -m pytest -q
```

For documentation-only changes, please check that links, paths, and examples are accurate.

This repository may not have full automated coverage for every Home Assistant path yet, so clear manual test notes in the pull request are helpful.

## Security Notes

Please be especially careful with changes involving:

- AnyList credentials or authentication failures.
- Diagnostics and redaction.
- Service action responses.
- Shopping list, recipe, or meal planning data.
- AnyList API request construction, parsing, token refresh, or logging.

Do not include real credentials, API tokens, private logs, shopping list contents, recipes, or personal Home Assistant configuration in issues or pull requests.

If you believe you found a security vulnerability, please do not open a public issue with exploit details. Use the project's security reporting process if available, or contact the maintainer privately.

## Documentation

Please update documentation when changing user-facing behavior. Depending on the change, this may include:

- `README.md`
- `custom_components/anylist/services.yaml`
- `custom_components/anylist/strings.json`
- `custom_components/anylist/quality_scale.yaml`

Use plain, direct language and include Home Assistant examples where they make the workflow easier to understand.

## Releases

Stable users should use the default repository URL:

```text
https://github.com/moryoav/ha-anylist
```

Published GitHub releases are preferred for HACS users.

## Code of Conduct

Please be respectful, constructive, and patient. This project is intended to help Home Assistant users connect their own AnyList accounts safely and reliably.
