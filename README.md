# job-seek: Find best job matches for you

You need:

1. **Job sources**: URLs where job offers are published. Like job boards or Slack channels.
2. **Your portfolio**: A text describing your skills, experience expectations and dislikes.
3. **Work scenarios**: A set of definitions and rules what you are looking for.
4. **GenAI LLM API key**: Credentials (API_KEY) for an Open AI compatible chat completions API. Either a local LLM (ollama, llama.cpp, LM-Studio, ...) or one of the public providers.

You get:

- **Filtered list of concrete jobs** matching your scenarios.
- **Easy to read job summary**: See all relevant facts at once.
- **Prioritized ranking of opportunities** evaluated against your skills, career stage and preferences.

Daily workflow:

1. Fetch newly published jobs
2. Check the findings. Leave feedback to finetune scenario specifications.
3. Approve or reject jobs. Leave notes to improve your profile.
4. Apply (not part of the app yet)
5. Review generated improvement proposals for work scenario filters and your profile

## Setup

To run the job-seek webserver you can start it as a container or directly from Python.

### As Container

You still might want to get the code and setup dependencies if you want to use the slack authentication feature. See below.

Once the container started, visit **/setup** in the running container's web UI to pick your LLM provider, enter an API key if needed, load or type a model, and save — the app writes directly to the mounted `data/config.toml`. (You can also hand-edit `data/config.toml` directly if you prefer.)

#### Podman

```shell
mkdir --parents data
podman run --detach --publish 8000:8000 \
  --userns=keep-id:uid=1000,gid=1000 \
  --volume "$(pwd)/data:/app/data" \
  registry.gitlab.com/rmmsr/job-seek:latest
```

> **Note**: `--userns=keep-id` maps the container's uid 1000 back to your own host uid, so files Podman writes into `data/` (the config, db, browser profile) stay owned by you rather than an arbitrary container uid.

#### Docker

```shell
mkdir --parents data
docker run --detach --publish 8000:8000 \
  --add-host=host.containers.internal:host-gateway \
  --volume "$(pwd)/data:/app/data" \
  registry.gitlab.com/rmmsr/job-seek:latest
```

> **Note**: We explicitly set `host.containers.internal` to match Podman's convention to reference the host serving the container.

> **Note**: If your user does not have id 1000 (check with `id`), you need to change ownership of `data`:
> ```shell
> chown --recursive 1000:1000 data
> ```

Open [http://localhost:8000](http://localhost:8000) in your browser.

### As local server

Requirements

- Python 3.12+
- [uv](https://docs.astral.sh/uv/)

Clone the code.

```shell
git clone https://gitlab.com/RmMsr/job-seek.git
cd job-seek

# Install dependencies
uv sync

# Install the Playwright browser (needed for auth-gated sources like Slack)
uv run playwright install chromium

cp config-template.toml config.toml

uv run uvicorn app.main:app --port 8000
```

Open [http://localhost:8000](http://localhost:8000) in your browser. If the LLM provider isn't configured yet, a banner on the home page will link you to **/setup** — pick a hosted provider (OpenAI, Groq, OpenRouter, etc.) or Custom for a local/self-hosted endpoint (Ollama, llama.cpp, vLLM, ...), enter your API key if needed, and save. The app writes `config.toml` for you.

## Source Authentication (Slack)

Any Slack job source needs credentials to your slack account. That works not by asking for username and password, but by extracting a user cookie (`xoxo-*`) that allows the app to gain the same access you have.

> **Warning**: The access cookie grants access to **all your workspaces**! Unfortunately there is no other option for normal slack users.

Inside the code folder run:

```shell
uv run python -m app.cli.slack_login
```

Or follow the instructions in the webapp to get the cookie by hand.
