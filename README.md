# Claude Code → Microsoft Foundry GPT-5.6-sol bridge

This is a local-only, standard-library Python proxy. It translates Claude Code's
Anthropic Messages requests to Microsoft Foundry's OpenAI v1 Responses API.

## One-time local setup

```zsh
cd ~/claude-gpt56-proxy
cp .env.example .env
chmod 600 .env
```

Edit `.env` locally. Set `FOUNDRY_ENDPOINT` to `https://RESOURCE.openai.azure.com`
(or a full `/openai/v1` base URL), `FOUNDRY_API_KEY`, and the *deployment name*
in `FOUNDRY_DEPLOYMENT`. Do not use a `/models` endpoint.

## Run Claude Code through the bridge

```zsh
cd ~/claude-gpt56-proxy
./start.sh
```

The launcher starts the proxy, checks `/health`, gives Claude Code a local-only
gateway token, and then opens Claude Code. It does not change your global Claude
Code settings. Exit Claude Code to stop the proxy.

After Foundry networking allows this Mac, run `./test.sh` to exercise health,
text, streaming, and function-tool conversion before opening Claude Code.

## What is supported

System prompts, text and image input, full request-supplied conversation history,
non-streaming and streaming text, function definitions, function calls, function
results, multi-turn tool use, basic usage fields, request IDs, and error logs.

## Limits

This is a protocol adapter rather than an Anthropic model. Claude-specific
thinking blocks, prompt caching semantics, server tools, and vendor-specific
beta features are not emulated. Tool argument streaming is supported when the
Foundry event stream includes function-call argument deltas. `count_tokens` is a
local character-based estimate, not Foundry tokenizer accounting.
