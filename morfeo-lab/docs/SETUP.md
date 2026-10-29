# Setup and troubleshooting

## Requirements

| | |
|---|---|
| Python | 3.10 or newer. Dependencies: PyYAML, Jinja2, pandas, NumPy (`requirements.txt`). |
| Docker | Docker Desktop (Windows, macOS) or Docker Engine (Linux), running. |
| kubectl | any recent version. |
| kind | 0.20 or newer, only when the lab creates the cluster (`cluster.provider: kind`). |
| Machine | 8 GB of RAM at least (16 GB recommended), 15 GB of free disk, 4 CPUs or more. Docker must be allowed **6 GB or more**. |
| Internet | the first `up` downloads about 2 GB (Kubernetes node image, JMeter and its plugins, Prometheus, Grafana, PostgreSQL). After that, nothing is downloaded during a test. |

## Windows (PowerShell)

```powershell
winget install Python.Python.3.12
winget install Docker.DockerDesktop
winget install Kubernetes.kubectl
winget install Kubernetes.kind
# open a new PowerShell so the PATH is refreshed, and start Docker Desktop

cd C:\path\to\morfeo-lab
py -3 -m pip install -r requirements.txt
.\morfeo-lab.cmd doctor
.\morfeo-lab.cmd up
```

`morfeo-lab.cmd` works from any folder and does not depend on the PowerShell execution policy. Keep the lab in a path
without special characters.

### Memory (WSL 2)

Docker Desktop with the WSL 2 backend gets its memory from WSL, which by default takes about half of the machine. If
`doctor` says Docker has less than 6 GB:

```powershell
notepad "$env:USERPROFILE\.wslconfig"
```

Add (or change) under `[wsl2]`:

```ini
[wsl2]
memory=8GB
```

Save, then run `wsl --shutdown` and start Docker Desktop again. With the Hyper-V backend, use Docker Desktop →
Settings → Resources → Memory.

## Linux and macOS

```sh
python3 -m pip install -r requirements.txt
./morfeo-lab doctor
./morfeo-lab up
```

On Linux, Docker needs to be reachable by your user (`sudo usermod -aG docker $USER`, then log in again). `kind` and
`kubectl` are single binaries: <https://kind.sigs.k8s.io>, <https://kubernetes.io/docs/tasks/tools/>.

## Using a cluster you already have

In `config/lab.yaml`:

```yaml
cluster:
  provider: existing
  context: my-cluster        # kubectl config get-contexts
  pools: false               # single node; or label nodes: kubectl label node <n> morfeo.io/pool=app|generators|system
  image_loader: docker       # Docker Desktop's Kubernetes; minikube, k3s or registry for the others
```

`up` creates the lab namespaces and the monitoring stack in it; `down --destroy` removes them. For a real cluster, give
the application and the generators separate nodes (`pools: true` and the labels above), so measurements are not
disturbed by neighbours.

## Troubleshooting

`morfeo-lab doctor` finds most of these. Use `-v` on any command to print the external commands it runs.

| Symptom | Cause and fix |
|---|---|
| `docker: installed but not running` | Start Docker Desktop and wait for *Engine running* (Linux: `sudo systemctl start docker`). |
| `Docker has 3.8 GB and the lab needs at least 6 GB` | Raise the Docker memory (WSL section above). |
| `Port 3000 (grafana) is in use` | Another program uses it. Change `cluster.ports.grafana` in `config/lab.yaml`, or close that program. |
| `certificate verify failed` while building images | The network inspects HTTPS with its own certificate. Export the company certificate as PEM and set `MORFEO_BUILD_CA` (`$env:MORFEO_BUILD_CA="C:\certs\company.pem"`, or `export MORFEO_BUILD_CA=/path/company.pem`), then run `up` again. |
| `registry-1.docker.io` cannot be resolved | No internet, blocked DNS or a VPN. The first `up` needs Docker Hub, Maven Central and GitHub. |
| `The load generator image is not in the cluster` | Run `morfeo-lab up`: it loads again what the cluster lost (for example after Docker Desktop was reset). |
| Pods stay `Pending` | Not enough memory in Docker, or no node has the `morfeo.io/pool` label (existing clusters). `morfeo-lab status` shows the pods. |
| The application never becomes ready | `deploy` prints the last lines of its log and the reason (image pull, memory, crash loop, failing health check). Check `health.path` in the application file and that the JAR needs only PostgreSQL. |
| `Some lab files have Windows line endings (CRLF)` | The folder was checked out with `core.autocrlf=true`. Unzip it again or `git config core.autocrlf false` and check out again; shell scripts with CRLF fail inside the Linux containers. |
| `'x.jmx' needs the JMeter plugin 'jpgc-…'` | Add the id to `generators.jmeter_plugins` and run `up`. |
| `up` stops at *Creating the cluster* and the node logs show `runc create failed` | The host kernel cannot run Kubernetes pods inside kind (seen on Linux hosts with cgroup v1 and in some virtual machines). Use a host with cgroup v2, or point the lab to a cluster you already have (above). `kind export logs` shows the node logs. |

## Uninstall

`morfeo-lab down --destroy` removes the cluster (or the lab namespaces). The images stay in Docker (`docker image prune`
removes them). `results/` is yours.
