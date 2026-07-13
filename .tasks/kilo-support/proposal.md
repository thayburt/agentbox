# Objective

Implement support for Kilocode's CLI into agentbox

# About

## Current Behaviour

Currently Agentbox has no support for the Kilocode cli

## Desired Behaviour

I want to be able to run Kilo CLI inside of Agentbox's containers.

Ideally Kilo CLI in the container will use the following configs: 

1. User's Global Configs
2. Agentbox Specific configs
3. Project/repo specific configs


# Requirements

To achieve this agentbox will have to do the following:

1. Bind mount the user's global configs into the container
    - For security keep the configs readonly in the container
    - Detect where the harness (on the host system) loads it's config
2. Support an agentbox specific config (via `<project>/.agentbox/<harness>/<config>.<ext>` eg for kilocode: `<project>/.agentbox/kilo/kilo.jsonc`)
    - For security mount the config as readonly
    - Use the host repository's config copy (let the agent modify the sandbox's copy, but don't inject that copy in the container)
    - Mount the config in a seperate directory such as `/agentbox/config/<config file>[.<ext>]` in the container
    - For Kilocode inject the config via the `KILO_CONFIG`
3. Still allow the harness to consume the project's local configs (eg for kilocode: let kilo use the repository's own `.kilo` directory)
4. Auto generate a basic config in the `init` command for future editing
