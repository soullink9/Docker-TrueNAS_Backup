# Development

## Layout

```
app/
  scheduler.py             container entry point: runs a cycle on BACKUP_SCHEDULE
  backup.py                one backup cycle; also `docker exec ... backup` by hand
  settings.py              environment variables, checked at start
  cronspec.py              the cron-syntax schedule parser
  power.py                 power on/off: IPMI, Wake-on-LAN, the backup box's API
  truenas_api.py           TrueNAS JSON-RPC 2.0 WebSocket client
  notify.py                Home Assistant webhook
  healthcheck.py           Docker healthcheck
homeassistant/             the Home Assistant blueprint (imported by URL)
tests/                     unit tests (standard library unittest, no TrueNAS needed)
docs/assets/icon.svg       repo icon (source); icon.png is the 512 px render
.gitea/workflows/build.yml build, test, publish, release on Gitea
.github/workflows/build.yml the same on GitHub (ghcr.io), for a push mirror
Dockerfile
VERSION                    the version, and the only place it is written
```

The only Python dependency is `websocket-client` (see `requirements.txt`); `ipmitool` comes from Alpine's packages. Everything else is the Python standard library.

## Tests

```
python -m unittest discover -s tests -v
```

The Dockerfile has a `test` stage that runs the same tests inside the real image. The build workflow runs it before building, so a failing test stops the build:

```
docker build --target test .
```

## Versions and releases

The version lives only in `VERSION` (`MAJOR.MINOR.PATCH`). The workflow passes it to the image label, and the scheduler logs it at start.

On every push to `main`, the workflow on Gitea:

1. runs the tests,
2. builds the image and pushes `:latest` and `:sha-<commit>`,
3. if `vX.Y.Z` (from `VERSION`) has no release yet, also pushes `:X.Y.Z` and creates the release.

So to release, bump `VERSION` in the same commit as the change. A version tag is pushed only once and never changes afterwards.

Pull requests are built and tested but nothing is published.

## Publishing on GitHub too

If the Gitea repo is push-mirrored to GitHub, `.github/workflows/build.yml` does the same there: it tests, pushes `ghcr.io/<owner>/docker-truenas_backup` (`:latest`, `:sha-<commit>`, `:X.Y.Z`) and creates the GitHub release for a new `VERSION`. It uses the built-in `GITHUB_TOKEN`, so there is nothing to configure. The first push creates the package as private: make it public once under the package's settings on GitHub, since `compose.example.yaml` pulls from there by default.

The two workflows never both run in one place: Gitea ignores `.github/workflows` when `.gitea/workflows` exists, GitHub never reads `.gitea/`, and the GitHub job also checks it is running on github.com.

## Workflow settings in Gitea

| Kind | Name | Value |
|------|------|-------|
| Secret | `REGISTRY_TOKEN` | A Gitea access token with **write:package** and **write:repository**. Used to push the image and create releases. |
| Variable | `REGISTRY_HOST` | Optional. The registry name the Docker daemon pushes to. Default: the host of the server URL (e.g. `gitea-server:3000`). A plain-http name must be in the daemon's `--insecure-registry` list. |
| Variable | `GITEA_INTERNAL_URL` | Optional. How the runner reaches Gitea's API, e.g. `http://gitea-server:3000`. Default: the server URL. |
| Variable | `DOCKER_HOST` | Optional. The Docker daemon to use. Default: found automatically (the mounted socket, then port 2375 on `localhost`, `docker` and `dind`). |
| Variable | `DOCKER_CERT_PATH` | Optional. Where the client certificates are when `DOCKER_HOST` is a TLS port (2376). Default: `/certs/client`. |

Gitea serves an image under every name that reaches it: the path after the host (`<owner>/<repo>`) is what identifies it. So the workflow can push to the internal name while your Docker host pulls from another, such as `gitea.example.com/<owner>/docker-truenas_backup`.

The job image only needs `curl`, `node` and `git`; the default `node:22-bookworm` has them. The workflow downloads Docker's static CLI and the buildx plugin itself (versions pinned at the top of the workflow).

## The runner and docker:dind

Job containers are created by the dind daemon but can't talk to it unless they get a way in. The workflow's job mounts dind's own socket:

```yaml
container:
  image: node:22-bookworm
  volumes:
    - /var/run/docker.sock:/var/run/docker.sock
```

The runner only allows mounts listed in its `config.yaml`, so that needs:

```yaml
container:
  valid_volumes:
    - /var/run/docker.sock
```

The workflow then finds the socket by itself; no `DOCKER_HOST` variable is needed.

Alternatively, with dind's TLS port (2376): mount the client certificates instead (`/certs/client:/certs/client:ro`, with `/certs/client` in `valid_volumes`) and set the `DOCKER_HOST` variable to `tcp://docker:2376`.

Either way the job gets full control of the dind daemon, which is what building images needs; dind keeps that away from the TrueNAS host's own Docker.
